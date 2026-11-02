"""D1 browser-test coordinates and exact-ownership cleanup. Does not seed production records.

Cleanup deletes ONLY explicitly recorded test-owned IDs/keys and operation
rows pinned by per-run unique persisted evidence (reason strings, destination,
created slot IDs). It never deletes by date range, action-plus-timestamp, or
"not present before the test". Shared controls are restored only when no
intervening change is detectable; otherwise the change is preserved and
reported as an explicit conflict (exit 1) after owned rows are removed.
"""
import json
import sys
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from app import crm_schemas as s
from app import identity_links
from app.config import Settings
from app.crm import CRM
from app.database import connect

IST = ZoneInfo('Asia/Kolkata')


def windows():
    today = datetime.now(IST).date()

    def landing(weekday, after):
        base = today + timedelta(days=after)
        day = base + timedelta(days=(weekday - base.isoweekday()) % 7)
        start = datetime(day.year, day.month, day.day, 11, 0, tzinfo=IST)
        return start, start + timedelta(minutes=30)
    return landing(2, 10), landing(3, 10)


def plan():
    prefix = 'BrowserD1-' + str(uuid4())
    phone = '+1555%07d' % (uuid4().int % 10000000)
    (tue_start, tue_end), (wed_start, wed_end) = windows()
    with connect(Settings()) as conn:
        stop, stop_at = conn.execute(
            'SELECT emergency_stop, updated_at FROM soloestate.automation_controls WHERE id=%s',
            ('default',)).fetchone()
        notice, policy_version = conn.execute(
            'SELECT notice_hours, version FROM soloestate.business_policies WHERE id=%s',
            ('default',)).fetchone()
    gen = tue_start.date() - timedelta(days=1)
    gen_end_day = gen + timedelta(days=6)
    crm = CRM()
    key = 'e2e-d1-plan-' + str(uuid4())
    lead = crm.save('leads', s.LeadCreate(request_key=key + '-lead', name=prefix,
        email=prefix.lower() + '@example.invalid', preferred_localities=[prefix],
        budget_min=100, budget_max=200, bedrooms=2))['record']
    prop = crm.save('properties', s.PropertyCreate(request_key=key + '-prop', title=prefix,
        locality=prefix, price=150, bedrooms=2))['record']
    seen = identity_links.receive(crm, 'whatsapp', phone[1:], key + '-identity')
    return dict(prefix=prefix, email=prefix.lower() + '@example.invalid', phone=phone,
                lead_id=lead['id'], property_id=prop['id'], link_id=seen['record']['id'],
                isolated_lead_id=seen['lead_id'], tue_start=tue_start.isoformat(),
                tue_end=tue_end.isoformat(), wed_start=wed_start.isoformat(),
                wed_end=wed_end.isoformat(), gen_start=gen.isoformat(),
                gen_end=gen_end_day.isoformat(), was_stopped=stop,
                stop_updated_at=stop_at.isoformat(), notice_hours=notice,
                policy_version=policy_version, request_ids=[], resource_ids=[],
                policy_ids=[], slot_ids=[], active_link_id=None, stop_lifted_at=None,
                reason_policy_check=prefix + ' policy check',
                reason_policy_restore=prefix + ' policy restore',
                reason_stop_on=prefix + ' stop drill',
                reason_stop_off=prefix + ' drill over')


def cleanup(data, _before_restore=None):
    """Remove test-owned rows, then compare-and-set shared controls.

    _before_restore is a test-only hook invoked after the shared-control read
    and before restoration, letting a regression interleave a committed owner
    change on another connection. Production callers leave it None.
    """
    prefix = data['prefix']
    assert prefix.startswith('BrowserD1-') and len(prefix) == 46
    conflicts = []
    with connect(Settings()) as conn:
        leads = [data['lead_id'], data['isolated_lead_id']]
        bookings = [str(r[0]) for r in conn.execute(
            'SELECT id FROM soloestate.bookings WHERE lead_id=ANY(%s::uuid[])', (leads,)).fetchall()]
        slots = [str(r[0]) for r in conn.execute(
            'SELECT slot_id FROM soloestate.bookings WHERE id=ANY(%s::uuid[])', (bookings,)).fetchall()] if bookings else []
        runs = [str(r[0]) for r in conn.execute(
            'SELECT DISTINCT run_id FROM soloestate.approvals WHERE lead_id=ANY(%s::uuid[])', (leads,)).fetchall()]
        convos = [str(r[0]) for r in conn.execute(
            'SELECT id FROM soloestate.conversations WHERE lead_id=ANY(%s::uuid[])', (leads,)).fetchall()]
        msgs = [str(r[0]) for r in conn.execute(
            'SELECT id FROM soloestate.conversation_messages WHERE conversation_id=ANY(%s::uuid[])',
            (convos,)).fetchall()] if convos else []
        owned_slots = [str(i) for i in data.get('slot_ids', [])]
        ids = leads + [data['property_id']] + bookings + runs + convos + msgs + data.get('request_ids', []) \
            + data.get('resource_ids', []) + data.get('policy_ids', []) + [data['link_id']]
        if data.get('active_link_id'):
            ids.append(data['active_link_id'])
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
        if msgs:
            conn.execute('DELETE FROM soloestate.conversation_messages WHERE id=ANY(%s::uuid[])', (msgs,))
        if convos:
            conn.execute('DELETE FROM soloestate.conversations WHERE id=ANY(%s::uuid[])', (convos,))
        conn.execute('DELETE FROM soloestate.identity_links WHERE lead_id=ANY(%s::uuid[])', (leads,))
        conn.execute('DELETE FROM soloestate.suppression_list WHERE destination=%s', (data['phone'],))
        conn.execute('DELETE FROM soloestate.lead_activities WHERE lead_id=ANY(%s::uuid[])', (leads,))
        conn.execute('DELETE FROM soloestate.crm_operations WHERE entity_id=ANY(%s::uuid[])', (ids,))
        # Entity-less operations, pinned by per-run unique persisted evidence only.
        reasons = [data['reason_policy_check'], data['reason_policy_restore']]
        policy_ops = conn.execute(
            "DELETE FROM soloestate.crm_operations WHERE action='policy:update' "
            'AND response->>%s = ANY(%s) RETURNING request_key',
            ('reason', reasons)).fetchall()
        stop_reasons = [data['reason_stop_on'], data['reason_stop_off']]
        stop_ops = conn.execute(
            "DELETE FROM soloestate.crm_operations WHERE action='automation-controls:set' "
            'AND response->>%s = ANY(%s) RETURNING request_key',
            ('reason', stop_reasons)).fetchall()
        supp_ops = conn.execute(
            "DELETE FROM soloestate.crm_operations WHERE action='suppression:create' "
            "AND response->'record'->>%s = %s RETURNING request_key",
            ('destination', data['phone'])).fetchall()
        gen_ops = []
        if owned_slots:
            gen_ops = conn.execute(
                "DELETE FROM soloestate.crm_operations WHERE action='slot:generate' "
                "AND response->'record'->'slot_ids' = %s::jsonb RETURNING request_key",
                (json.dumps(owned_slots),)).fetchall()
        if slots + owned_slots:
            conn.execute('DELETE FROM soloestate.visit_slots WHERE id=ANY(%s::uuid[])', (slots + owned_slots,))
        if data.get('policy_ids'):
            conn.execute('DELETE FROM soloestate.automation_policies WHERE id=ANY(%s::uuid[])',
                         (data['policy_ids'],))
        if data.get('resource_ids'):
            conn.execute('DELETE FROM soloestate.resources WHERE id=ANY(%s::uuid[])', (data['resource_ids'],))
        conn.execute('DELETE FROM soloestate.properties WHERE id=%s', (data['property_id'],))
        conn.execute('DELETE FROM soloestate.leads WHERE id=%s', (data['lead_id'],))
        conn.execute('DELETE FROM soloestate.leads WHERE id=%s', (data['isolated_lead_id'],))
        # Shared controls: restore only when no intervening change is detectable.
        # notice_hours is never written here (conflict-only), so it has no
        # write race; the stop re-arm below is an atomic compare-and-set.
        stop, stop_at, notice = conn.execute(
            'SELECT c.emergency_stop, c.updated_at, p.notice_hours FROM soloestate.automation_controls c, '
            "soloestate.business_policies p WHERE c.id='default' AND p.id='default'").fetchone()
        if _before_restore is not None:
            _before_restore()
        if notice != data['notice_hours']:
            conflicts.append('notice_hours changed to %r (expected %r); preserved' % (notice, data['notice_hours']))
        if data['was_stopped']:
            if stop is True:
                pass
            elif stop is False and (
                    (data.get('stop_lifted_at')
                     and stop_at == datetime.fromisoformat(data['stop_lifted_at']))
                    or stop_at == datetime.fromisoformat(data['stop_updated_at'])):
                restored = conn.execute(
                    "UPDATE soloestate.automation_controls SET emergency_stop=true WHERE id='default' "
                    'AND emergency_stop=false AND updated_at=%s RETURNING id',
                    (stop_at,)).fetchone()
                if restored is None:
                    conflicts.append('emergency_stop re-arm lost a concurrent update; preserved current state')
            else:
                conflicts.append('emergency_stop lifted by this run but controls changed since; preserved off')
        elif stop is not False:
            conflicts.append('emergency_stop is %r (expected false); preserved' % (stop,))
    return dict(cleaned=prefix, ids=len(ids), slots=len(slots + owned_slots),
                ops=len(policy_ops) + len(stop_ops) + len(supp_ops) + len(gen_ops),
                conflicts=conflicts)


if __name__ == '__main__':
    if sys.argv[1] == 'plan':
        print(json.dumps(plan()))
    else:
        result = cleanup(json.loads(sys.argv[2]))
        print(json.dumps(result))
        if result['conflicts']:
            sys.exit(1)
