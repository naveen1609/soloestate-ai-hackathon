"""B5 real PostgreSQL visit-outcome checks; fictional rollback records, except the outcome/cancel race committed on separate connections with exact-ID cleanup."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.database import connect
from app.crm import CRM, CRMError
from app import crm_schemas as s
from app.visits import record_outcome
from app.main import create_app
from app.crm_api import service
from app.owner_api import require_owner

pytestmark = pytest.mark.integrity_database


def records(crm, conn):
    prefix = 'B5-fictional-' + str(uuid4())
    def key(): return prefix + ':' + str(uuid4())
    lead = crm.save('leads', s.LeadCreate(request_key=key(), name=prefix, email=prefix + '@example.invalid',
        preferred_localities=[prefix], budget_min=100, budget_max=200, bedrooms=2))['record']
    prop = crm.save('properties', s.PropertyCreate(request_key=key(), title=prefix, locality=prefix,
        price=150, bedrooms=2))['record']
    start = conn.execute("SELECT greatest(coalesce(max(ends_at),clock_timestamp()),clock_timestamp())+interval '20 years' FROM soloestate.visit_slots").fetchone()[0]
    slots = [crm.slot(s.SlotCreate(request_key=key(), starts_at=start + timedelta(hours=i),
        ends_at=start + timedelta(hours=i, minutes=30)))['record'] for i in (0, 2, 4, 6)]
    return dict(crm=crm, conn=conn, key=key, prefix=prefix, lead=lead, prop=prop, slots=slots)


@pytest.fixture
def b5(request):
    if not request.config.getoption('--run-integrity-db-tests'): pytest.skip('Use --run-integrity-db-tests')
    with connect(Settings()) as conn:
        with conn.transaction(force_rollback=True):
            @contextmanager
            def factory():
                with conn.transaction(): yield conn
            yield records(CRM(factory), conn)


def prepare(b, slot=0, old=None):
    return b['crm'].prepare(s.AppointmentRequest(request_key=b['key'](), lead_id=b['lead']['id'],
        property_id=b['prop']['id'], slot_id=b['slots'][slot]['id'],
        replaces_booking_id=old['id'] if old else None, expected_version=old['version'] if old else None))['record']


def book(b, slot=0, old=None):
    review = prepare(b, slot, old)
    return b['crm'].commit(UUID(review['id']), s.Confirm(request_key=b['key'](),
        expected_payload_hash=review['payload_hash']))['record']


def outcome(b, booking, **kw):
    base = dict(request_key=b['key'](), expected_version=booking['version'], reason='Fictional visit outcome')
    return record_outcome(b['crm'], booking['id'], s.OutcomeUpdate(**{**base, **kw}))['record']


def history(b):
    from app.journey import activities
    return activities(b['crm'], b['lead']['id'])['items']


def cancel(b, booking):
    return b['crm'].cancel(booking['id'], s.Cancel(request_key=b['key'](),
        expected_version=booking['version'], reason='Fictional owner cancellation'))['record']


def test_record_completed_derives_result_and_preserves_journey(b5):
    b = b5; visit = book(b)
    assert (visit['visit_outcome'], visit['followup_state'], visit['visit_result']) == (None, 'none', 'scheduled')
    done = outcome(b, visit, visit_outcome='completed', outcome_note='Buyer toured twice.',
        followup_state='pending', followup_note='Send inventory sheet.')
    assert (done['visit_outcome'], done['outcome_note'], done['followup_state'], done['followup_note']) == (
        'completed', 'Buyer toured twice.', 'pending', 'Send inventory sheet.')
    assert done['version'] == 2 and done['status'] == 'confirmed'
    detail = b['crm'].detail('appointments', visit['id'])['record']
    assert (detail['visit_result'], detail['has_replacement']) == ('completed', False)
    assert b['crm'].detail('leads', b['lead']['id'])['record']['journey_stage'] == 'new'


def test_no_show_is_terminal_and_leaves_history_intact(b5):
    b = b5; visit = book(b)
    missed = outcome(b, visit, visit_outcome='no_show')
    assert missed['visit_outcome'] == 'no_show' and missed['outcome_note'] is None
    assert b['crm'].detail('appointments', visit['id'])['record']['visit_result'] == 'no_show'
    before = b['crm'].detail('appointments', missed['id'])
    key = b['key']()
    with pytest.raises(CRMError, match='terminal'):
        record_outcome(b['crm'], missed['id'], s.OutcomeUpdate(request_key=key,
            expected_version=missed['version'], visit_outcome='completed', reason='Fictional rewrite'))
    with pytest.raises(CRMError, match='terminal'):
        record_outcome(b['crm'], missed['id'], s.OutcomeUpdate(request_key=b['key'](),
            expected_version=missed['version'], outcome_note='Late note.', reason='Fictional note'))
    assert b['crm'].detail('appointments', missed['id']) == before
    with pytest.raises(CRMError) as exc:
        b['crm'].operation(key)
    assert exc.value.status == 404
    assert len(history(b)) == 1


def test_cancelled_bookings_reject_outcome_writes(b5):
    b = b5; visit = book(b)
    cancelled = cancel(b, visit)
    key = b['key']()
    with pytest.raises(CRMError, match='confirmed visits'):
        record_outcome(b['crm'], cancelled['id'], s.OutcomeUpdate(request_key=key,
            expected_version=cancelled['version'], visit_outcome='completed', reason='Fictional rewrite'))
    assert b['crm'].detail('appointments', cancelled['id'])['record']['visit_outcome'] is None
    with pytest.raises(CRMError) as exc:
        b['crm'].operation(key)
    assert exc.value.status == 404
    assert history(b) == []


def test_recorded_outcomes_block_cancel_and_replace(b5):
    b = b5
    done = outcome(b, book(b, 0), visit_outcome='completed', reason='Fictional visit')
    missed = outcome(b, book(b, 1), visit_outcome='no_show', reason='Fictional visit')
    for visit in (done, missed):
        with pytest.raises(CRMError, match='cannot be cancelled or rescheduled'):
            b['crm'].cancel(visit['id'], s.Cancel(request_key=b['key'](),
                expected_version=visit['version'], reason='Fictional late cancel'))
        with pytest.raises(CRMError, match='cannot be cancelled or rescheduled'):
            prepare(b, 2, visit)
        assert b['crm'].detail('appointments', visit['id'])['record']['status'] == 'confirmed'


def test_commit_after_outcome_leaves_original_intact(b5):
    b = b5; visit = book(b)
    review = prepare(b, 1, visit)
    outcome(b, visit, visit_outcome='completed', reason='Fictional visit')
    with pytest.raises(CRMError):
        b['crm'].commit(UUID(review['id']), s.Confirm(request_key=b['key'](),
            expected_payload_hash=review['payload_hash']))
    assert b['crm'].detail('appointments', visit['id'])['record']['status'] == 'confirmed'
    assert b['conn'].execute('SELECT count(*) FROM soloestate.bookings WHERE lead_id=%s',
        (b['lead']['id'],)).fetchone()[0] == 1


def test_followup_graph_with_note_rules_and_reopen(b5):
    b = b5; visit = book(b)
    with pytest.raises(CRMError, match='next-action note'):
        outcome(b, visit, followup_state='pending')
    visit = outcome(b, visit, followup_state='pending', followup_note='Send inventory sheet.')
    assert (visit['followup_state'], visit['followup_note']) == ('pending', 'Send inventory sheet.')
    visit = outcome(b, visit, followup_state='done')
    assert (visit['followup_state'], visit['followup_note']) == ('done', 'Send inventory sheet.')
    with pytest.raises(CRMError, match='Invalid follow-up transition'):
        outcome(b, visit, followup_state='none')
    visit = outcome(b, visit, followup_state='pending')
    assert (visit['followup_state'], visit['followup_note']) == ('pending', 'Send inventory sheet.')
    visit = outcome(b, visit, followup_state='pending', followup_note='Send revised sheet.')
    assert visit['followup_note'] == 'Send revised sheet.'
    visit = outcome(b, visit, followup_state='none')
    assert (visit['followup_state'], visit['followup_note']) == ('none', None)


def test_followup_note_edits_and_invalid_combinations(b5):
    b = b5; visit = book(b)
    with pytest.raises(CRMError, match='Start visit follow-up'):
        outcome(b, visit, followup_note='Orphan note.')
    with pytest.raises(CRMError, match='Invalid follow-up transition'):
        outcome(b, visit, followup_state='done')
    visit = outcome(b, visit, followup_state='pending', followup_note='Call buyer.')
    with pytest.raises(CRMError, match='cleared when withdrawing'):
        outcome(b, visit, followup_state='none', followup_note='Kept note.')
    with pytest.raises(CRMError, match='nonempty'):
        outcome(b, visit, followup_note='   ')
    visit = outcome(b, visit, followup_note='Call buyer Tuesday.')
    assert visit['followup_note'] == 'Call buyer Tuesday.'
    with pytest.raises(CRMError, match='No visit change'):
        outcome(b, visit, followup_note='Call buyer Tuesday.')
    with pytest.raises(CRMError, match='Recording an outcome is required'):
        outcome(b, visit, outcome_note='Orphan outcome note.')


def test_followup_works_on_cancelled_visits(b5):
    b = b5; visit = book(b)
    cancelled = cancel(b, visit)
    updated = outcome(b, cancelled, followup_state='pending', followup_note='Offer next week.')
    assert (updated['status'], updated['followup_state']) == ('cancelled', 'pending')
    assert b['crm'].detail('appointments', cancelled['id'])['record']['visit_result'] == 'cancelled'


def test_replay_returns_identical_outcome_without_new_writes(b5):
    b = b5; visit = book(b)
    req = s.OutcomeUpdate(request_key=b['key'](), expected_version=visit['version'],
        visit_outcome='completed', followup_state='pending', followup_note='Send sheet.', reason='Fictional visit')
    first = record_outcome(b['crm'], visit['id'], req)
    assert record_outcome(b['crm'], visit['id'], req) == first
    assert b['crm'].detail('appointments', visit['id'])['record']['version'] == 2
    assert len(history(b)) == 1


def test_changed_payload_key_reuse_rejected_without_side_effects(b5):
    b = b5; visit = book(b)
    req = s.OutcomeUpdate(request_key=b['key'](), expected_version=visit['version'],
        visit_outcome='completed', reason='Fictional visit')
    record_outcome(b['crm'], visit['id'], req)
    with pytest.raises(CRMError, match='different operation'):
        record_outcome(b['crm'], visit['id'], req.model_copy(
            update={'followup_state': 'pending', 'followup_note': 'Late.'}))
    detail = b['crm'].detail('appointments', visit['id'])['record']
    assert (detail['version'], detail['followup_state']) == (2, 'none')
    assert len(history(b)) == 1


def test_stale_version_rejected_without_side_effects(b5):
    b = b5
    original = book(b)
    updated = outcome(b, original, followup_state='pending', followup_note='Call buyer.')
    assert updated['version'] == 2
    with pytest.raises(CRMError, match='changed'):
        outcome(b, original, followup_state='done')
    assert b['crm'].detail('appointments', original['id'])['record']['followup_state'] == 'pending'


def test_missing_booking_is_not_found_without_writes(b5):
    b = b5; key = b['key']()
    with pytest.raises(CRMError) as exc:
        record_outcome(b['crm'], uuid4(), s.OutcomeUpdate(request_key=key, expected_version=1,
            visit_outcome='completed', reason='Fictional visit'))
    assert exc.value.status == 404
    with pytest.raises(CRMError) as exc:
        b['crm'].operation(key)
    assert exc.value.status == 404


def test_archived_lead_blocks_outcome_writes(b5):
    b = b5; visit = book(b)
    b['crm'].archive(b['lead']['id'], s.Archive(request_key=b['key'](), expected_version=1, archived=True))
    with pytest.raises(CRMError) as exc:
        outcome(b, visit, visit_outcome='completed')
    assert exc.value.status == 422 and 'Restore' in str(exc.value)
    b['crm'].archive(b['lead']['id'], s.Archive(request_key=b['key'](), expected_version=2, archived=False))
    assert outcome(b, visit, visit_outcome='completed')['visit_outcome'] == 'completed'


def test_legacy_bookings_keep_null_outcome_with_no_implied_attendance(b5):
    b = b5; visit = book(b)
    row = b['conn'].execute('SELECT visit_outcome, outcome_note, followup_state, followup_note FROM soloestate.bookings WHERE id=%s',
        (visit['id'],)).fetchone()
    assert tuple(row) == (None, None, 'none', None)
    assert b['crm'].detail('appointments', visit['id'])['record']['visit_result'] == 'scheduled'
    cancelled = cancel(b, visit)
    assert cancelled['visit_outcome'] is None
    assert b['crm'].detail('appointments', visit['id'])['record']['visit_result'] == 'cancelled'


def test_history_snapshots_record_visit_before_and_after(b5):
    b = b5; visit = book(b)
    key = b['key']()
    record_outcome(b['crm'], visit['id'], s.OutcomeUpdate(request_key=key, expected_version=1,
        visit_outcome='completed', outcome_note='Toured twice.', followup_state='pending',
        followup_note='Send sheet.', reason='Fictional visit'))
    entries = history(b)
    assert len(entries) == 1
    entry = entries[0]
    assert (entry['reason'], entry['request_key']) == ('Fictional visit', key)
    assert entry['before_state']['visit'] == {'id': visit['id'], 'lead_id': b['lead']['id'],
        'property_id': b['prop']['id'], 'slot_id': b['slots'][0]['id'], 'status': 'confirmed',
        'replaces_booking_id': None, 'visit_outcome': None, 'outcome_note': None,
        'followup_state': 'none', 'followup_note': None, 'version': 1}
    after = entry['after_state']['visit']
    assert (after['visit_outcome'], after['outcome_note'], after['followup_state'],
        after['followup_note'], after['version']) == ('completed', 'Toured twice.', 'pending', 'Send sheet.', 2)
    assert after['id'] == visit['id']


def test_activity_write_failure_rolls_back_outcome_and_operation(b5, monkeypatch):
    import app.visits as visits_mod
    b = b5; visit = book(b)
    def fail(*args, **kwargs):
        raise RuntimeError('Simulated activity outage')
    monkeypatch.setattr(visits_mod, 'log_activity', fail)
    key = b['key']()
    with pytest.raises(RuntimeError, match='Simulated activity outage'):
        record_outcome(b['crm'], visit['id'], s.OutcomeUpdate(request_key=key, expected_version=1,
            visit_outcome='completed', reason='Fictional visit'))
    assert b['crm'].detail('appointments', visit['id'])['record']['visit_outcome'] is None
    with pytest.raises(CRMError) as exc:
        b['crm'].operation(key)
    assert exc.value.status == 404


def test_replacement_chain_derives_lineage_and_current(b5):
    b = b5
    first = book(b, 0); second = book(b, 1, first); third = book(b, 2, second)
    a = b['crm'].detail('appointments', first['id'])['record']
    c = b['crm'].detail('appointments', second['id'])['record']
    d = b['crm'].detail('appointments', third['id'])['record']
    assert (a['status'], a['visit_result'], a['has_replacement']) == ('cancelled', 'rescheduled', True)
    assert (c['status'], c['visit_result'], c['has_replacement']) == ('cancelled', 'rescheduled', True)
    assert (d['status'], d['visit_result'], d['has_replacement']) == ('confirmed', 'scheduled', False)
    assert (c['replaces_booking_id'], d['replaces_booking_id']) == (first['id'], second['id'])
    current = [row for row in b['crm'].listing('appointments', lead_id=b['lead']['id'])['items']
               if row['status'] == 'confirmed']
    assert [row['id'] for row in current] == [third['id']]


def test_fully_cancelled_thread_has_no_current_confirmed_appointment(b5):
    b = b5
    first = book(b, 0); second = book(b, 1, first)
    cancel(b, second)
    a = b['crm'].detail('appointments', first['id'])['record']
    c = b['crm'].detail('appointments', second['id'])['record']
    assert (a['visit_result'], a['has_replacement']) == ('rescheduled', True)
    assert (c['visit_result'], c['has_replacement']) == ('cancelled', False)
    assert b['crm'].listing('appointments', lead_id=b['lead']['id'], status='confirmed')['total'] == 0


def test_no_show_rebooks_as_fresh_request_without_rewriting_history(b5):
    b = b5
    missed = outcome(b, book(b, 0), visit_outcome='no_show', reason='Fictional visit')
    with pytest.raises(CRMError, match='cannot be cancelled or rescheduled'):
        prepare(b, 1, missed)
    fresh = book(b, 1)
    assert fresh['replaces_booking_id'] is None and fresh['status'] == 'confirmed'
    assert fresh['visit_outcome'] is None
    again = b['crm'].detail('appointments', missed['id'])['record']
    assert (again['visit_outcome'], again['status'], again['version']) == ('no_show', 'confirmed', 2)


def test_outcome_filter_lists_recorded_visits(b5):
    b = b5
    outcome(b, book(b, 0), visit_outcome='completed', reason='Fictional visit')
    outcome(b, book(b, 1), visit_outcome='no_show', reason='Fictional visit')
    book(b, 2)
    assert b['crm'].listing('appointments', lead_id=b['lead']['id'], outcome='completed')['total'] == 1
    assert b['crm'].listing('appointments', lead_id=b['lead']['id'], outcome='no_show')['total'] == 1
    assert b['crm'].listing('appointments', lead_id=b['lead']['id'], status='confirmed')['total'] == 3
    with pytest.raises(CRMError) as exc:
        b['crm'].listing('appointments', outcome='visited')
    assert exc.value.status == 422


def test_owner_http_outcome_flow(b5):
    b = b5; visit = book(b)
    app = create_app(); app.dependency_overrides[require_owner] = lambda: 'owner'
    app.dependency_overrides[service] = lambda: b['crm']
    with TestClient(app) as api:
        wrote = api.post(f"/owner/crm/appointments/{visit['id']}/outcome", json=dict(
            request_key=b['key'](), expected_version=1, visit_outcome='completed',
            followup_state='pending', followup_note='Send sheet.', reason='Fictional visit')).json()['record']
        assert (wrote['visit_outcome'], wrote['followup_state'], wrote['version']) == ('completed', 'pending', 2)
        assert api.get(f"/owner/crm/appointments/{visit['id']}").json()['record']['visit_result'] == 'completed'
        assert api.get('/owner/crm/appointments', params={'lead_id': b['lead']['id'],
            'outcome': 'completed'}).json()['total'] == 1
        assert api.get(f'/owner/crm/appointments/{uuid4()}').status_code == 404
        assert api.post(f"/owner/crm/appointments/{visit['id']}/outcome", json=dict(
            request_key=b['key'](), expected_version=1, followup_state='done',
            reason='Fictional stale')).status_code == 409
        assert api.get('/owner/crm/appointments?outcome=visited').status_code == 422


def test_outcome_and_cancel_race_serializes_with_atomic_rejection(request):
    if not request.config.getoption('--run-integrity-db-tests'): pytest.skip('Use --run-integrity-db-tests')
    crm = CRM()
    with connect(Settings()) as conn:
        b = records(crm, conn)
    try:
        visit = book(b)
        gate = Barrier(2)
        outcome_key, cancel_key = b['key'](), b['key']()
        def record():
            gate.wait(timeout=30)
            try:
                return record_outcome(CRM(), visit['id'], s.OutcomeUpdate(request_key=outcome_key,
                    expected_version=visit['version'], visit_outcome='completed', reason='Fictional race'))
            except CRMError:
                return None
        def cancel_visit():
            gate.wait(timeout=30)
            try:
                return CRM().cancel(visit['id'], s.Cancel(request_key=cancel_key,
                    expected_version=visit['version'], reason='Fictional race'))
            except CRMError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            won_outcome, won_cancel = list(pool.map(lambda fn: fn(), [record, cancel_visit]))
        assert (won_outcome is not None) != (won_cancel is not None)
        with connect(Settings()) as conn:
            row = conn.execute('SELECT status, visit_outcome, version FROM soloestate.bookings WHERE id=%s',
                (visit['id'],)).fetchone()
            if won_outcome is not None:
                assert tuple(row) == ('confirmed', 'completed', 2)
                assert conn.execute('SELECT count(*) FROM soloestate.lead_activities WHERE lead_id=%s',
                    (b['lead']['id'],)).fetchone()[0] == 1
                assert conn.execute('SELECT count(*) FROM soloestate.crm_operations WHERE request_key=%s',
                    (cancel_key,)).fetchone()[0] == 0
            else:
                assert tuple(row) == ('cancelled', None, 2)
                assert conn.execute('SELECT count(*) FROM soloestate.lead_activities WHERE lead_id=%s',
                    (b['lead']['id'],)).fetchone()[0] == 0
                assert conn.execute('SELECT count(*) FROM soloestate.crm_operations WHERE request_key=%s',
                    (outcome_key,)).fetchone()[0] == 0
    finally:
        with connect(Settings()) as conn:
            runs = [r[0] for r in conn.execute('SELECT run_id FROM soloestate.approvals WHERE lead_id=%s',
                (b['lead']['id'],)).fetchall()]
            conn.execute('DELETE FROM soloestate.bookings WHERE lead_id=%s', (b['lead']['id'],))
            conn.execute('DELETE FROM soloestate.approval_decisions WHERE approval_id IN (SELECT id FROM soloestate.approvals WHERE lead_id=%s)',
                (b['lead']['id'],))
            conn.execute('DELETE FROM soloestate.approvals WHERE lead_id=%s', (b['lead']['id'],))
            conn.execute('DELETE FROM soloestate.runs WHERE id=ANY(%s::uuid[])', (runs,))
            conn.execute('DELETE FROM soloestate.lead_activities WHERE lead_id=%s', (b['lead']['id'],))
            conn.execute('DELETE FROM soloestate.crm_operations WHERE request_key LIKE %s', (b['prefix'] + ':%',))
            conn.execute('DELETE FROM soloestate.visit_slots WHERE id=ANY(%s::uuid[])', ([item['id'] for item in b['slots']],))
            conn.execute('DELETE FROM soloestate.properties WHERE id=%s', (b['prop']['id'],))
            conn.execute('DELETE FROM soloestate.leads WHERE id=%s', (b['lead']['id'],))
