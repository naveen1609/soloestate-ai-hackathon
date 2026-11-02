"""policy_fixture exact-ownership cleanup regression.

Committed fictional fixtures with exact-ID cleanup: simulates the browser
spec's owned operations plus unrelated sentinel records created inside the
fixture window, runs the real fixture cleanup, and proves owned rows are
removed while sentinels and shared-setting interventions survive.
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'frontend' / 'e2e'))
import policy_fixture

from app import booking_requests, crm_schemas as s
from app import identity_links
from app import policies as pol
from app import resources
from app.config import Settings
from app.crm import CRM, scalar
from app.database import connect

pytestmark = pytest.mark.d1_database

IST = ZoneInfo('Asia/Kolkata')


@pytest.fixture
def iso(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    yield CRM()


def _clean_wednesday(conn):
    holidays = set(conn.execute(
        'SELECT holiday_dates FROM soloestate.business_policies WHERE id=%s', ('default',)).fetchone()[0] or [])
    base = datetime.now(IST).date() + timedelta(days=10)
    day = base + timedelta(days=(3 - base.isoweekday()) % 7)
    while day in holidays:
        day += timedelta(days=7)
    start = datetime(day.year, day.month, day.day, 11, 0, tzinfo=IST)
    return start, start + timedelta(minutes=30)


def _scrub_owned(conn, data, reasons):
    """Exact-ID owned-row removal for test teardown (never broad predicates)."""
    leads = [data['lead_id'], data['isolated_lead_id']]
    bookings = [r[0] for r in conn.execute(
        'SELECT id FROM soloestate.bookings WHERE lead_id=ANY(%s::uuid[])', (leads,)).fetchall()]
    runs = [r[0] for r in conn.execute(
        'SELECT DISTINCT run_id FROM soloestate.approvals WHERE lead_id=ANY(%s::uuid[])', (leads,)).fetchall()]
    conn.execute('DELETE FROM soloestate.booking_requests WHERE lead_id=ANY(%s::uuid[])', (leads,))
    if bookings:
        conn.execute('DELETE FROM soloestate.bookings WHERE id=ANY(%s::uuid[]) AND replaces_booking_id IS NOT NULL',
                     (bookings,))
        conn.execute('DELETE FROM soloestate.bookings WHERE id=ANY(%s::uuid[])', (bookings,))
    if runs:
        conn.execute('DELETE FROM soloestate.approval_decisions WHERE approval_id IN '
                     '(SELECT id FROM soloestate.approvals WHERE run_id=ANY(%s::uuid[]))', (runs,))
        conn.execute('DELETE FROM soloestate.approvals WHERE run_id=ANY(%s::uuid[])', (runs,))
        conn.execute('DELETE FROM soloestate.run_steps WHERE run_id=ANY(%s::uuid[])', (runs,))
        conn.execute('DELETE FROM soloestate.runs WHERE id=ANY(%s::uuid[])', (runs,))
    conn.execute('DELETE FROM soloestate.conversation_messages WHERE conversation_id IN '
                 '(SELECT id FROM soloestate.conversations WHERE lead_id=ANY(%s::uuid[]))', (leads,))
    conn.execute('DELETE FROM soloestate.conversations WHERE lead_id=ANY(%s::uuid[])', (leads,))
    conn.execute('DELETE FROM soloestate.identity_links WHERE lead_id=ANY(%s::uuid[])', (leads,))
    conn.execute('DELETE FROM soloestate.suppression_list WHERE destination=%s', (data['phone'],))
    conn.execute('DELETE FROM soloestate.lead_activities WHERE lead_id=ANY(%s::uuid[])', (leads,))
    ids = leads + [data['property_id']] + [str(i) for i in bookings + runs] + data.get('request_ids', []) \
        + data.get('resource_ids', []) + data.get('policy_ids', []) + [str(i) for i in data.get('slot_ids', [])] \
        + [data['link_id']] + ([data['active_link_id']] if data.get('active_link_id') else [])
    conn.execute('DELETE FROM soloestate.crm_operations WHERE entity_id=ANY(%s::uuid[])', (ids,))
    if reasons:
        conn.execute("DELETE FROM soloestate.crm_operations WHERE response->>'reason' = ANY(%s)", (reasons,))
    conn.execute('DELETE FROM soloestate.visit_slots WHERE id=ANY(%s::uuid[])',
                 ([str(i) for i in data.get('slot_ids', [])],)) if data.get('slot_ids') else None
    conn.execute('DELETE FROM soloestate.automation_policies WHERE id=ANY(%s::uuid[])',
                 (data.get('policy_ids', []),)) if data.get('policy_ids') else None
    conn.execute('DELETE FROM soloestate.resources WHERE id=ANY(%s::uuid[])',
                 (data.get('resource_ids', []),)) if data.get('resource_ids') else None
    conn.execute('DELETE FROM soloestate.properties WHERE id=%s', (data['property_id'],))
    conn.execute('DELETE FROM soloestate.leads WHERE id=ANY(%s::uuid[])', (leads,))


def test_policy_cleanup_removes_only_test_owned_rows(iso):
    crm = iso
    data = None
    sentinel_phone = None
    sentinel = dict(slots=[], gen_ids=[], leads=[])
    data = policy_fixture.plan()
    assert data['was_stopped'] is False
    key = lambda: data['prefix'] + ':' + str(uuid4())
    sentinel_tag = 'SentinelD1-' + uuid4().hex[:8]
    original_notice = data['notice_hours']
    sentinel_notice = original_notice + 40
    try:
        with connect(Settings()) as conn:
            # --- owned operations mirroring the browser spec ---
            policy = pol.get_policy(crm)['record']
            other = 20 if original_notice != 20 else 24
            pol.update_policy(crm, s.PolicyUpdate(request_key=key(), expected_version=policy['version'],
                                                  notice_hours=other, reason=data['reason_policy_check']))
            policy = pol.get_policy(crm)['record']
            pol.update_policy(crm, s.PolicyUpdate(request_key=key(), expected_version=policy['version'],
                                                  notice_hours=original_notice, reason=data['reason_policy_restore']))
            agent = resources.create(crm, s.ResourceCreate(request_key=key(), name=data['prefix'] + ' agent'))['record']
            data['resource_ids'].append(agent['id'])
            resources.deactivate(crm, agent['id'], s.ResourceDeactivate(
                request_key=key(), expected_version=agent['version'], reason=data['prefix'] + ' churn'))
            gen = pol.generate_slots(crm, s.SlotGeneration(
                request_key=key(), start_date=datetime.fromisoformat(data['gen_start']).date(),
                end_date=datetime.fromisoformat(data['gen_end']).date()))['record']
            assert gen['created'] > 0 and len(gen['slot_ids']) == gen['created']
            data['slot_ids'].extend(gen['slot_ids'])
            auto = pol.create_automation_policy(crm, s.AutomationPolicyCreate(
                request_key=key(), kind='followup_nudge', days_after=2, after_event='no_visit_booked',
                frequency='once', categories=['reminder'], lead_statuses=['new'], max_attempts=1,
                per_lead_cooldown_hours=24, wording='template'))['record']
            data['policy_ids'].append(auto['id'])
            identity_links.suppress(crm, s.SuppressionCreate(
                request_key=key(), channel='whatsapp', destination=data['phone'],
                evidence_ref='Fictional cleanup regression'))
            pol.set_stop(crm, s.ControlsSet(request_key=key(), stopped=True, reason=data['reason_stop_on']))
            lifted = pol.set_stop(crm, s.ControlsSet(
                request_key=key(), stopped=False, reason=data['reason_stop_off']))['record']
            data['stop_lifted_at'] = lifted['updated_at']
            active = identity_links.link(crm, s.IdentityLinkCreate(
                request_key=key(), link_id=data['link_id'], lead_id=data['lead_id'],
                verified_destination=data['phone'],
                reason='Fictional cleanup regression link'))['record']
            data['active_link_id'] = active['id']
            identity_links.revoke(crm, UUID(active['id']), s.IdentityRevoke(
                request_key=key(), expected_version=active['version'],
                reason='Fictional cleanup regression revoke'))
            start, end = _clean_wednesday(conn)
            proposed = booking_requests.propose(crm, s.BookingRequestCreate(
                request_key=key(), lead_id=data['lead_id'], property_id=data['property_id'],
                requested_start=start, requested_end=end))['record']
            assert proposed['status'] == 'approved'
            data['request_ids'].append(proposed['id'])
            committed = booking_requests.commit(crm, proposed['id'], s.BookingCommit(
                request_key=key(), expected_request_hash=proposed['request_hash']))
            assert committed['committed'] is True
            # --- unrelated sentinels created inside the fixture window ---
            sentinel_lead = crm.save('leads', s.LeadCreate(
                request_key=key(), name=sentinel_tag, email=sentinel_tag + '@example.invalid'))['record']
            sentinel['leads'].append(sentinel_lead['id'])
            tue = datetime.fromisoformat(data['tue_start'])
            sentinel_slot = crm.slot(s.SlotCreate(request_key=key(), starts_at=tue, ends_at=tue + timedelta(minutes=30)))['record']
            sentinel['slots'].append(sentinel_slot['id'])
            far = datetime.now(IST).date() + timedelta(days=60)
            sentinel_gen = pol.generate_slots(crm, s.SlotGeneration(
                request_key=key(), start_date=far, end_date=far + timedelta(days=1)))['record']
            assert len(sentinel_gen['slot_ids']) > 0
            sentinel['gen_ids'] = [str(i) for i in sentinel_gen['slot_ids']]
            sentinel['slots'].extend(sentinel['gen_ids'])
            sentinel_phone = '+1555%07d' % (uuid4().int % 10000000)
            identity_links.suppress(crm, s.SuppressionCreate(
                request_key=key(), channel='whatsapp', destination=sentinel_phone,
                evidence_ref=sentinel_tag))
            pol.set_stop(crm, s.ControlsSet(request_key=key(), stopped=True, reason=sentinel_tag + ' on'))
            pol.set_stop(crm, s.ControlsSet(request_key=key(), stopped=False, reason=sentinel_tag + ' off'))
            policy = pol.get_policy(crm)['record']
            pol.update_policy(crm, s.PolicyUpdate(request_key=key(), expected_version=policy['version'],
                                                  notice_hours=sentinel_notice, reason=sentinel_tag + ' notice'))
        result = policy_fixture.cleanup(data)
        assert result['conflicts'] == ['notice_hours changed to %r (expected %r); preserved' % (
            sentinel_notice, original_notice)]
        with connect(Settings()) as conn:
            # owned rows are gone
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.leads WHERE id=ANY(%s::uuid[])',
                                       ([data['lead_id'], data['isolated_lead_id']],)).fetchone()) == 0
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.properties WHERE id=%s',
                                       (data['property_id'],)).fetchone()) == 0
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.visit_slots WHERE id=ANY(%s::uuid[])',
                                       (data['slot_ids'],)).fetchone()) == 0
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.bookings WHERE lead_id=%s',
                                       (data['lead_id'],)).fetchone()) == 0
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.booking_requests WHERE lead_id=%s',
                                       (data['lead_id'],)).fetchone()) == 0
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.identity_links WHERE lead_id=ANY(%s::uuid[])',
                                       ([data['lead_id'], data['isolated_lead_id']],)).fetchone()) == 0
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.suppression_list WHERE destination=%s',
                                       (data['phone'],)).fetchone()) == 0
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.resources WHERE id=ANY(%s::uuid[])',
                                       (data['resource_ids'],)).fetchone()) == 0
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.automation_policies WHERE id=ANY(%s::uuid[])',
                                       (data['policy_ids'],)).fetchone()) == 0
            assert scalar(conn.execute("SELECT count(*) FROM soloestate.crm_operations WHERE response->>'reason' = ANY(%s)",
                                       ([data['reason_policy_check'], data['reason_policy_restore'],
                                         data['reason_stop_on'], data['reason_stop_off']],)).fetchone()) == 0
            assert scalar(conn.execute("SELECT count(*) FROM soloestate.crm_operations WHERE action='suppression:create' "
                                       "AND response->'record'->>'destination' = %s", (data['phone'],)).fetchone()) == 0
            assert scalar(conn.execute("SELECT count(*) FROM soloestate.crm_operations WHERE action='slot:generate' "
                                       "AND response->'record'->'slot_ids' = %s::jsonb",
                                       (json.dumps([str(i) for i in data['slot_ids']]),)).fetchone()) == 0
            # sentinels survive
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.visit_slots WHERE id=ANY(%s::uuid[])',
                                       (sentinel['slots'],)).fetchone()) == len(sentinel['slots'])
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.leads WHERE id=ANY(%s::uuid[])',
                                       (sentinel['leads'],)).fetchone()) == 1
            assert scalar(conn.execute("SELECT count(*) FROM soloestate.crm_operations WHERE response->>'reason' LIKE %s",
                                       (sentinel_tag + '%',)).fetchone()) == 3
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.suppression_list WHERE destination=%s',
                                       (sentinel_phone,)).fetchone()) == 1
            assert scalar(conn.execute('SELECT notice_hours FROM soloestate.business_policies WHERE id=%s',
                                       ('default',)).fetchone()) == sentinel_notice
            assert scalar(conn.execute('SELECT emergency_stop FROM soloestate.automation_controls WHERE id=%s',
                                       ('default',)).fetchone()) is False
    finally:
        with connect(Settings()) as conn:
            conn.execute('UPDATE soloestate.business_policies SET notice_hours=%s WHERE id=%s',
                         (original_notice, 'default'))
            conn.execute("UPDATE soloestate.automation_controls SET emergency_stop=false WHERE id='default'")
            if data is not None:
                _scrub_owned(conn, data, [data['reason_policy_check'], data['reason_policy_restore'],
                                          data['reason_stop_on'], data['reason_stop_off']])
            conn.execute("DELETE FROM soloestate.crm_operations WHERE response->>'reason' LIKE %s",
                         (sentinel_tag + '%',))
            if sentinel['gen_ids']:
                conn.execute("DELETE FROM soloestate.crm_operations WHERE action='slot:generate' "
                             "AND response->'record'->'slot_ids' = %s::jsonb",
                             (json.dumps(sentinel['gen_ids']),))
            if sentinel['leads'] + sentinel['slots']:
                conn.execute('DELETE FROM soloestate.crm_operations WHERE entity_id=ANY(%s::uuid[])',
                             (sentinel['leads'] + sentinel['slots'],))
            if sentinel_phone is not None:
                conn.execute("DELETE FROM soloestate.crm_operations WHERE action='suppression:create' "
                             "AND response->'record'->>'destination' = %s", (sentinel_phone,))
                conn.execute('DELETE FROM soloestate.suppression_list WHERE destination=%s', (sentinel_phone,))
            if sentinel['slots']:
                conn.execute('DELETE FROM soloestate.visit_slots WHERE id=ANY(%s::uuid[])', (sentinel['slots'],))
            if sentinel['leads']:
                conn.execute('DELETE FROM soloestate.leads WHERE id=ANY(%s::uuid[])', (sentinel['leads'],))


def test_policy_cleanup_restores_prearmed_stop_without_conflict(iso):
    crm = iso
    pre_reason = 'SentinelD1-prearm-' + uuid4().hex[:8]
    key = lambda: 'cleanup-prearm:' + str(uuid4())
    pol.set_stop(crm, s.ControlsSet(request_key=key(), stopped=True, reason=pre_reason))
    data = policy_fixture.plan()
    assert data['was_stopped'] is True
    try:
        lifted = pol.set_stop(crm, s.ControlsSet(
            request_key=key(), stopped=False, reason=data['reason_stop_off']))['record']
        data['stop_lifted_at'] = lifted['updated_at']
        result = policy_fixture.cleanup(data)
        assert result['conflicts'] == []
        with connect(Settings()) as conn:
            assert scalar(conn.execute('SELECT emergency_stop FROM soloestate.automation_controls WHERE id=%s',
                                       ('default',)).fetchone()) is True
    finally:
        with connect(Settings()) as conn:
            conn.execute("UPDATE soloestate.automation_controls SET emergency_stop=false WHERE id='default'")
            conn.execute("DELETE FROM soloestate.crm_operations WHERE response->>'reason' = ANY(%s)",
                         ([pre_reason, data['reason_stop_off']],))
            _scrub_owned(conn, data, [])


def test_policy_cleanup_conflict_preserves_intervening_stop_change(iso):
    crm = iso
    pre_reason = 'SentinelD1-prearm-' + uuid4().hex[:8]
    key = lambda: 'cleanup-conflict:' + str(uuid4())
    pol.set_stop(crm, s.ControlsSet(request_key=key(), stopped=True, reason=pre_reason))
    data = policy_fixture.plan()
    assert data['was_stopped'] is True
    intrude_reason = 'SentinelD1-intrude-' + uuid4().hex[:8]
    try:
        pol.set_stop(crm, s.ControlsSet(request_key=key(), stopped=False, reason=data['reason_stop_off']))
        with connect(Settings()) as conn:
            lifted_at = scalar(conn.execute(
                "SELECT updated_at FROM soloestate.automation_controls WHERE id='default'").fetchone())
        data['stop_lifted_at'] = lifted_at.isoformat()
        pol.set_stop(crm, s.ControlsSet(request_key=key(), stopped=False, reason=intrude_reason))
        result = policy_fixture.cleanup(data)
        assert result['conflicts'] == ['emergency_stop lifted by this run but controls changed since; preserved off']
        with connect(Settings()) as conn:
            assert scalar(conn.execute('SELECT emergency_stop FROM soloestate.automation_controls WHERE id=%s',
                                       ('default',)).fetchone()) is False
    finally:
        with connect(Settings()) as conn:
            conn.execute("UPDATE soloestate.automation_controls SET emergency_stop=false WHERE id='default'")
            conn.execute("DELETE FROM soloestate.crm_operations WHERE response->>'reason' = ANY(%s)",
                         ([pre_reason, data['reason_stop_off'], intrude_reason],))
            _scrub_owned(conn, data, [])


def test_policy_cleanup_cas_preserves_interleaved_owner_change(iso):
    crm = iso
    pre_reason = 'SentinelD1-caspre-' + uuid4().hex[:8]
    key = lambda: 'cleanup-cas:' + str(uuid4())
    pol.set_stop(crm, s.ControlsSet(request_key=key(), stopped=True, reason=pre_reason))
    data = policy_fixture.plan()
    assert data['was_stopped'] is True
    intrude_reason = 'SentinelD1-casintrude-' + uuid4().hex[:8]
    intruded = []

    def intrude():
        # Committed on a second connection after cleanup's shared-control
        # read and before its restoration write. The old unconditional
        # UPDATE re-armed over this; the compare-and-set must lose loudly.
        pol.set_stop(CRM(), s.ControlsSet(
            request_key='cleanup-cas-intrude:' + str(uuid4()), stopped=False, reason=intrude_reason))
        intruded.append(True)

    try:
        lifted = pol.set_stop(crm, s.ControlsSet(
            request_key=key(), stopped=False, reason=data['reason_stop_off']))['record']
        data['stop_lifted_at'] = lifted['updated_at']
        result = policy_fixture.cleanup(data, _before_restore=intrude)
        assert intruded == [True]
        assert result['conflicts'] == ['emergency_stop re-arm lost a concurrent update; preserved current state']
        with connect(Settings()) as conn:
            assert scalar(conn.execute('SELECT emergency_stop FROM soloestate.automation_controls WHERE id=%s',
                                       ('default',)).fetchone()) is False
            assert scalar(conn.execute("SELECT count(*) FROM soloestate.crm_operations WHERE response->>'reason' = %s",
                                       (intrude_reason,)).fetchone()) == 1
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.leads WHERE id=ANY(%s::uuid[])',
                                       ([data['lead_id'], data['isolated_lead_id']],)).fetchone()) == 0
    finally:
        with connect(Settings()) as conn:
            conn.execute("UPDATE soloestate.automation_controls SET emergency_stop=false WHERE id='default'")
            conn.execute("DELETE FROM soloestate.crm_operations WHERE response->>'reason' = ANY(%s)",
                         ([pre_reason, data['reason_stop_off'], intrude_reason],))
            _scrub_owned(conn, data, [])
