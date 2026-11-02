"""D1 database tests: policy scheduling, booking-request lifecycle, identity, caps, legacy compat.

Rollback by default; committed fixtures with exact-ID cleanup for concurrency.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from threading import Barrier, Event
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Json
from app import crm_schemas as s
from app import booking_requests, identity_links, resources
from app import policies as pol
from app.caps import authorize_delivery, cooldown_ok, refund_cap, reserve_cap
from app.config import Settings
from app.agent.store import RunConflict
from app.crm import CRM, CRMError, scalar
from app.database import connect
from app.owner import DecisionRequest, OwnerService
from app.policies import OWNER_RESOURCE_ID
from app.resources import lock_ordered
from app.tools.schemas import ToolContext
from app.tools.service import BusinessTools

pytestmark = pytest.mark.d1_database

IST = ZoneInfo('Asia/Kolkata')


def business_window(*, weekday, hour, minute=0, duration=30, min_days_ahead=10):
    """Future IST window landing on the ISO weekday (Mon=1..Sun=7); past the fixed seed week."""
    today = datetime.now(IST).date()
    base = today + timedelta(days=min_days_ahead)
    day = base + timedelta(days=(weekday - base.isoweekday()) % 7)
    start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)
    return start, start + timedelta(minutes=duration)


def clean_window(conn, **kwargs):
    """business_window shifted by whole weeks off seeded policy holidays."""
    holidays = set(conn.execute("SELECT holiday_dates FROM soloestate.business_policies WHERE id='default'").fetchone()[0] or [])
    start, end = business_window(**kwargs)
    while start.date() in holidays or end.date() in holidays:
        start, end = start + timedelta(days=7), end + timedelta(days=7)
    return start, end


def records(crm, conn):
    prefix = 'd1-test-' + str(uuid4())
    def key():
        return prefix + ':' + str(uuid4())
    lead = crm.save('leads', s.LeadCreate(request_key=key(), name=prefix, email=prefix + '@example.invalid',
        preferred_localities=[prefix], budget_min=100, budget_max=200, bedrooms=2))['record']
    prop = crm.save('properties', s.PropertyCreate(request_key=key(), title=prefix, locality=prefix, price=150, bedrooms=2))['record']
    agent = resources.create(crm, s.ResourceCreate(request_key=key(), name=prefix + ' agent'))['record']
    return dict(crm=crm, conn=conn, key=key, prefix=prefix, lead=lead, prop=prop, agent=agent)


@pytest.fixture
def d1(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    with connect(Settings()) as conn:
        with conn.transaction(force_rollback=True):
            @contextmanager
            def factory():
                with conn.transaction():
                    yield conn
            yield records(CRM(factory), conn)


def mkslot(b, start, end, resource_id=None):
    return b['crm'].slot(s.SlotCreate(request_key=b['key'](), starts_at=start, ends_at=end,
                                      resource_id=resource_id))['record']


def owner_book(b, slot_id, resource_id=None):
    review = b['crm'].prepare(s.AppointmentRequest(request_key=b['key'](), lead_id=b['lead']['id'],
        property_id=b['prop']['id'], slot_id=slot_id, resource_id=resource_id))['record']
    return b['crm'].commit(UUID(review['id']),
        s.Confirm(request_key=b['key'](), expected_payload_hash=review['payload_hash']))['record']


def tool_book(b, slot_id, resource_id=None):
    run = uuid4()
    b['conn'].execute("INSERT INTO soloestate.runs(id,request_key,goal,status) VALUES (%s,%s,'Fictional D1 tool booking','pending')",
                      (run, b['key']()))
    tools, ctx = BusinessTools(b['crm'].connect), ToolContext(run_id=run)
    args = dict(kind='booking', lead_id=b['lead']['id'], property_id=b['prop']['id'], slot_id=slot_id, idempotency_key=b['key']())
    if resource_id is not None:
        args['resource_id'] = str(resource_id)
    approval = tools.execute('request_approval', args, ctx).data['approval']['id']
    owner = OwnerService(b['crm'].connect)
    owner.decide(approval, DecisionRequest(decision='approved', expected_payload_hash=owner.review(approval)['payload_hash'],
                                           idempotency_key=b['key']()))
    return tools.execute('book_site_visit', dict(approval_id=approval, idempotency_key=b['key']()), ctx)


def propose(b, start, end, **overrides):
    params = dict(request_key=b['key'](), lead_id=b['lead']['id'], property_id=b['prop']['id'],
                  requested_start=start, requested_end=end)
    params.update(overrides)
    return booking_requests.propose(b['crm'], s.BookingRequestCreate(**params))


def run_ids_for_lead(conn, lead_id, prefix):
    rows = conn.execute(
        """SELECT id FROM soloestate.runs WHERE request_key LIKE %s ESCAPE '\\'
           OR id IN (SELECT run_id FROM soloestate.approvals WHERE lead_id=%s)""",
        (prefix.replace('%', '\\%').replace('_', '\\_') + '%', lead_id)).fetchall()
    return [row[0] for row in rows]


@pytest.fixture
def d1c(request):
    """Committed fixture for true concurrency; exact-ID cleanup afterwards."""
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    data = records(CRM(), None)
    data['slots'] = []
    yield data
    cleanup_committed(data)


def cleanup_committed(data):
    """Exact-ID cleanup; retries dropped connections (deletes are idempotent)."""
    for attempt in range(3):
        try:
            with connect(Settings()) as conn:
                with conn.transaction():
                    lead, prefix = data['lead']['id'], data['prefix']
                    run_ids = run_ids_for_lead(conn, lead, prefix)
                    slot_ids = [row[0] for row in conn.execute(
                        'SELECT slot_id FROM soloestate.bookings WHERE lead_id=%s', (lead,)).fetchall()]
                    if run_ids:
                        conn.execute('DELETE FROM soloestate.run_steps WHERE run_id = ANY(%s)', (run_ids,))
                    conn.execute('DELETE FROM soloestate.booking_requests WHERE lead_id=%s', (lead,))
                    conn.execute('DELETE FROM soloestate.bookings WHERE lead_id=%s', (lead,))
                    conn.execute("""DELETE FROM soloestate.approval_decisions WHERE approval_id IN
                        (SELECT id FROM soloestate.approvals WHERE lead_id=%s)""", (lead,))
                    conn.execute('DELETE FROM soloestate.approvals WHERE lead_id=%s', (lead,))
                    conn.execute('DELETE FROM soloestate.followup_drafts WHERE lead_id=%s', (lead,))
                    if run_ids:
                        conn.execute('DELETE FROM soloestate.runs WHERE id = ANY(%s)', (run_ids,))
                    conn.execute("""DELETE FROM soloestate.conversation_messages WHERE conversation_id IN
                        (SELECT id FROM soloestate.conversations WHERE lead_id=%s)""", (lead,))
                    conn.execute('DELETE FROM soloestate.conversations WHERE lead_id=%s', (lead,))
                    conn.execute('DELETE FROM soloestate.identity_links WHERE lead_id=%s', (lead,))
                    conn.execute('DELETE FROM soloestate.lead_activities WHERE lead_id=%s', (lead,))
                    for extra in data.get('extra_leads', []):
                        conn.execute("""DELETE FROM soloestate.conversation_messages WHERE conversation_id IN
                            (SELECT id FROM soloestate.conversations WHERE lead_id=%s)""", (extra,))
                        conn.execute('DELETE FROM soloestate.conversations WHERE lead_id=%s', (extra,))
                        conn.execute('DELETE FROM soloestate.identity_links WHERE lead_id=%s', (extra,))
                        conn.execute('DELETE FROM soloestate.lead_activities WHERE lead_id=%s', (extra,))
                        conn.execute('DELETE FROM soloestate.leads WHERE id=%s', (extra,))
                    conn.execute('DELETE FROM soloestate.crm_operations WHERE request_key LIKE %s ESCAPE %s',
                                 (prefix.replace('%', '\\%').replace('_', '\\_') + '%', '\\'))
                    if slot_ids:
                        conn.execute('DELETE FROM soloestate.visit_slots WHERE id = ANY(%s)', (slot_ids,))
                    for slot_id in data['slots']:
                        conn.execute('DELETE FROM soloestate.visit_slots WHERE id=%s', (slot_id,))
                    conn.execute('DELETE FROM soloestate.resources WHERE id=%s', (data['agent']['id'],))
                    conn.execute('DELETE FROM soloestate.properties WHERE id=%s', (data['prop']['id'],))
                    conn.execute('DELETE FROM soloestate.leads WHERE id=%s', (lead,))
            return
        except psycopg.OperationalError:
            if attempt == 2:
                raise


def mkslot_c(b, start, end, resource_id=None):
    slot = CRM().slot(s.SlotCreate(request_key=b['key'](), starts_at=start, ends_at=end, resource_id=resource_id))['record']
    b['slots'].append(slot['id'])
    return slot


# --- SCH: resource-aware scheduling ---

def test_sch08_seed_slots_migrated_to_owner_resource(d1):
    rows = d1['conn'].execute(
        "SELECT count(*), count(*) FILTER (WHERE resource_id=%s) FROM soloestate.visit_slots WHERE id::text LIKE '30000000-%%'",
        (str(OWNER_RESOURCE_ID),)).fetchone()
    assert rows[0] == 6 and rows[1] == 6
    assert d1['conn'].execute(
        'SELECT count(*) FROM soloestate.bookings WHERE resource_id IS NULL').fetchone()[0] == 0


def test_sch06_overlapping_visits_on_different_resources(d1):
    start, end = clean_window(d1['conn'], weekday=3, hour=11)
    owner_slot = mkslot(d1, start, end)
    agent_slot = mkslot(d1, start, end, resource_id=d1['agent']['id'])
    first = owner_book(d1, owner_slot['id'])
    second = owner_book(d1, agent_slot['id'], resource_id=d1['agent']['id'])
    assert first['status'] == second['status'] == 'confirmed'
    assert first['resource_id'] != second['resource_id']


def test_sch07_buffer_blocks_adjacent_and_trigger_backstop(d1):
    policy = pol.get_policy(d1['crm'])['record']
    pol.update_policy(d1['crm'], s.PolicyUpdate(request_key=d1['key'](), expected_version=policy['version'],
                                                buffer_minutes=15, reason='Fictional D1 buffer regression'))
    first_start, first_end = clean_window(d1['conn'], weekday=3, hour=11)
    adjacent = mkslot(d1, first_end, first_end + timedelta(minutes=30))
    staged = d1['crm'].prepare(s.AppointmentRequest(request_key=d1['key'](), lead_id=d1['lead']['id'],
        property_id=d1['prop']['id'], slot_id=adjacent['id']))['record']
    from app.crm import payload_hash
    staged_payload = d1['conn'].execute('SELECT payload FROM soloestate.approvals WHERE id=%s',
                                        (staged['approval_id'],)).fetchone()[0]
    decided_at = d1['conn'].execute(
        """INSERT INTO soloestate.approval_decisions(id,approval_id,idempotency_key,decision,payload_hash,actor)
           VALUES (%s,%s,%s,'approved',%s,'owner') RETURNING decided_at""",
        (uuid4(), staged['approval_id'], d1['key'](), payload_hash(staged_payload))).fetchone()[0]
    d1['conn'].execute("UPDATE soloestate.approvals SET status='approved',decided_at=%s WHERE id=%s",
                       (decided_at, staged['approval_id']))
    owner_book(d1, mkslot(d1, first_start, first_end)['id'])
    with pytest.raises(CRMError, match='not available|already booked'):
        owner_book(d1, adjacent['id'])
    later = mkslot(d1, first_end + timedelta(minutes=30), first_end + timedelta(minutes=60))
    assert owner_book(d1, later['id'])['status'] == 'confirmed'
    with pytest.raises(psycopg.errors.CheckViolation, match='Resource is already booked'):
        with d1['conn'].transaction():
            d1['conn'].execute(
                """INSERT INTO soloestate.bookings(id,run_id,approval_id,lead_id,slot_id,property_id,resource_id,idempotency_key)
                   SELECT %s,a.run_id,a.id,%s,%s,%s,%s,%s FROM soloestate.approvals a WHERE a.id=%s""",
                (uuid4(), d1['lead']['id'], adjacent['id'], d1['prop']['id'], str(OWNER_RESOURCE_ID),
                 d1['key'](), staged['approval_id']))


def test_sch09_tool_availability_matches_shared_evaluator(d1):
    from psycopg.rows import dict_row
    start, _ = clean_window(d1['conn'], weekday=3, hour=11)
    day_start = start.replace(hour=0, minute=0)
    created = [mkslot(d1, day_start + timedelta(hours=h), day_start + timedelta(hours=h, minutes=30)) for h in (11, 14)]
    run = uuid4()
    d1['conn'].execute("INSERT INTO soloestate.runs(id,request_key,goal,status) VALUES (%s,%s,'Fictional D1 availability','pending')",
                       (run, d1['key']()))
    result = BusinessTools(d1['crm'].connect).execute(
        'get_available_slots', dict(since=day_start.isoformat(), until=(day_start + timedelta(days=1)).isoformat()), ToolContext(run_id=run))
    assert result.status == 'succeeded'
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        expected = pol.availability_windows(cur, day_start, day_start + timedelta(days=1),
                                            now=datetime.now(IST).astimezone(IST))
    assert [w['id'] for w in result.data['slots']] == [str(w['id']) for w in expected]
    assert {slot['id'] for slot in created} <= {w['id'] for w in result.data['slots']}


def test_sch11_site_capacity_enforced_and_unset_means_unlimited(d1):
    start, end = clean_window(d1['conn'], weekday=3, hour=11)
    d1['crm'].save('properties', s.PropertyUpdate(
        title=d1['prop']['title'], locality=d1['prop']['locality'], price=d1['prop']['price'],
        bedrooms=d1['prop']['bedrooms'], description='', active=True, concurrent_visit_capacity=1,
        expected_version=d1['prop']['version'], request_key=d1['key']()), d1['prop']['id'])
    owner_book(d1, mkslot(d1, start, end)['id'])
    agent_slot = mkslot(d1, start, end, resource_id=d1['agent']['id'])
    with pytest.raises(CRMError, match='site_unavailable|Site capacity'):
        owner_book(d1, agent_slot['id'], resource_id=d1['agent']['id'])
    detail = d1['crm'].detail('properties', d1['prop']['id'])['record']
    d1['crm'].save('properties', s.PropertyUpdate(
        title=detail['title'], locality=detail['locality'], price=detail['price'],
        bedrooms=detail['bedrooms'], description=detail['description'], active=True,
        concurrent_visit_capacity=None, expected_version=detail['version'], request_key=d1['key']()), d1['prop']['id'])
    assert owner_book(d1, agent_slot['id'], resource_id=d1['agent']['id'])['status'] == 'confirmed'


def test_sch12_deactivate_blocked_by_future_booking_then_succeeds(d1):
    start, end = clean_window(d1['conn'], weekday=3, hour=11)
    booking = owner_book(d1, mkslot(d1, start, end, resource_id=d1['agent']['id'])['id'], resource_id=d1['agent']['id'])
    with pytest.raises(CRMError, match='Reassign or cancel') as blocked:
        resources.deactivate(d1['crm'], d1['agent']['id'], s.ResourceDeactivate(
            request_key=d1['key'](), expected_version=d1['agent']['version'], reason='Fictional D1 churn'))
    assert booking['id'] in str(blocked.value)
    d1['crm'].cancel(UUID(booking['id']), s.Cancel(
        request_key=d1['key'](), expected_version=booking['version'], reason='Fictional D1 capacity release'))
    result = resources.deactivate(d1['crm'], d1['agent']['id'], s.ResourceDeactivate(
        request_key=d1['key'](), expected_version=d1['agent']['version'], reason='Fictional D1 churn'))
    assert result['record']['active'] is False


def _commit_owner_flow(lead_id, prop_id, slot_id, resource_id, key):
    crm = CRM()
    review = crm.prepare(s.AppointmentRequest(request_key=key(), lead_id=lead_id, property_id=prop_id,
                                              slot_id=slot_id, resource_id=resource_id))['record']
    return crm.commit(UUID(review['id']), s.Confirm(request_key=key(), expected_payload_hash=review['payload_hash']))


def _commit_owner_flow_retry(lead_id, prop_id, slot_id, resource_id, key, old=None):
    """Whole-flow retry on lock-timeout 503s (the documented retry contract)."""
    for _ in range(4):
        try:
            crm = CRM()
            review = crm.prepare(s.AppointmentRequest(request_key=key(), lead_id=lead_id, property_id=prop_id,
                slot_id=slot_id, resource_id=resource_id, **(old or {})))['record']
            return crm.commit(UUID(review['id']), s.Confirm(request_key=key(),
                expected_payload_hash=review['payload_hash']))
        except CRMError as exc:
            if exc.status != 503:
                raise
    raise CRMError('Lock contention did not clear after retries.', 503)


def seed_alternatives(b, anchor, count=3):
    """Owner slot rows on clean days after anchor so alternatives() has grid rows."""
    made, ahead = 0, 1
    while made < count:
        day = (anchor + timedelta(days=ahead)).replace(hour=11, minute=0, second=0, microsecond=0)
        ahead += 1
        if day.isoweekday() == 2:
            continue
        mkslot(b, day, day + timedelta(minutes=30))
        made += 1


def test_sch05_concurrent_same_slot_single_winner(d1c):
    start, end = business_window(weekday=3, hour=11)
    slot = mkslot_c(d1c, start, end)
    barrier, outcomes = Barrier(2), []
    def attempt(n):
        barrier.wait(timeout=120)
        try:
            _commit_owner_flow(d1c['lead']['id'], d1c['prop']['id'], slot['id'], None, d1c['key'])
            outcomes.append('booked')
        except (CRMError, RunConflict) as exc:
            outcomes.append(str(exc))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(attempt, (0, 1)))
    assert outcomes.count('booked') == 1
    assert len([o for o in outcomes if o != 'booked']) == 1
    with connect(Settings()) as conn:
        assert conn.execute("SELECT count(*) FROM soloestate.bookings WHERE slot_id=%s AND status='confirmed'",
                            (slot['id'],)).fetchone()[0] == 1


def test_sch10_concurrent_adjacent_buffer_single_winner(d1c):
    with connect(Settings()) as conn:
        with conn.transaction():
            conn.execute('UPDATE soloestate.business_policies SET buffer_minutes=15 WHERE id=%s', ('default',))
    try:
        start, _ = business_window(weekday=4, hour=11)
        first = mkslot_c(d1c, start, start + timedelta(minutes=30))
        second = mkslot_c(d1c, start + timedelta(minutes=30), start + timedelta(minutes=60))
        barrier, outcomes = Barrier(2), []
        def attempt(slot_id):
            barrier.wait(timeout=120)
            try:
                _commit_owner_flow(d1c['lead']['id'], d1c['prop']['id'], slot_id, None, d1c['key'])
                outcomes.append('booked')
            except (CRMError, RunConflict) as exc:
                outcomes.append(str(exc))
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(attempt, (first['id'], second['id'])))
        assert outcomes.count('booked') == 1
        with connect(Settings()) as conn:
            assert conn.execute("SELECT count(*) FROM soloestate.bookings WHERE lead_id=%s AND status='confirmed'",
                                (d1c['lead']['id'],)).fetchone()[0] == 1
    finally:
        with connect(Settings()) as conn:
            with conn.transaction():
                conn.execute('UPDATE soloestate.business_policies SET buffer_minutes=0 WHERE id=%s', ('default',))


def test_sch13_concurrent_mixed_owner_commits(d1c):
    start, _ = business_window(weekday=5, hour=11)
    slots = [mkslot_c(d1c, start + timedelta(hours=i), start + timedelta(hours=i, minutes=30)) for i in (0, 2)]
    barrier = Barrier(2)
    def attempt(slot_id):
        barrier.wait(timeout=120)
        return _commit_owner_flow_retry(d1c['lead']['id'], d1c['prop']['id'], slot_id, None, d1c['key'])['record']['status']
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(attempt, [s['id'] for s in slots])) == ['confirmed', 'confirmed']


def test_sch14_concurrent_cross_resource_replacement(d1c):
    start, _ = business_window(weekday=5, hour=14)
    first = mkslot_c(d1c, start, start + timedelta(minutes=30))
    second = mkslot_c(d1c, start, start + timedelta(minutes=30), resource_id=d1c['agent']['id'])
    crm = CRM()
    booking_a = owner_book({**d1c, 'crm': crm, 'conn': None}, first['id'])
    booking_b = owner_book({**d1c, 'crm': crm, 'conn': None}, second['id'], resource_id=d1c['agent']['id'])
    new_a = mkslot_c(d1c, start + timedelta(hours=2), start + timedelta(hours=2, minutes=30),
                     resource_id=d1c['agent']['id'])
    new_b = mkslot_c(d1c, start + timedelta(hours=4), start + timedelta(hours=4, minutes=30))
    barrier = Barrier(2)
    def attempt(booking, slot_id, resource_id):
        barrier.wait(timeout=120)
        old = dict(replaces_booking_id=booking['id'], expected_version=booking['version'])
        return _commit_owner_flow_retry(d1c['lead']['id'], d1c['prop']['id'], slot_id, resource_id, d1c['key'], old)['record']['status']
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(attempt, booking_a, new_a['id'], d1c['agent']['id'])
        future_b = pool.submit(attempt, booking_b, new_b['id'], None)
        assert [future_a.result(), future_b.result()] == ['confirmed', 'confirmed']


def test_sch15_ordered_locks_serialize_inverse_pairs(d1c):
    from psycopg.rows import dict_row
    barrier = Barrier(2)
    def attempt(order):
        barrier.wait(timeout=120)
        with connect(Settings()) as conn:
            with conn.transaction():
                with conn.cursor(row_factory=dict_row) as cur:
                    return [str(row['id']) for row in lock_ordered(cur, order)]
    pair = [UUID(str(OWNER_RESOURCE_ID)), UUID(d1c['agent']['id'])]
    with ThreadPoolExecutor(max_workers=2) as pool:
        submitted = [pool.submit(attempt, pair), pool.submit(attempt, list(reversed(pair)))]
        first, second = (future.result() for future in submitted)
    assert first == second == sorted([str(pair[0]), str(pair[1])])


def _hold_resource(resource_id, hold_seconds, locked, box=None, go=None):
    with connect(Settings()) as conn:
        with conn.transaction():
            conn.execute('SELECT * FROM soloestate.resources WHERE id=%s FOR UPDATE', (resource_id,))
            if box is not None:
                box['now'] = conn.execute('SELECT clock_timestamp()').fetchone()[0]
            locked.set()
            if go is not None:
                assert go.wait(timeout=30)
            conn.execute('SELECT pg_sleep(%s)', (hold_seconds,))


def _race_call(fn, tries=8):
    """Run fn, retrying retryable 503 contention per the documented contract.
    Non-503 outcomes (including graceful envelopes and terminal conflicts)
    return immediately; exhausted retries surface for the assertion."""
    for _ in range(tries):
        try:
            return ('ok', fn())
        except CRMError as exc:
            if exc.status == 503:
                continue
            return ('error', (exc.status, str(exc)))
        except RunConflict as exc:
            return ('error', ('conflict', str(exc)))
    return ('error', (503, 'retries exhausted'))


def test_sch16_same_property_capacity_race_refuses_gracefully(d1c):
    with connect(Settings()) as conn:
        start, end = clean_window(conn, weekday=3, hour=11)
        conn.execute('UPDATE soloestate.properties SET concurrent_visit_capacity=1 WHERE id=%s', (d1c['prop']['id'],))
    crm = CRM()
    rows = [booking_requests.propose(crm, s.BookingRequestCreate(
        request_key=d1c['key'](), lead_id=d1c['lead']['id'], property_id=d1c['prop']['id'],
        requested_start=start, requested_end=end, requested_resource_id=resource_id))['record']
        for resource_id in (str(OWNER_RESOURCE_ID), d1c['agent']['id'])]
    assert [row['status'] for row in rows] == ['approved', 'approved']
    barrier, outcomes = Barrier(2), []
    def attempt(row):
        barrier.wait(timeout=120)
        status, result = _race_call(lambda: booking_requests.commit(
            CRM(), UUID(row['id']), s.BookingCommit(
                request_key=d1c['key'](), expected_request_hash=row['request_hash'])))
        if status == 'ok':
            outcomes.append(('ok', result['committed'], tuple(result.get('reasons', []))))
        else:
            outcomes.append(('error', result))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(attempt, rows))
    booked = [o for o in outcomes if o[0] == 'ok' and o[1] is True]
    refused = [o for o in outcomes if o[0] == 'ok' and o[1] is False]
    assert len(booked) == 1 and len(refused) == 1 and refused[0][2] == ('site_capacity',), outcomes
    with connect(Settings()) as conn:
        assert conn.execute("SELECT count(*) FROM soloestate.bookings WHERE lead_id=%s AND status='confirmed'",
                            (d1c['lead']['id'],)).fetchone()[0] == 1


def test_sch17_mixed_owner_autonomy_race_single_winner(d1c):
    with connect(Settings()) as conn:
        start, end = clean_window(conn, weekday=4, hour=11)
    crm = CRM()
    slot = crm.slot(s.SlotCreate(request_key=d1c['key'](), starts_at=start, ends_at=end,
                                 resource_id=OWNER_RESOURCE_ID))['record']
    d1c['slots'].append(slot['id'])
    review = crm.prepare(s.AppointmentRequest(request_key=d1c['key'](), lead_id=d1c['lead']['id'],
        property_id=d1c['prop']['id'], slot_id=slot['id'], resource_id=OWNER_RESOURCE_ID))['record']
    req = booking_requests.propose(crm, s.BookingRequestCreate(
        request_key=d1c['key'](), lead_id=d1c['lead']['id'], property_id=d1c['prop']['id'],
        requested_start=start, requested_end=end, requested_resource_id=OWNER_RESOURCE_ID))['record']
    assert req['status'] == 'approved'
    barrier, outcomes = Barrier(2), []
    def owner_side():
        barrier.wait(timeout=120)
        status, result = _race_call(lambda: CRM().commit(UUID(review['id']), s.Confirm(
            request_key=d1c['key'](), expected_payload_hash=review['payload_hash'])))
        outcomes.append(('owner', 'booked' if status == 'ok' else result))
    def auto_side():
        barrier.wait(timeout=120)
        status, result = _race_call(lambda: booking_requests.commit(
            CRM(), UUID(req['id']), s.BookingCommit(
                request_key=d1c['key'](), expected_request_hash=req['request_hash'])))
        if status == 'ok':
            outcomes.append(('auto', 'booked' if result['committed'] else 'refused:' + ','.join(result['reasons'])))
        else:
            outcomes.append(('auto', result))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda fn: fn(), [owner_side, auto_side]))
    assert sum(1 for o in outcomes if o[1] == 'booked') == 1, outcomes
    for side, outcome in outcomes:
        if side == 'owner' and outcome != 'booked':
            assert outcome[0] != 503, f'owner path deadlocked: {outcome}'
        if side == 'auto' and outcome != 'booked':
            assert outcome == 'refused:resource_unavailable', f'unexpected autonomy outcome: {outcome}'
    with connect(Settings()) as conn:
        assert conn.execute("SELECT count(*) FROM soloestate.bookings WHERE slot_id=%s AND status='confirmed'",
                            (slot['id'],)).fetchone()[0] == 1


def test_sch18_deactivation_race_preserves_booking_or_refusal(d1c):
    with connect(Settings()) as conn:
        start, end = clean_window(conn, weekday=4, hour=14)
    crm = CRM()
    req = booking_requests.propose(crm, s.BookingRequestCreate(
        request_key=d1c['key'](), lead_id=d1c['lead']['id'], property_id=d1c['prop']['id'],
        requested_start=start, requested_end=end, requested_resource_id=d1c['agent']['id']))['record']
    assert req['status'] == 'approved'
    barrier, outcomes = Barrier(2), []
    def deactivate_side():
        barrier.wait(timeout=120)
        status, result = _race_call(lambda: resources.deactivate(
            CRM(), d1c['agent']['id'], s.ResourceDeactivate(
                request_key=d1c['key'](), expected_version=d1c['agent']['version'], reason='Fictional D1 churn')))
        outcomes.append(('deactivate', 'ok' if status == 'ok' else result))
    def commit_side():
        barrier.wait(timeout=120)
        status, result = _race_call(lambda: booking_requests.commit(
            CRM(), UUID(req['id']), s.BookingCommit(
                request_key=d1c['key'](), expected_request_hash=req['request_hash'])))
        if status == 'ok':
            outcomes.append(('commit', 'booked' if result['committed'] else 'refused:' + ','.join(result['reasons'])))
        else:
            outcomes.append(('commit', result))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda fn: fn(), [deactivate_side, commit_side]))
    by_side = dict(outcomes)
    assert ('booked' in by_side.values()) != ('ok' in by_side.values()), by_side
    if by_side['commit'] != 'booked':
        assert by_side['commit'] == 'refused:resource_unavailable', by_side
    if by_side['deactivate'] != 'ok':
        assert 'Reassign or cancel' in by_side['deactivate'][1], by_side


# --- APR: booking-request lifecycle ---

def confirm_req(b, row):
    return booking_requests.confirm(b['crm'], UUID(row['id']),
        s.BookingConfirm(request_key=b['key'](), expected_version=row['version']))


def decide_req(b, row, decision, reason='Fictional D1 owner review', **approved):
    return booking_requests.decide(b['crm'], UUID(row['id']), s.BookingDecide(
        request_key=b['key'](), expected_version=row['version'], expected_request_hash=row['request_hash'],
        decision=decision, reason=reason, **approved))


def commit_req(b, row, actor='owner'):
    return booking_requests.commit(b['crm'], UUID(row['id']),
        s.BookingCommit(request_key=b['key'](), expected_request_hash=row['request_hash']), actor)


def test_apr01_clean_exact_auto_approves_and_commits(d1):
    start, end = clean_window(d1['conn'], weekday=3, hour=11)
    created = propose(d1, start, end)
    row = created['record']
    assert row['status'] == 'approved' and row['needs_owner_approval'] is False
    assert created['alternatives'] == [] and created['exact_grid'] is True
    result = commit_req(d1, row)
    assert result['record']['status'] == 'booked'
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        booking = cur.execute('SELECT * FROM soloestate.bookings WHERE id=%s', (result['booking_id'],)).fetchone()
        assert booking['status'] == 'confirmed' and str(booking['resource_id']) == str(OWNER_RESOURCE_ID)
        approval = cur.execute('SELECT * FROM soloestate.approvals WHERE id=%s', (result['record']['approval_id'],)).fetchone()
        assert approval['kind'] == 'booking' and approval['status'] == 'consumed'
        decision = cur.execute('SELECT * FROM soloestate.approval_decisions WHERE approval_id=%s', (approval['id'],)).fetchone()
        assert decision['decision'] == 'approved' and decision['actor'] == 'policy'
        run = cur.execute('SELECT * FROM soloestate.runs WHERE id=%s', (approval['run_id'],)).fetchone()
        assert run['origin'] == 'autonomy' and run['status'] == 'completed'
        assert scalar(cur.execute('SELECT count(*) FROM soloestate.lead_activities WHERE lead_id=%s',
                                (d1['lead']['id'],)).fetchone()) >= 2


def test_apr02_tuesday_exception_owner_substitution_books(d1):
    start, end = business_window(weekday=2, hour=11)
    seed_alternatives(d1, start)
    created = propose(d1, start, end)
    row = created['record']
    assert row['status'] == 'awaiting_owner' and row['needs_owner_approval'] is True
    assert 'weekly_holiday' in row['exception_reasons'] and 'outside_working_day' in row['exception_reasons']
    assert len(created['alternatives']) > 0
    assert all('resource_id' in alt for alt in created['alternatives'])
    detail = booking_requests.detail(d1['crm'], UUID(row['id']))
    assert len(detail['alternatives']) > 0
    sub_start, sub_end = clean_window(d1['conn'], weekday=3, hour=11)
    decided = decide_req(d1, row, 'approved', approved_start=sub_start, approved_end=sub_end,
                         approved_resource_id=UUID(str(OWNER_RESOURCE_ID)))
    assert decided['record']['status'] == 'approved'
    result = commit_req(d1, decided['record'])
    assert result['record']['status'] == 'booked'
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        decision = cur.execute("""SELECT d.* FROM soloestate.approval_decisions d
            JOIN soloestate.approvals a ON a.id=d.approval_id WHERE a.id=%s""",
            (result['record']['approval_id'],)).fetchone()
        assert decision['actor'] == 'owner'
        booking = cur.execute('SELECT * FROM soloestate.bookings WHERE id=%s', (result['booking_id'],)).fetchone()
        slot = cur.execute('SELECT * FROM soloestate.visit_slots WHERE id=%s', (booking['slot_id'],)).fetchone()
        assert slot['starts_at'] == sub_start and slot['ends_at'] == sub_end


def test_apr03_resource_taken_between_approval_and_commit(d1):
    start, end = clean_window(d1['conn'], weekday=3, hour=11)
    row = propose(d1, start, end)['record']
    assert row['status'] == 'approved'
    owner_book(d1, mkslot(d1, start, end)['id'])
    refused = commit_req(d1, row)
    assert refused['committed'] is False and 'resource_unavailable' in refused['reasons']
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        updated = cur.execute('SELECT * FROM soloestate.booking_requests WHERE id=%s', (row['id'],)).fetchone()
        assert updated['status'] == 'awaiting_owner' and 'resource_unavailable' in updated['exception_reasons']
        assert updated['booking_id'] is None
        assert scalar(cur.execute('SELECT count(*) FROM soloestate.bookings WHERE lead_id=%s', (d1['lead']['id'],)).fetchone()) == 1


def test_apr04_past_window_expires_and_fresh_request_allowed(d1):
    now = datetime.now(IST)
    start = (now - timedelta(days=1)).replace(hour=11, minute=0, second=0, microsecond=0)
    row = propose(d1, start, start + timedelta(minutes=30))['record']
    assert row['status'] == 'awaiting_owner' and 'short_notice' in row['exception_reasons']
    refused = commit_req(d1, row)
    assert refused['committed'] is False and refused['record']['status'] == 'expired'
    with d1['conn'].cursor(row_factory=__import__('psycopg').rows.dict_row) as cur:
        assert scalar(cur.execute('SELECT status FROM soloestate.booking_requests WHERE id=%s', (row['id'],)).fetchone()) == 'expired'
    fresh_start, fresh_end = clean_window(d1['conn'], weekday=3, hour=14)
    assert propose(d1, fresh_start, fresh_end)['record']['status'] == 'approved'
    assert d1['conn'].execute('SELECT count(*) FROM soloestate.bookings WHERE lead_id=%s', (d1['lead']['id'],)).fetchone()[0] == 0


def test_apr05_conflict_returns_to_owner_with_fresh_alternatives(d1):
    start, end = clean_window(d1['conn'], weekday=4, hour=11)
    row = propose(d1, start, end)['record']
    seed_alternatives(d1, start)
    owner_book(d1, mkslot(d1, start, end)['id'])
    refused = commit_req(d1, row)
    assert refused['committed'] is False and 'resource_unavailable' in refused['reasons']
    detail = booking_requests.detail(d1['crm'], UUID(row['id']))
    assert detail['record']['status'] == 'awaiting_owner'
    assert len(detail['alternatives']) > 0


def test_apr06_customer_confirm_locks_normalized_window(d1):
    start, _ = clean_window(d1['conn'], weekday=4, hour=11)
    requested = start.replace(minute=7)
    created = propose(d1, requested, requested + timedelta(minutes=30))
    assert created['exact_grid'] is False
    row = created['record']
    assert row['status'] == 'proposed' and row['customer_confirmed_start'] is None
    assert row['proposal_expires_at'] is not None
    confirmed = confirm_req(d1, row)['record']
    assert confirmed['status'] == 'approved'
    assert datetime.fromisoformat(confirmed['requested_start']) == requested
    assert datetime.fromisoformat(confirmed['customer_confirmed_start']) == datetime.fromisoformat(created['normalized_start'])
    assert datetime.fromisoformat(confirmed['customer_confirmed_end']) == datetime.fromisoformat(created['normalized_end'])
    assert confirmed['customer_confirmed_start'] != confirmed['requested_start']
    assert commit_req(d1, confirmed)['record']['status'] == 'booked'


def test_apr07_proposal_ttl_and_request_ttl_expire(d1):
    from psycopg.rows import dict_row
    start, _ = clean_window(d1['conn'], weekday=4, hour=14)
    requested = start.replace(minute=7)
    proposed = propose(d1, requested, requested + timedelta(minutes=30))['record']
    d1['conn'].execute("UPDATE soloestate.booking_requests SET proposal_expires_at=clock_timestamp()-interval '1 minute' WHERE id=%s",
                       (proposed['id'],))
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        fresh = cur.execute('SELECT * FROM soloestate.booking_requests WHERE id=%s', (proposed['id'],)).fetchone()
    refused = booking_requests.confirm(d1['crm'], fresh['id'],
        s.BookingConfirm(request_key=d1['key'](), expected_version=fresh['version']))
    assert refused['confirmed'] is False and 'lapsed' in refused['message']
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        assert scalar(cur.execute('SELECT status FROM soloestate.booking_requests WHERE id=%s', (proposed['id'],)).fetchone()) == 'expired'
    approved = propose(d1, *clean_window(d1['conn'], weekday=5, hour=11))['record']
    d1['conn'].execute("UPDATE soloestate.booking_requests SET expires_at=clock_timestamp()-interval '1 minute' WHERE id=%s",
                       (approved['id'],))
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        fresh = cur.execute('SELECT * FROM soloestate.booking_requests WHERE id=%s', (approved['id'],)).fetchone()
    refused = booking_requests.commit(d1['crm'], fresh['id'],
        s.BookingCommit(request_key=d1['key'](), expected_request_hash=fresh['request_hash']))
    assert refused['committed'] is False and refused['record']['status'] == 'expired'
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        assert scalar(cur.execute('SELECT status FROM soloestate.booking_requests WHERE id=%s', (approved['id'],)).fetchone()) == 'expired'


def test_apr08_shifted_window_rechecked_at_commit(d1):
    from psycopg.rows import dict_row
    row = propose(d1, *clean_window(d1['conn'], weekday=5, hour=14))['record']
    d1['conn'].execute(
        """UPDATE soloestate.booking_requests
           SET customer_confirmed_start=clock_timestamp()+interval '15 hours',
               customer_confirmed_end=clock_timestamp()+interval '15 hours 30 minutes'
           WHERE id=%s""", (row['id'],))
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        fresh = cur.execute('SELECT * FROM soloestate.booking_requests WHERE id=%s', (row['id'],)).fetchone()
    refused = booking_requests.commit(d1['crm'], fresh['id'],
        s.BookingCommit(request_key=d1['key'](), expected_request_hash=fresh['request_hash']))
    assert refused['committed'] is False and 'Policy conditions changed' in refused['message']
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        updated = cur.execute('SELECT * FROM soloestate.booking_requests WHERE id=%s', (row['id'],)).fetchone()
        assert updated['status'] == 'awaiting_owner' and 'short_notice' in updated['exception_reasons']


def test_apr09_wrong_hash_rejected_without_side_effects(d1):
    from psycopg.rows import dict_row
    row = propose(d1, *clean_window(d1['conn'], weekday=3, hour=14))['record']
    with pytest.raises(CRMError, match='changed'):
        booking_requests.commit(d1['crm'], UUID(row['id']),
            s.BookingCommit(request_key=d1['key'](), expected_request_hash='0' * 64))
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        assert scalar(cur.execute('SELECT status FROM soloestate.booking_requests WHERE id=%s', (row['id'],)).fetchone()) == 'approved'
    assert d1['conn'].execute('SELECT count(*) FROM soloestate.bookings WHERE lead_id=%s', (d1['lead']['id'],)).fetchone()[0] == 0


def test_apr10_recorded_exception_passes_but_new_fails(d1):
    start, end = business_window(weekday=2, hour=11)
    row = propose(d1, start, end)['record']
    decided = decide_req(d1, row, 'approved', reason='Fictional D1 Tuesday override')['record']
    assert set(decided['exception_reasons']) >= {'weekly_holiday', 'outside_working_day'}
    assert commit_req(d1, decided)['record']['status'] == 'booked'
    other = propose(d1, *clean_window(d1['conn'], weekday=3, hour=11))['record']
    policy = pol.get_policy(d1['crm'])['record']
    pol.update_policy(d1['crm'], s.PolicyUpdate(request_key=d1['key'](), expected_version=policy['version'],
                                                hours_end='11:00', reason='Fictional D1 narrowed hours'))
    refused = commit_req(d1, other)
    assert refused['committed'] is False and 'Policy conditions changed' in refused['message']
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        updated = cur.execute('SELECT * FROM soloestate.booking_requests WHERE id=%s', (other['id'],)).fetchone()
        assert updated['status'] == 'awaiting_owner' and 'outside_hours' in updated['exception_reasons']


def test_apr11_deactivated_resource_blocks_commit(d1):
    start, end = clean_window(d1['conn'], weekday=4, hour=11)
    row = propose(d1, start, end, requested_resource_id=d1['agent']['id'])['record']
    assert row['status'] == 'approved'
    resources.deactivate(d1['crm'], d1['agent']['id'], s.ResourceDeactivate(
        request_key=d1['key'](), expected_version=d1['agent']['version'], reason='Fictional D1 departure'))
    refused = commit_req(d1, row)
    assert refused['committed'] is False and 'resource_unavailable' in refused['reasons']


def test_apr12_owner_exception_with_resource_substitution(d1):
    start, end = business_window(weekday=2, hour=14)
    row = propose(d1, start, end)['record']
    sub_start, sub_end = clean_window(d1['conn'], weekday=4, hour=14)
    decided = decide_req(d1, row, 'approved', approved_start=sub_start, approved_end=sub_end,
                         approved_resource_id=UUID(d1['agent']['id']))['record']
    result = commit_req(d1, decided)
    assert result['record']['status'] == 'booked'
    with d1['conn'].cursor(row_factory=__import__('psycopg').rows.dict_row) as cur:
        booking = cur.execute('SELECT * FROM soloestate.bookings WHERE id=%s', (result['booking_id'],)).fetchone()
        assert str(booking['resource_id']) == d1['agent']['id']


def test_apr13_conflicting_commit_returns_to_owner_never_books(d1):
    from psycopg.rows import dict_row
    row = propose(d1, *clean_window(d1['conn'], weekday=5, hour=11))['record']
    other = dict(d1, lead=d1['crm'].save('leads', s.LeadCreate(
        request_key=d1['key'](), name=d1['prefix'] + ' rival', email='rival-' + d1['prefix'] + '@example.invalid',
        preferred_localities=[d1['prefix']], budget_min=100, budget_max=200, bedrooms=2))['record'])
    owner_book(other, mkslot(d1, datetime.fromisoformat(row['customer_confirmed_start']),
                             datetime.fromisoformat(row['customer_confirmed_end']))['id'])
    refused = commit_req(d1, row)
    assert refused['committed'] is False and 'resource_unavailable' in refused['reasons']
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        updated = cur.execute('SELECT * FROM soloestate.booking_requests WHERE id=%s', (row['id'],)).fetchone()
        assert updated['status'] == 'awaiting_owner' and updated['booking_id'] is None


def test_apr_commit_race_on_missing_slot_single_booking(d1c):
    start, end = business_window(weekday=3, hour=11)
    crm = CRM()
    rows = [booking_requests.propose(crm, s.BookingRequestCreate(
        request_key=d1c['key'](), lead_id=d1c['lead']['id'], property_id=d1c['prop']['id'],
        requested_start=start, requested_end=end))['record'] for _ in range(2)]
    assert [row['status'] for row in rows] == ['approved', 'approved']
    barrier, outcomes = Barrier(2), []
    def attempt(row):
        barrier.wait(timeout=120)
        try:
            booking_requests.commit(CRM(), UUID(row['id']), s.BookingCommit(
                request_key=d1c['key'](), expected_request_hash=row['request_hash']))
            outcomes.append('booked')
        except (CRMError, RunConflict) as exc:
            outcomes.append(str(exc))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(attempt, rows))
    assert outcomes.count('booked') == 1
    assert not any('UniqueViolation' in outcome or 'duplicate key' in outcome for outcome in outcomes)
    with connect(Settings()) as conn:
        assert conn.execute("SELECT count(*) FROM soloestate.bookings WHERE lead_id=%s AND status='confirmed'",
                            (d1c['lead']['id'],)).fetchone()[0] == 1


def test_apr19_commit_rechecks_expiry_after_lock_wait(d1c):
    with connect(Settings()) as conn:
        start, end = clean_window(conn, weekday=3, hour=11)
    crm = CRM()
    row = booking_requests.propose(crm, s.BookingRequestCreate(
        request_key=d1c['key'](), lead_id=d1c['lead']['id'], property_id=d1c['prop']['id'],
        requested_start=start, requested_end=end))['record']
    assert row['status'] == 'approved'
    locked, go, box = Event(), Event(), {}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_hold_resource, row['customer_confirmed_resource_id'], 4, locked, box, go)
        assert locked.wait(timeout=30)
        with connect(Settings()) as conn:
            conn.execute('UPDATE soloestate.booking_requests SET expires_at=%s WHERE id=%s',
                         (box['now'] + timedelta(seconds=4), row['id']))
        go.set()
        result = booking_requests.commit(CRM(), UUID(row['id']), s.BookingCommit(
            request_key=d1c['key'](), expected_request_hash=row['request_hash']))
        future.result(timeout=60)
    assert result['committed'] is False and result['record']['status'] == 'expired'
    with connect(Settings()) as conn:
        assert conn.execute('SELECT count(*) FROM soloestate.bookings WHERE lead_id=%s',
                            (d1c['lead']['id'],)).fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM soloestate.approvals WHERE lead_id=%s',
                            (d1c['lead']['id'],)).fetchone()[0] == 0


def test_apr20_commit_rechecks_notice_after_lock_wait(d1c):
    from app.booking_requests import request_hash_for
    from app.policies import evaluate_window
    with connect(Settings()) as conn:
        start, end = clean_window(conn, weekday=3, hour=14)
    crm = CRM()
    row = booking_requests.propose(crm, s.BookingRequestCreate(
        request_key=d1c['key'](), lead_id=d1c['lead']['id'], property_id=d1c['prop']['id'],
        requested_start=start, requested_end=end))['record']
    assert row['status'] == 'approved' and row['exception_reasons'] == []
    resource_id = row['customer_confirmed_resource_id']
    locked, go, box = Event(), Event(), {}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_hold_resource, resource_id, 4, locked, box, go)
        assert locked.wait(timeout=30)
        with connect(Settings()) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                policy = cur.execute("SELECT * FROM soloestate.business_policies WHERE id='default'").fetchone()
            moved_start = box['now'] + timedelta(hours=16, seconds=4)
            moved_end = moved_start + timedelta(minutes=row['requested_duration_min'])
            before = evaluate_window(dict(policy), moved_start, moved_end, box['now'],
                                     row['requested_duration_min'])
            assert 'short_notice' not in before['reasons']
            conn.execute("""UPDATE soloestate.booking_requests
                SET customer_confirmed_start=%s, customer_confirmed_end=%s, request_hash=%s WHERE id=%s""",
                (moved_start, moved_end, request_hash_for(
                    lead_id=row['lead_id'], property_id=row['property_id'], start=moved_start, end=moved_end,
                    resource_id=UUID(resource_id), duration_min=row['requested_duration_min'],
                    policy_version=row['policy_version'], reasons=[]), row['id']))
            updated = conn.execute('SELECT request_hash FROM soloestate.booking_requests WHERE id=%s',
                                   (row['id'],)).fetchone()[0]
        go.set()
        result = booking_requests.commit(CRM(), UUID(row['id']), s.BookingCommit(
            request_key=d1c['key'](), expected_request_hash=updated))
        future.result(timeout=60)
    assert result['committed'] is False and result['record']['status'] == 'awaiting_owner'
    assert 'short_notice' in result['record']['exception_reasons']
    with connect(Settings()) as conn:
        assert conn.execute('SELECT count(*) FROM soloestate.bookings WHERE lead_id=%s',
                            (d1c['lead']['id'],)).fetchone()[0] == 0


def test_apr_propose_idempotent_and_changed_payload_rejected(d1):
    start, end = clean_window(d1['conn'], weekday=3, hour=15)
    fixed = d1['key']()
    first = booking_requests.propose(d1['crm'], s.BookingRequestCreate(
        request_key=fixed, lead_id=d1['lead']['id'], property_id=d1['prop']['id'],
        requested_start=start, requested_end=end))['record']
    again = booking_requests.propose(d1['crm'], s.BookingRequestCreate(
        request_key=fixed, lead_id=d1['lead']['id'], property_id=d1['prop']['id'],
        requested_start=start, requested_end=end))['record']
    assert again['id'] == first['id']
    with pytest.raises(CRMError):
        booking_requests.propose(d1['crm'], s.BookingRequestCreate(
            request_key=fixed, lead_id=d1['lead']['id'], property_id=d1['prop']['id'],
            requested_start=start + timedelta(hours=1), requested_end=end + timedelta(hours=1)))
    assert d1['conn'].execute('SELECT count(*) FROM soloestate.booking_requests WHERE lead_id=%s',
                              (d1['lead']['id'],)).fetchone()[0] == 1


def test_apr15_stop_blocks_commit(d1):
    from psycopg.rows import dict_row
    row = propose(d1, *clean_window(d1['conn'], weekday=3, hour=11))['record']
    pol.set_stop(d1['crm'], s.ControlsSet(request_key=d1['key'](), stopped=True, reason='Fictional D1 drill'))
    refused = commit_req(d1, row)
    assert refused['committed'] is False and 'emergency_stop' in refused['reasons']
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        updated = cur.execute('SELECT * FROM soloestate.booking_requests WHERE id=%s', (row['id'],)).fetchone()
        assert updated['status'] == 'awaiting_owner' and 'emergency_stop' in updated['exception_reasons']
    assert d1['conn'].execute('SELECT count(*) FROM soloestate.bookings WHERE lead_id=%s', (d1['lead']['id'],)).fetchone()[0] == 0


def test_apr16_direct_owner_flow_ignores_stop(d1):
    pol.set_stop(d1['crm'], s.ControlsSet(request_key=d1['key'](), stopped=True, reason='Fictional D1 drill'))
    start, end = clean_window(d1['conn'], weekday=4, hour=11)
    assert owner_book(d1, mkslot(d1, start, end)['id'])['status'] == 'confirmed'


def test_apr17_commit_recovers_after_stop_lifted(d1):
    row = propose(d1, *clean_window(d1['conn'], weekday=5, hour=11))['record']
    pol.set_stop(d1['crm'], s.ControlsSet(request_key=d1['key'](), stopped=True, reason='Fictional D1 drill'))
    refused = commit_req(d1, row)
    assert refused['committed'] is False and 'emergency_stop' in refused['reasons']
    pol.set_stop(d1['crm'], s.ControlsSet(request_key=d1['key'](), stopped=False, reason='Fictional D1 drill over'))
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        fresh = cur.execute('SELECT * FROM soloestate.booking_requests WHERE id=%s', (row['id'],)).fetchone()
    decided = booking_requests.decide(d1['crm'], fresh['id'], s.BookingDecide(
        request_key=d1['key'](), expected_version=fresh['version'], expected_request_hash=fresh['request_hash'],
        decision='approved', reason='Fictional D1 resume after drill'))
    assert commit_req(d1, decided['record'])['record']['status'] == 'booked'


def test_apr18_propose_and_decide_allowed_under_stop(d1):
    pol.set_stop(d1['crm'], s.ControlsSet(request_key=d1['key'](), stopped=True, reason='Fictional D1 drill'))
    start, end = business_window(weekday=2, hour=11)
    row = propose(d1, start, end)['record']
    assert row['status'] == 'awaiting_owner'
    sub_start, sub_end = clean_window(d1['conn'], weekday=3, hour=11)
    decided = decide_req(d1, row, 'approved', approved_start=sub_start, approved_end=sub_end)['record']
    assert decided['status'] == 'approved'
    refused = commit_req(d1, decided)
    assert refused['committed'] is False and 'emergency_stop' in refused['reasons']


def test_apr_route_refusal_maps_to_409_with_persisted_state(d1):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.crm_api import service
    from app.owner_api import require_owner
    row = propose(d1, *clean_window(d1['conn'], weekday=3, hour=11))['record']
    pol.set_stop(d1['crm'], s.ControlsSet(request_key=d1['key'](), stopped=True, reason='Fictional D1 drill'))
    app = create_app()
    app.dependency_overrides[require_owner] = lambda: 'owner'
    app.dependency_overrides[service] = lambda: d1['crm']
    with TestClient(app) as client:
        response = client.post('/owner/crm/booking-requests/' + row['id'] + '/commit',
                               json={'request_key': d1['key'](), 'expected_request_hash': row['request_hash']})
        assert response.status_code == 409
        assert 'Emergency stop' in response.json()['message']
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        updated = cur.execute('SELECT * FROM soloestate.booking_requests WHERE id=%s', (row['id'],)).fetchone()
        assert updated['status'] == 'awaiting_owner' and 'emergency_stop' in updated['exception_reasons']


# --- ID: identity isolation and owner-verified linking ---

def test_id01_first_sight_isolates_and_repeat_routes(d1):
    first = identity_links.receive(d1['crm'], 'whatsapp', '15550001111', d1['key']())
    assert first['scope'] == 'isolated' and first['created'] is True
    assert first['record']['status'] == 'observed'
    assert first['lead_id'] != d1['lead']['id']
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        isolated = cur.execute('SELECT * FROM soloestate.leads WHERE id=%s', (first['lead_id'],)).fetchone()
        assert isolated['email'].endswith('@example.invalid') and 'whatsapp' in isolated['email']
        assert isolated['archived_at'] is None
    second = identity_links.receive(d1['crm'], 'whatsapp', '15550001111', d1['key']())
    assert second['record']['id'] == first['record']['id'] and second['created'] is False


def test_id02_matching_contact_detail_does_not_attach(d1):
    canonical = d1['crm'].detail('leads', d1['lead']['id'])['record']
    result = identity_links.receive(d1['crm'], 'website', canonical['email'], d1['key']())
    assert result['lead_id'] != canonical['id'] and result['scope'] == 'isolated'
    after = d1['crm'].detail('leads', canonical['id'])['record']
    assert after['version'] == canonical['version'] and after['archived_at'] is None
    assert d1['conn'].execute('SELECT count(*) FROM soloestate.identity_links WHERE lead_id=%s',
                              (canonical['id'],)).fetchone()[0] == 0


def test_id03_owner_link_activates_and_destination_must_match_sender(d1):
    seen = identity_links.receive(d1['crm'], 'whatsapp', '15550002222', d1['key']())
    with pytest.raises(CRMError, match='verified destination'):
        identity_links.link(d1['crm'], s.IdentityLinkCreate(
            request_key=d1['key'](), link_id=seen['record']['id'], lead_id=d1['lead']['id'],
            verified_destination='+19995550000', reason='Fictional D1 wrong number'))
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        before = cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s',
                             (seen['conversation_id'],)).fetchone()
    linked = identity_links.link(d1['crm'], s.IdentityLinkCreate(
        request_key=d1['key'](), link_id=seen['record']['id'], lead_id=d1['lead']['id'],
        verified_destination='+15550002222', reason='Fictional D1 showroom check-in'))['record']
    assert linked['status'] == 'active' and linked['method'] == 'owner_verified'
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        assert scalar(cur.execute('SELECT status FROM soloestate.identity_links WHERE id=%s',
                                (seen['record']['id'],)).fetchone()) == 'superseded'
        assert scalar(cur.execute('SELECT archived_at FROM soloestate.leads WHERE id=%s',
                                  (seen['lead_id'],)).fetchone()) is not None
        assert scalar(cur.execute("SELECT count(*) FROM soloestate.lead_activities WHERE lead_id=%s AND reason LIKE 'Identity linked%%'",
                                  (d1['lead']['id'],)).fetchone()) == 1
        after = cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s',
                            (seen['conversation_id'],)).fetchone()
        assert dict(after) == dict(before)


def test_id04_link_to_archived_lead_rejected(d1):
    seen = identity_links.receive(d1['crm'], 'whatsapp', '15550003333', d1['key']())
    d1['crm'].archive(d1['lead']['id'], s.Archive(request_key=d1['key'](), expected_version=1, archived=True))
    with pytest.raises(CRMError, match='archived'):
        identity_links.link(d1['crm'], s.IdentityLinkCreate(
            request_key=d1['key'](), link_id=seen['record']['id'], lead_id=d1['lead']['id'],
            verified_destination='+15550003333', reason='Fictional D1 stale link'))


def test_id05_revoke_then_new_identity_observed(d1):
    seen = identity_links.receive(d1['crm'], 'whatsapp', '15550004444', d1['key']())
    active = identity_links.link(d1['crm'], s.IdentityLinkCreate(
        request_key=d1['key'](), link_id=seen['record']['id'], lead_id=d1['lead']['id'],
        verified_destination='+15550004444', reason='Fictional D1 consent desk'))['record']
    revoked = identity_links.revoke(d1['crm'], UUID(active['id']), s.IdentityRevoke(
        request_key=d1['key'](), expected_version=active['version'], reason='Fictional D1 number recycled'))['record']
    assert revoked['status'] == 'revoked'
    with pytest.raises(CRMError, match='active link'):
        identity_links.revoke(d1['crm'], UUID(active['id']), s.IdentityRevoke(
            request_key=d1['key'](), expected_version=revoked['version'], reason='Fictional D1 duplicate revoke'))
    again = identity_links.receive(d1['crm'], 'whatsapp', '15550004444', d1['key']())
    assert again['created'] is True and again['record']['id'] != active['id']
    assert again['record']['status'] == 'observed'


def test_id06_website_link_accepts_owner_confirmed_email(d1):
    seen = identity_links.receive(d1['crm'], 'website', 'visitor-' + d1['prefix'], d1['key']())
    with pytest.raises(CRMError, match='email address or E.164'):
        identity_links.link(d1['crm'], s.IdentityLinkCreate(
            request_key=d1['key'](), link_id=seen['record']['id'], lead_id=d1['lead']['id'],
            verified_destination='not an address', reason='Fictional D1 malformed'))
    linked = identity_links.link(d1['crm'], s.IdentityLinkCreate(
        request_key=d1['key'](), link_id=seen['record']['id'], lead_id=d1['lead']['id'],
        verified_destination='confirmed-' + d1['prefix'] + '@example.invalid',
        reason='Fictional D1 front-desk check'))['record']
    assert linked['status'] == 'active'


def test_id07_live_link_uniqueness_backstop(d1):
    identity_links.receive(d1['crm'], 'whatsapp', '15550005555', d1['key']())
    with pytest.raises(psycopg.errors.UniqueViolation):
        with d1['conn'].transaction():
            d1['conn'].execute(
                """INSERT INTO soloestate.identity_links(id,channel,provider_ref,lead_id,status,created_by_actor)
                   VALUES (%s,'whatsapp','15550005555',%s,'observed','channel')""", (uuid4(), d1['lead']['id']))


def test_id08_concurrent_links_single_winner(d1c):
    seen = identity_links.receive(CRM(), 'whatsapp', '15550005666', d1c['key']())
    other = CRM().save('leads', s.LeadCreate(request_key=d1c['key'](), name=d1c['prefix'] + ' rival canonical',
        email='rival-' + d1c['prefix'] + '@example.invalid'))['record']
    d1c['extra_leads'] = [seen['lead_id'], other['id']]
    barrier, outcomes = Barrier(2), []
    def attempt(lead_id):
        barrier.wait(timeout=120)
        status, result = _race_call(lambda: identity_links.link(
            CRM(), s.IdentityLinkCreate(request_key=d1c['key'](), link_id=seen['record']['id'], lead_id=lead_id,
                verified_destination='+15550005666', reason='Fictional D1 concurrent link')))
        outcomes.append((lead_id, status, result))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(attempt, [d1c['lead']['id'], other['id']]))
    won = [o for o in outcomes if o[1] == 'ok']
    lost = [o for o in outcomes if o[1] == 'error']
    assert len(won) == 1 and len(lost) == 1
    assert lost[0][2][0] != 503 and 'observed identity' in lost[0][2][1]
    with connect(Settings()) as conn:
        assert conn.execute("SELECT count(*) FROM soloestate.identity_links WHERE channel='whatsapp'"
                            " AND provider_ref='15550005666' AND status='active'").fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM soloestate.identity_links WHERE id=%s AND status=%s',
                            (seen['record']['id'], 'superseded')).fetchone()[0] == 1


def test_id09_canonical_conversation_reused_or_created(d1):
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        existing = cur.execute('INSERT INTO soloestate.conversations(lead_id) VALUES (%s) RETURNING *',
                               (d1['lead']['id'],)).fetchone()
    seen = identity_links.receive(d1['crm'], 'whatsapp', '15550005777', d1['key']())
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        before = cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s',
                             (seen['conversation_id'],)).fetchone()
    linked = identity_links.link(d1['crm'], s.IdentityLinkCreate(
        request_key=d1['key'](), link_id=seen['record']['id'], lead_id=d1['lead']['id'],
        verified_destination='+15550005777', reason='Fictional D1 reuse check'))
    assert linked['canonical_conversation_id'] == str(existing['id'])
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        after = cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s',
                            (seen['conversation_id'],)).fetchone()
        assert dict(after) == dict(before)
    fresh = identity_links.receive(d1['crm'], 'whatsapp', '15550005888', d1['key']())
    d1['conn'].execute("UPDATE soloestate.conversations SET status='resolved' WHERE lead_id=%s", (d1['lead']['id'],))
    created = identity_links.link(d1['crm'], s.IdentityLinkCreate(
        request_key=d1['key'](), link_id=fresh['record']['id'], lead_id=d1['lead']['id'],
        verified_destination='+15550005888', reason='Fictional D1 create check'))['canonical_conversation_id']
    assert created != str(existing['id'])
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        row = cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s', (created,)).fetchone()
        assert row['lead_id'] == UUID(d1['lead']['id']) and row['status'] == 'open'


def test_id10_concurrent_first_sightings_share_one_row(d1c):
    barrier, outcomes = Barrier(2), []
    def attempt():
        barrier.wait(timeout=120)
        status, result = _race_call(lambda: identity_links.receive(CRM(), 'whatsapp', '15550005999', d1c['key']()))
        outcomes.append((status, result))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: attempt(), range(2)))
    assert [o[0] for o in outcomes] == ['ok', 'ok']
    assert outcomes[0][1]['record']['id'] == outcomes[1][1]['record']['id']
    assert sorted(o[1]['created'] for o in outcomes) == [False, True]
    d1c['extra_leads'] = [outcomes[0][1]['lead_id']]
    with connect(Settings()) as conn:
        assert conn.execute("SELECT count(*) FROM soloestate.identity_links WHERE channel='whatsapp'"
                            " AND provider_ref='15550005999'").fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM soloestate.conversations WHERE lead_id=%s',
                            (outcomes[0][1]['lead_id'],)).fetchone()[0] == 1


# --- POL: messaging/consent policy and caps ---

def _opted_in_whatsapp_lead(b, phone):
    from app.journey import JourneyUpdate, update
    lead = b['crm'].detail('leads', b['lead']['id'])['record']
    update(b['crm'], UUID(lead['id']), JourneyUpdate(request_key=b['key'](), expected_version=lead['version'],
        phone=phone, whatsapp_permission='opted_in', reason='Fictional D1 WhatsApp consent'))
    with b['conn'].cursor(row_factory=dict_row) as cur:
        return cur.execute('SELECT * FROM soloestate.leads WHERE id=%s', (lead['id'],)).fetchone()


def test_pol01_stop_blocks_delivery_authorization(d1):
    phone = '+15550006666'
    lead = _opted_in_whatsapp_lead(d1, phone)
    seen = identity_links.receive(d1['crm'], 'whatsapp', '15550006666', d1['key']())
    link = identity_links.link(d1['crm'], s.IdentityLinkCreate(
        request_key=d1['key'](), link_id=seen['record']['id'], lead_id=lead['id'],
        verified_destination=phone, reason='Fictional D1 consent desk'))['record']
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        verdict = authorize_delivery(cur, channel='whatsapp', destination=phone, link=link, lead=lead,
                                     category='reminder', day=date.today(), cap=25)
        assert verdict['authorized'] is True and verdict['basis'] == 'link'
    pol.set_stop(d1['crm'], s.ControlsSet(request_key=d1['key'](), stopped=True, reason='Fictional D1 drill'))
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        blocked = authorize_delivery(cur, channel='whatsapp', destination=phone, link=link, lead=lead,
                                     category='reminder', day=date.today(), cap=25)
        assert blocked['authorized'] is False and 'emergency_stop' in blocked['reasons']


def test_pol05_authorization_refuses_each_unsafe_basis(d1):
    phone = '+15550007777'
    lead = _opted_in_whatsapp_lead(d1, phone)
    seen = identity_links.receive(d1['crm'], 'whatsapp', '15550007777', d1['key']())
    link = identity_links.link(d1['crm'], s.IdentityLinkCreate(
        request_key=d1['key'](), link_id=seen['record']['id'], lead_id=lead['id'],
        verified_destination=phone, reason='Fictional D1 consent desk'))['record']
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        sensitive = authorize_delivery(cur, channel='whatsapp', destination=phone, link=link, lead=lead,
                                       category='negotiation', day=date.today(), cap=25)
        assert sensitive['authorized'] is False and 'sensitive_category' in sensitive['reasons']
        cooldown = authorize_delivery(cur, channel='whatsapp', destination=phone, link=link, lead=lead,
                                      category='reminder', day=date.today(), cap=25, open_attempts=1)
        assert cooldown['authorized'] is False and 'cooldown_active' in cooldown['reasons']
        wrong = authorize_delivery(cur, channel='whatsapp', destination='+19995550000', link=link, lead=lead,
                                   category='reminder', day=date.today(), cap=25)
        assert wrong['authorized'] is False and 'destination_unauthorized' in wrong['reasons']
    identity_links.suppress(d1['crm'], s.SuppressionCreate(
        request_key=d1['key'](), channel='whatsapp', destination=phone, evidence_ref='Fictional D1 STOP note'))
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        suppressed = authorize_delivery(cur, channel='whatsapp', destination=phone, link=link, lead=lead,
                                        category='reminder', day=date.today(), cap=25)
        assert suppressed['authorized'] is False and 'suppressed' in suppressed['reasons']


def test_pol02_automation_policy_categories_and_enable_gate(d1):
    with pytest.raises(CRMError, match='Unknown message categories'):
        pol.create_automation_policy(d1['crm'], s.AutomationPolicyCreate(
            request_key=d1['key'](), kind='followup_nudge', days_after=2, after_event='no_visit_booked',
            categories=['bogus'], lead_statuses=['new'], wording='template'))
    created = pol.create_automation_policy(d1['crm'], s.AutomationPolicyCreate(
        request_key=d1['key'](), kind='followup_nudge', days_after=2, after_event='no_visit_booked',
        categories=['followup_routine'], lead_statuses=['new'], wording='template'))['record']
    assert created['enabled'] is False
    enabled = pol.update_automation_policy(d1['crm'], UUID(created['id']), s.AutomationPolicyUpdate(
        request_key=d1['key'](), expected_version=created['version'], enabled=True))['record']
    assert enabled['enabled'] is True
    bare = pol.create_automation_policy(d1['crm'], s.AutomationPolicyCreate(
        request_key=d1['key'](), kind='booking_reminder', hours_before=24, wording='template'))['record']
    with pytest.raises(CRMError, match='categories and lead statuses'):
        pol.update_automation_policy(d1['crm'], UUID(bare['id']), s.AutomationPolicyUpdate(
            request_key=d1['key'](), expected_version=bare['version'], enabled=True))


def test_pol03_cap_reservation_is_atomic_and_refundable(d1):
    today = date.today()
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        assert reserve_cap(cur, today, 'whatsapp', 2) is True
        assert reserve_cap(cur, today, 'whatsapp', 2) is True
        assert reserve_cap(cur, today, 'whatsapp', 2) is False
        assert scalar(cur.execute('SELECT used FROM soloestate.delivery_caps WHERE day=%s AND channel=%s',
                                (today, 'whatsapp')).fetchone()) == 2
        refund_cap(cur, today, 'whatsapp')
        assert reserve_cap(cur, today, 'whatsapp', 2) is True


def _raw_lead(b, lead_id):
    with b['conn'].cursor(row_factory=dict_row) as cur:
        return cur.execute('SELECT * FROM soloestate.leads WHERE id=%s', (lead_id,)).fetchone()


def _observed_whatsapp_thread(b, digits):
    seen = identity_links.receive(b['crm'], 'whatsapp', digits, b['key']())
    return seen['record'], _raw_lead(b, seen['lead_id']), seen['conversation_id']


def _deliver(b, **kw):
    args = dict(category='reminder', day=date.today(), cap=25)
    args.update(kw)
    with b['conn'].cursor(row_factory=dict_row) as cur:
        return authorize_delivery(cur, **args)


def _record_inbound(b, conversation_id, *, actor, role='customer', provider_ts=None, received_at=None,
                    channel=None, provider_ref=None, link_id=None):
    """Server-only synthetic authenticated-ingress evidence (tests only).

    Writes the persisted rows real ingress produces — an ingestion operation
    plus the linked message under the conversation lock with gapless seq —
    so authorization is tested against stored evidence, never a flag. When
    channel is given, the operation response carries the server-owned
    ingestion envelope {'inbound_evidence': {channel, provider_ref, link_id}}
    exactly as chat appends (website) and D2 webhooks (whatsapp) stamp it;
    omitting channel writes a legacy envelope-less row, which qualifies
    nothing. Channel rows also carry provider_ts when supplied. Visitor rows
    mirror chat appends. No production endpoint or client input can invoke this.
    """
    key, msg_id = b['key'](), uuid4()
    if actor == 'channel':
        action = 'channel:inbound'
        stamp = provider_ts.isoformat() if isinstance(provider_ts, datetime) else provider_ts
        response = {'provider_ts': stamp, 'synthetic': True}
    else:
        action = f'conversation:message:{conversation_id}'
        response = {'record': {'id': str(msg_id)}}
    if channel is not None:
        response['inbound_evidence'] = {'channel': channel, 'provider_ref': provider_ref,
                                        'link_id': str(link_id) if link_id is not None else None}
    with b['conn'].cursor(row_factory=dict_row) as cur:
        cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s FOR UPDATE', (conversation_id,))
        seq = cur.execute('SELECT coalesce(max(seq),0)+1 AS next FROM soloestate.conversation_messages'
                          ' WHERE conversation_id=%s', (conversation_id,)).fetchone()['next']
        cur.execute("""INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response)
            VALUES (%s,%s,%s,%s,%s,%s)""",
            (key, action, 'synthetic-authenticated-ingress', actor, msg_id, Json(response)))
        if received_at is None:
            cur.execute("""INSERT INTO soloestate.conversation_messages(id,conversation_id,seq,role,body,request_key)
                VALUES (%s,%s,%s,%s,%s,%s)""",
                (msg_id, conversation_id, seq, role, 'Fictional D1 ingress probe', key))
        else:
            cur.execute("""INSERT INTO soloestate.conversation_messages(id,conversation_id,seq,role,body,request_key,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (msg_id, conversation_id, seq, role, 'Fictional D1 ingress probe', key, received_at))
    return msg_id


def test_pol06_observed_in_thread_requires_full_bindings(d1):
    phone, digits = '+15550009111', '15550009111'
    link, iso, convo = _observed_whatsapp_thread(d1, digits)
    _record_inbound(d1, convo, actor='channel', channel='whatsapp', provider_ref=digits, link_id=link['id'])
    good = _deliver(d1, channel='whatsapp', destination=phone, link=link, lead=iso, conversation_id=convo)
    assert good['authorized'] is True and good['basis'] == 'in_thread'
    wrong_dest = _deliver(d1, channel='whatsapp', destination='+19995550000', link=link, lead=iso,
                          conversation_id=convo)
    assert wrong_dest['authorized'] is False and 'destination_unauthorized' in wrong_dest['reasons']
    wrong_channel = _deliver(d1, channel='website', destination=phone, link=link, lead=iso, conversation_id=convo)
    assert wrong_channel['authorized'] is False and 'destination_unauthorized' in wrong_channel['reasons']
    wrong_lead = _deliver(d1, channel='whatsapp', destination=phone, link=link, lead=_raw_lead(d1, d1['lead']['id']),
                          conversation_id=convo)
    assert wrong_lead['authorized'] is False and 'destination_unauthorized' in wrong_lead['reasons']
    _, _, other_convo = _observed_whatsapp_thread(d1, '15550009222')
    wrong_convo = _deliver(d1, channel='whatsapp', destination=phone, link=link, lead=iso, conversation_id=other_convo)
    assert wrong_convo['authorized'] is False and 'thread_mismatch' in wrong_convo['reasons']
    missing_convo = _deliver(d1, channel='whatsapp', destination=phone, link=link, lead=iso,
                             conversation_id=uuid4())
    assert missing_convo['authorized'] is False and 'thread_mismatch' in missing_convo['reasons']
    no_convo = _deliver(d1, channel='whatsapp', destination=phone, link=link, lead=iso)
    assert no_convo['authorized'] is False and 'thread_mismatch' in no_convo['reasons']


def test_pol07_stale_revoked_and_cross_lead_links_cannot_authorize(d1):
    phone, digits = '+15550009333', '15550009333'
    link, iso, convo = _observed_whatsapp_thread(d1, digits)
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        db_now = cur.execute('SELECT clock_timestamp()').fetchone()['clock_timestamp']
    _record_inbound(d1, convo, actor='channel', channel='whatsapp', provider_ref=digits, link_id=link['id'],
                    received_at=db_now - timedelta(hours=25))
    stale = _deliver(d1, channel='whatsapp', destination=phone, link=link, lead=iso, conversation_id=convo)
    assert stale['authorized'] is False and 'thread_expired' in stale['reasons']
    active = identity_links.link(d1['crm'], s.IdentityLinkCreate(
        request_key=d1['key'](), link_id=link['id'], lead_id=d1['lead']['id'],
        verified_destination=phone, reason='Fictional D1 binding probe'))['record']
    canon = _raw_lead(d1, d1['lead']['id'])
    superseded = _deliver(d1, channel='whatsapp', destination=phone, link=link, lead=iso, conversation_id=convo)
    assert superseded['authorized'] is False and 'destination_unauthorized' in superseded['reasons']
    revoked = identity_links.revoke(d1['crm'], UUID(active['id']), s.IdentityRevoke(
        request_key=d1['key'](), expected_version=active['version'], reason='Fictional D1 binding probe'))['record']
    assert revoked['status'] == 'revoked'
    gone = _deliver(d1, channel='whatsapp', destination=phone, link=revoked, lead=canon)
    assert gone['authorized'] is False and 'destination_unauthorized' in gone['reasons']
    decoy = d1['crm'].save('leads', s.LeadCreate(request_key=d1['key'](), name=d1['prefix'] + ' decoy',
        email=d1['prefix'] + 'decoy@example.invalid'))['record']
    from app.journey import JourneyUpdate, update
    update(d1['crm'], UUID(decoy['id']), JourneyUpdate(request_key=d1['key'](), expected_version=decoy['version'],
        phone=phone, whatsapp_permission='opted_in', reason='Fictional D1 decoy consent'))
    fresh_active = identity_links.link(d1['crm'], s.IdentityLinkCreate(
        request_key=d1['key'](), link_id=_observed_whatsapp_thread(d1, '15550009444')[0]['id'],
        lead_id=d1['lead']['id'], verified_destination='+15550009444',
        reason='Fictional D1 binding probe'))['record']
    scoped = _deliver(d1, channel='whatsapp', destination='+15550009444', link=fresh_active,
                      lead=_raw_lead(d1, decoy['id']))
    assert scoped['authorized'] is False and scoped['basis'] is None
    assert 'destination_unauthorized' in scoped['reasons']


def test_pol08_website_thread_and_gate_refusals_keep_valid_bindings(d1):
    seen = identity_links.receive(d1['crm'], 'website', 'web:pending', d1['key']())
    convo = seen['conversation_id']
    handle = 'web:' + str(convo)
    d1['conn'].execute('UPDATE soloestate.identity_links SET provider_ref=%s WHERE id=%s', (handle, seen['record']['id']))
    iso = _raw_lead(d1, seen['lead_id'])
    _record_inbound(d1, convo, actor='visitor', channel='website', provider_ref=handle,
                    link_id=seen['record']['id'])
    web = _deliver(d1, channel='website', destination=handle, link=seen['record'], lead=iso, conversation_id=convo)
    assert web['authorized'] is True and web['basis'] == 'in_thread'
    d1['conn'].execute('UPDATE soloestate.identity_links SET provider_ref=%s WHERE id=%s',
                       ('web:' + str(uuid4()), seen['record']['id']))
    diverged = _deliver(d1, channel='website', destination=handle, link=seen['record'], lead=iso,
                        conversation_id=convo)
    assert diverged['authorized'] is False and 'thread_mismatch' in diverged['reasons']
    phone, digits = '+15550009555', '15550009555'
    link, lead_iso, thread = _observed_whatsapp_thread(d1, digits)
    _record_inbound(d1, thread, actor='channel', channel='whatsapp', provider_ref=digits, link_id=link['id'])
    identity_links.suppress(d1['crm'], s.SuppressionCreate(
        request_key=d1['key'](), channel='whatsapp', destination=phone, evidence_ref='Fictional D1 STOP note'))
    stopped = _deliver(d1, channel='whatsapp', destination=phone, link=link, lead=lead_iso, conversation_id=thread)
    assert stopped['authorized'] is False and stopped['basis'] == 'in_thread'
    assert 'suppressed' in stopped['reasons']
    from app.journey import JourneyUpdate, update
    update(d1['crm'], lead_iso['id'], JourneyUpdate(request_key=d1['key'](), expected_version=lead_iso['version'],
        contact_permission='opted_out', reason='Fictional D1 opt-out probe'))
    opted = _deliver(d1, channel='whatsapp', destination=phone, link=link, lead=_raw_lead(d1, lead_iso['id']),
                     conversation_id=thread)
    assert opted['authorized'] is False and opted['basis'] == 'in_thread'
    assert 'contact_blocked' in opted['reasons']
    linked = identity_links.link(d1['crm'], s.IdentityLinkCreate(
        request_key=d1['key'](), link_id=link['id'], lead_id=d1['lead']['id'],
        verified_destination=phone, reason='Fictional D1 binding probe'))
    canon_convo = linked['canonical_conversation_id']
    canon = _raw_lead(d1, d1['lead']['id'])
    scoped_ok = _deliver(d1, channel='whatsapp', destination=phone, link=linked['record'], lead=canon,
                         conversation_id=canon_convo)
    assert scoped_ok['authorized'] is False and scoped_ok['basis'] == 'link'
    assert 'permission_required' in scoped_ok['reasons'] and 'thread_mismatch' not in scoped_ok['reasons']
    stray = _deliver(d1, channel='whatsapp', destination=phone, link=linked['record'], lead=canon,
                     conversation_id=thread)
    assert stray['authorized'] is False and stray['basis'] is None
    assert 'thread_mismatch' in stray['reasons']


def _db_now(b):
    with b['conn'].cursor(row_factory=dict_row) as cur:
        return cur.execute('SELECT clock_timestamp()').fetchone()['clock_timestamp']


def test_pol09_thread_window_requires_qualifying_inbound(d1):
    phone, digits = '+15550009666', '15550009666'
    link, iso, convo = _observed_whatsapp_thread(d1, digits)
    bare = _deliver(d1, channel='whatsapp', destination=phone, link=link, lead=iso, conversation_id=convo)
    assert bare['authorized'] is False and 'thread_expired' in bare['reasons']
    d1['conn'].execute('UPDATE soloestate.identity_links SET created_at=clock_timestamp()-make_interval(hours=>48)'
                       ' WHERE id=%s', (link['id'],))
    _record_inbound(d1, convo, actor='channel', channel='whatsapp', provider_ref=digits, link_id=link['id'])
    aged = _deliver(d1, channel='whatsapp', destination=phone, link=link, lead=iso, conversation_id=convo)
    assert aged['authorized'] is True and aged['basis'] == 'in_thread'
    link2, iso2, convo2 = _observed_whatsapp_thread(d1, '15550009777')
    _record_inbound(d1, convo2, actor='channel', channel='whatsapp', provider_ref='15550009777',
                    link_id=link2['id'], received_at=_db_now(d1) - timedelta(hours=25))
    old = _deliver(d1, channel='whatsapp', destination='+15550009777', link=link2, lead=iso2, conversation_id=convo2)
    assert old['authorized'] is False and 'thread_expired' in old['reasons']
    link3, iso3, convo3 = _observed_whatsapp_thread(d1, '15550009888')
    _record_inbound(d1, convo3, actor='channel', channel='whatsapp', provider_ref='15550009888',
                    link_id=link3['id'], received_at=_db_now(d1) - timedelta(hours=24))
    edge = _deliver(d1, channel='whatsapp', destination='+15550009888', link=link3, lead=iso3, conversation_id=convo3)
    assert edge['authorized'] is False and 'thread_expired' in edge['reasons']
    link4, iso4, convo4 = _observed_whatsapp_thread(d1, '15550009999')
    _record_inbound(d1, convo4, actor='channel', channel='whatsapp', provider_ref='15550009999',
                    link_id=link4['id'], received_at=_db_now(d1) - timedelta(hours=24, seconds=-60))
    near = _deliver(d1, channel='whatsapp', destination='+15550009999', link=link4, lead=iso4, conversation_id=convo4)
    assert near['authorized'] is True and near['basis'] == 'in_thread'
    link5, iso5, convo5 = _observed_whatsapp_thread(d1, '15550009011')
    from app.conversation import append as append_message
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        version = cur.execute('SELECT version FROM soloestate.conversations WHERE id=%s', (convo5,)).fetchone()['version']
    append_message(d1['crm'], convo5, s.MessageAppend(
        request_key=d1['key'](), expected_version=version, role='customer', body='Fictional owner-authored line'))
    forged = _deliver(d1, channel='whatsapp', destination='+15550009011', link=link5, lead=iso5, conversation_id=convo5)
    assert forged['authorized'] is False and 'thread_expired' in forged['reasons']
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        rows = cur.execute("SELECT role FROM soloestate.conversation_messages WHERE conversation_id=%s", (convo5,)).fetchall()
    assert [row['role'] for row in rows] == ['customer']
    link6, iso6, convo6 = _observed_whatsapp_thread(d1, '15550009012')
    for role in ('assistant', 'owner', 'system'):
        _record_inbound(d1, convo6, actor='channel', role=role, channel='whatsapp',
                        provider_ref='15550009012', link_id=link6['id'])
    mute = _deliver(d1, channel='whatsapp', destination='+15550009012', link=link6, lead=iso6, conversation_id=convo6)
    assert mute['authorized'] is False and 'thread_expired' in mute['reasons']
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        count = cur.execute('SELECT count(*) FROM soloestate.conversation_messages WHERE conversation_id=%s',
                            (convo6,)).fetchone()['count']
    assert count == 3


def test_pol10_provider_event_time_governs_window(d1):
    now = _db_now(d1)
    link, iso, convo = _observed_whatsapp_thread(d1, '15550009101')
    _record_inbound(d1, convo, actor='channel', channel='whatsapp', provider_ref='15550009101',
                    link_id=link['id'], provider_ts=now - timedelta(hours=25))
    late = _deliver(d1, channel='whatsapp', destination='+15550009101', link=link, lead=iso, conversation_id=convo)
    assert late['authorized'] is False and 'thread_expired' in late['reasons']
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        evidence = cur.execute("""SELECT m.created_at AS received_at, o.response AS response
            FROM soloestate.conversation_messages m JOIN soloestate.crm_operations o ON o.request_key=m.request_key
            WHERE m.conversation_id=%s""", (convo,)).fetchone()
    assert evidence['received_at'] > now - timedelta(minutes=5)
    assert evidence['response']['provider_ts'] == (now - timedelta(hours=25)).isoformat()
    link2, iso2, convo2 = _observed_whatsapp_thread(d1, '15550009102')
    _record_inbound(d1, convo2, actor='channel', channel='whatsapp', provider_ref='15550009102',
                    link_id=link2['id'], provider_ts=now - timedelta(hours=1))
    fresh = _deliver(d1, channel='whatsapp', destination='+15550009102', link=link2, lead=iso2, conversation_id=convo2)
    assert fresh['authorized'] is True and fresh['basis'] == 'in_thread'
    link3, iso3, convo3 = _observed_whatsapp_thread(d1, '15550009103')
    _record_inbound(d1, convo3, actor='channel', channel='whatsapp', provider_ref='15550009103',
                    link_id=link3['id'], provider_ts=now + timedelta(hours=1))
    future_event = _deliver(d1, channel='whatsapp', destination='+15550009103', link=link3, lead=iso3,
                            conversation_id=convo3)
    assert future_event['authorized'] is False and 'thread_expired' in future_event['reasons']
    link4, iso4, convo4 = _observed_whatsapp_thread(d1, '15550009104')
    _record_inbound(d1, convo4, actor='channel', channel='whatsapp', provider_ref='15550009104',
                    link_id=link4['id'], received_at=now + timedelta(hours=1))
    future_receipt = _deliver(d1, channel='whatsapp', destination='+15550009104', link=link4, lead=iso4,
                              conversation_id=convo4)
    assert future_receipt['authorized'] is False and 'thread_expired' in future_receipt['reasons']
    link5, iso5, convo5 = _observed_whatsapp_thread(d1, '15550009105')
    _record_inbound(d1, convo5, actor='channel', channel='whatsapp', provider_ref='15550009105',
                    link_id=link5['id'], provider_ts='not-a-time')
    garbled = _deliver(d1, channel='whatsapp', destination='+15550009105', link=link5, lead=iso5, conversation_id=convo5)
    assert garbled['authorized'] is False and 'thread_expired' in garbled['reasons']
    link6, iso6, convo6 = _observed_whatsapp_thread(d1, '15550009106')
    _record_inbound(d1, convo6, actor='channel', channel='whatsapp', provider_ref='15550009106',
                    link_id=link6['id'],
                    provider_ts=(now - timedelta(hours=1)).replace(tzinfo=None).isoformat())
    naive = _deliver(d1, channel='whatsapp', destination='+15550009106', link=link6, lead=iso6, conversation_id=convo6)
    assert naive['authorized'] is True and naive['basis'] == 'in_thread'


def test_pol11_suppression_survives_link_churn(d1):
    phone, digits = '+15550009223', '15550009223'
    identity_links.suppress(d1['crm'], s.SuppressionCreate(
        request_key=d1['key'](), channel='whatsapp', destination=phone, evidence_ref='Fictional D1 prior STOP'))
    seen = identity_links.receive(d1['crm'], 'whatsapp', digits, d1['key']())
    active = identity_links.link(d1['crm'], s.IdentityLinkCreate(
        request_key=d1['key'](), link_id=seen['record']['id'], lead_id=d1['lead']['id'],
        verified_destination=phone, reason='Fictional D1 link one'))['record']
    identity_links.revoke(d1['crm'], UUID(active['id']), s.IdentityRevoke(
        request_key=d1['key'](), expected_version=active['version'], reason='Fictional D1 churn'))
    again = identity_links.receive(d1['crm'], 'whatsapp', digits, d1['key']())
    assert again['created'] is True and again['record']['id'] != active['id']
    relinked = identity_links.link(d1['crm'], s.IdentityLinkCreate(
        request_key=d1['key'](), link_id=again['record']['id'], lead_id=d1['lead']['id'],
        verified_destination=phone, reason='Fictional D1 link two'))['record']
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        assert scalar(cur.execute('SELECT count(*) FROM soloestate.suppression_list WHERE channel=%s AND destination=%s',
                                ('whatsapp', phone)).fetchone()) == 1
        verdict = authorize_delivery(cur, channel='whatsapp', destination=phone, link=relinked,
                                     lead=_raw_lead(d1, d1['lead']['id']), category='reminder',
                                     day=date.today(), cap=25)
        assert verdict['authorized'] is False and 'suppressed' in verdict['reasons']


def test_pol12_website_inbound_cannot_authorize_whatsapp(d1):
    phone, digits = '+15550009341', '15550009341'
    link, iso, convo = _observed_whatsapp_thread(d1, digits)
    _record_inbound(d1, convo, actor='visitor', channel='website',
                    provider_ref='web:' + str(convo), link_id=None)
    foreign = _deliver(d1, channel='whatsapp', destination=phone, link=link, lead=iso, conversation_id=convo)
    assert foreign['authorized'] is False and 'thread_expired' in foreign['reasons']
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        stored = scalar(cur.execute(
            'SELECT o.response FROM soloestate.conversation_messages m '
            'JOIN soloestate.crm_operations o ON o.request_key=m.request_key '
            'WHERE m.conversation_id=%s', (convo,)).fetchone())
    assert stored['inbound_evidence'] == {'channel': 'website', 'provider_ref': 'web:' + str(convo),
                                          'link_id': None}
    _record_inbound(d1, convo, actor='channel', channel='whatsapp', provider_ref=digits, link_id=link['id'])
    own = _deliver(d1, channel='whatsapp', destination=phone, link=link, lead=iso, conversation_id=convo)
    assert own['authorized'] is True and own['basis'] == 'in_thread'


def test_pol13_sender_evidence_is_sender_bound(d1):
    phone_a, digits_a = '+15550009351', '15550009351'
    phone_b, digits_b = '+15550009352', '15550009352'
    link_a, iso_a, convo_a = _observed_whatsapp_thread(d1, digits_a)
    link_b, _, _ = _observed_whatsapp_thread(d1, digits_b)
    _record_inbound(d1, convo_a, actor='channel', channel='whatsapp',
                    provider_ref=digits_b, link_id=link_b['id'])
    crossed = _deliver(d1, channel='whatsapp', destination=phone_a, link=link_a, lead=iso_a,
                       conversation_id=convo_a)
    assert crossed['authorized'] is False and 'thread_expired' in crossed['reasons']
    _record_inbound(d1, convo_a, actor='channel', channel='whatsapp',
                    provider_ref=digits_a, link_id=link_a['id'])
    own = _deliver(d1, channel='whatsapp', destination=phone_a, link=link_a, lead=iso_a,
                   conversation_id=convo_a)
    assert own['authorized'] is True and own['basis'] == 'in_thread'
    iso_b = _raw_lead(d1, link_b['lead_id'])
    stray = _deliver(d1, channel='whatsapp', destination=phone_b, link=link_b, lead=iso_b,
                     conversation_id=convo_a)
    assert stray['authorized'] is False and 'thread_mismatch' in stray['reasons']


def test_pol14_wrong_thread_and_stale_link_evidence_rejected(d1):
    phone, digits = '+15550009361', '15550009361'
    link, iso, convo = _observed_whatsapp_thread(d1, digits)
    _record_inbound(d1, convo, actor='channel', channel='whatsapp', provider_ref=digits, link_id=link['id'])
    sane = _deliver(d1, channel='whatsapp', destination=phone, link=link, lead=iso, conversation_id=convo)
    assert sane['authorized'] is True and sane['basis'] == 'in_thread'
    other = d1['conn'].execute(
        'INSERT INTO soloestate.conversations(id,lead_id,property_id) VALUES (%s,%s,%s) RETURNING id',
        (uuid4(), iso['id'], None)).fetchone()[0]
    wrong = _deliver(d1, channel='whatsapp', destination=phone, link=link, lead=iso, conversation_id=other)
    assert wrong['authorized'] is False and 'thread_expired' in wrong['reasons']
    _record_inbound(d1, other, actor='channel', channel='whatsapp', provider_ref=digits, link_id=uuid4())
    stale = _deliver(d1, channel='whatsapp', destination=phone, link=link, lead=iso, conversation_id=other)
    assert stale['authorized'] is False and 'thread_expired' in stale['reasons']
    _record_inbound(d1, other, actor='channel')
    legacy = _deliver(d1, channel='whatsapp', destination=phone, link=link, lead=iso, conversation_id=other)
    assert legacy['authorized'] is False and 'thread_expired' in legacy['reasons']


def test_pol15_revoked_generation_evidence_cannot_authorize_rebinding(d1):
    phone, digits = '+15550009371', '15550009371'
    seen = identity_links.receive(d1['crm'], 'whatsapp', digits, d1['key']())
    gen1, thread = seen['record'], seen['conversation_id']
    _record_inbound(d1, thread, actor='channel', channel='whatsapp', provider_ref=digits, link_id=gen1['id'])
    before = _deliver(d1, channel='whatsapp', destination=phone, link=gen1,
                      lead=_raw_lead(d1, seen['lead_id']), conversation_id=thread)
    assert before['authorized'] is True and before['basis'] == 'in_thread'
    active = identity_links.link(d1['crm'], s.IdentityLinkCreate(
        request_key=d1['key'](), link_id=gen1['id'], lead_id=d1['lead']['id'],
        verified_destination=phone, reason='Fictional D1 generation churn'))['record']
    identity_links.revoke(d1['crm'], UUID(active['id']), s.IdentityRevoke(
        request_key=d1['key'](), expected_version=active['version'], reason='Fictional D1 generation churn'))
    gen3 = identity_links.receive(d1['crm'], 'whatsapp', digits, d1['key']())['record']
    assert gen3['id'] != gen1['id']
    # Synthetic reassignment: the old thread is reattached to the new generation's
    # lead, modelling a D2 routing fault. The gen1-bound evidence must still refuse.
    d1['conn'].execute('UPDATE soloestate.conversations SET lead_id=%s WHERE id=%s', (gen3['lead_id'], thread))
    reused = _deliver(d1, channel='whatsapp', destination=phone, link=gen3,
                      lead=_raw_lead(d1, gen3['lead_id']), conversation_id=thread)
    assert reused['authorized'] is False and 'thread_expired' in reused['reasons']
    _record_inbound(d1, thread, actor='channel', channel='whatsapp', provider_ref=digits, link_id=gen3['id'])
    fresh = _deliver(d1, channel='whatsapp', destination=phone, link=gen3,
                     lead=_raw_lead(d1, gen3['lead_id']), conversation_id=thread)
    assert fresh['authorized'] is True and fresh['basis'] == 'in_thread'


def test_pol16_visitor_append_stamps_channel_evidence(d1):
    from app.chat import RATE_LIMIT_PER_HOUR
    from app.conversation import append as append_message
    seen = identity_links.receive(d1['crm'], 'website', 'web:pending', d1['key']())
    convo = seen['conversation_id']
    handle = 'web:' + str(convo)
    d1['conn'].execute('UPDATE soloestate.identity_links SET provider_ref=%s WHERE id=%s',
                       (handle, seen['record']['id']))
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        version = cur.execute('SELECT version FROM soloestate.conversations WHERE id=%s', (convo,)).fetchone()['version']
    key = d1['key']()
    append_message(d1['crm'], convo, s.MessageAppend(
        request_key=key, expected_version=version, role='customer', body='Fictional visitor line'),
        actor='visitor', rate_limit_per_hour=RATE_LIMIT_PER_HOUR, visitor_send=True)
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        stored = scalar(cur.execute('SELECT response FROM soloestate.crm_operations WHERE request_key=%s',
                                    (key,)).fetchone())
    assert stored['inbound_evidence'] == {'channel': 'website', 'provider_ref': handle,
                                          'link_id': seen['record']['id']}
    homepage = _observed_whatsapp_thread(d1, '15550009381')[2]
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        version = cur.execute('SELECT version FROM soloestate.conversations WHERE id=%s', (homepage,)).fetchone()['version']
    unlinked = d1['key']()
    append_message(d1['crm'], homepage, s.MessageAppend(
        request_key=unlinked, expected_version=version, role='customer', body='Fictional visitor line'),
        actor='visitor', rate_limit_per_hour=RATE_LIMIT_PER_HOUR, visitor_send=True)
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        bare = scalar(cur.execute('SELECT response FROM soloestate.crm_operations WHERE request_key=%s',
                                  (unlinked,)).fetchone())
    assert bare['inbound_evidence'] == {'channel': 'website', 'provider_ref': 'web:' + str(homepage),
                                        'link_id': None}


# --- LEG: legacy compatibility ---

def _tool_run(b):
    run = uuid4()
    b['conn'].execute("INSERT INTO soloestate.runs(id,request_key,goal,status) VALUES (%s,%s,'Fictional D1 legacy branch','pending')",
                      (run, b['key']()))
    return BusinessTools(b['crm'].connect), ToolContext(run_id=run)


def test_leg_v1_approval_books_owner_and_replays_identical(d1):
    run = uuid4()
    d1['conn'].execute("INSERT INTO soloestate.runs(id,request_key,goal,status) VALUES (%s,%s,'Fictional D1 legacy replay','pending')",
                       (run, d1['key']()))
    tools, ctx = BusinessTools(d1['crm'].connect), ToolContext(run_id=run)
    start, end = clean_window(d1['conn'], weekday=3, hour=11)
    slot = mkslot(d1, start, end)
    args = dict(kind='booking', lead_id=d1['lead']['id'], property_id=d1['prop']['id'], slot_id=slot['id'],
                idempotency_key=d1['key']())
    first = tools.execute('request_approval', args, ctx).data['approval']
    again = tools.execute('request_approval', args, ctx).data['approval']
    assert again['id'] == first['id']
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        payload = scalar(cur.execute('SELECT payload FROM soloestate.approvals WHERE id=%s', (first['id'],)).fetchone())
        assert payload.get('snapshot_version', 1) == 1 and 'resource_id' not in payload
    owner = OwnerService(d1['crm'].connect)
    owner.decide(first['id'], DecisionRequest(decision='approved', expected_payload_hash=owner.review(first['id'])['payload_hash'],
                                              idempotency_key=d1['key']()))
    booked = tools.execute('book_site_visit', dict(approval_id=first['id'], idempotency_key=d1['key']()), ctx)
    assert booked.status == 'succeeded' and booked.data['booking']['resource_id'] == str(OWNER_RESOURCE_ID)


def test_leg_v2_approval_pins_resource(d1):
    start, end = clean_window(d1['conn'], weekday=4, hour=11)
    slot = mkslot(d1, start, end, resource_id=d1['agent']['id'])
    booked = tool_book(d1, slot['id'], resource_id=d1['agent']['id'])
    assert booked.status == 'succeeded' and booked.data['booking']['resource_id'] == d1['agent']['id']
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        payload = scalar(cur.execute('SELECT payload FROM soloestate.approvals WHERE lead_id=%s ORDER BY created_at DESC LIMIT 1',
                                   (d1['lead']['id'],)).fetchone())
        assert payload['snapshot_version'] == 2 and payload['resource_id'] == d1['agent']['id']
    tools, ctx = _tool_run(d1)
    fresh = mkslot(d1, *clean_window(d1['conn'], weekday=5, hour=14), resource_id=d1['agent']['id'])
    args = dict(kind='booking', lead_id=d1['lead']['id'], property_id=d1['prop']['id'], slot_id=fresh['id'],
                resource_id=str(d1['agent']['id']), idempotency_key=d1['key']())
    one = tools.execute('request_approval', args, ctx).data['approval']['id']
    assert tools.execute('request_approval', args, ctx).data['approval']['id'] == one


def test_leg08_v1_key_with_v2_args_conflicts_without_writes(d1):
    slot = mkslot(d1, *clean_window(d1['conn'], weekday=3, hour=11))
    tools, ctx = _tool_run(d1)
    key = d1['key']()
    base = dict(kind='booking', lead_id=d1['lead']['id'], property_id=d1['prop']['id'], slot_id=slot['id'],
                idempotency_key=key)
    first = tools.execute('request_approval', base, ctx).data['approval']['id']
    conflicted = tools.execute('request_approval', dict(base, resource_id=str(d1['agent']['id'])), ctx)
    assert conflicted.status == 'error' and conflicted.code == 'idempotency_conflict'
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        assert scalar(cur.execute('SELECT count(*) FROM soloestate.approvals WHERE lead_id=%s',
                                (d1['lead']['id'],)).fetchone()) == 1
        payload = scalar(cur.execute('SELECT payload FROM soloestate.approvals WHERE id=%s', (first,)).fetchone())
        assert payload.get('snapshot_version', 1) == 1 and 'resource_id' not in payload
    owner_key = d1['key']()
    d1['crm'].prepare(s.AppointmentRequest(request_key=owner_key, lead_id=d1['lead']['id'],
        property_id=d1['prop']['id'], slot_id=slot['id']))
    with pytest.raises(CRMError, match='different operation'):
        d1['crm'].prepare(s.AppointmentRequest(request_key=owner_key, lead_id=d1['lead']['id'],
            property_id=d1['prop']['id'], slot_id=slot['id'], resource_id=d1['agent']['id']))
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        assert scalar(cur.execute('SELECT count(*) FROM soloestate.crm_operations WHERE request_key=%s',
                                (owner_key,)).fetchone()) == 1
        assert scalar(cur.execute('SELECT count(*) FROM soloestate.approvals WHERE lead_id=%s',
                                (d1['lead']['id'],)).fetchone()) == 2


def test_leg14_v2_key_changed_resource_conflicts(d1):
    slot = mkslot(d1, *clean_window(d1['conn'], weekday=4, hour=11))
    tools, ctx = _tool_run(d1)
    key = d1['key']()
    base = dict(kind='booking', lead_id=d1['lead']['id'], property_id=d1['prop']['id'], slot_id=slot['id'],
                resource_id=str(OWNER_RESOURCE_ID), idempotency_key=key)
    first = tools.execute('request_approval', base, ctx).data['approval']['id']
    conflicted = tools.execute('request_approval', dict(base, resource_id=str(d1['agent']['id'])), ctx)
    assert conflicted.status == 'error' and conflicted.code == 'idempotency_conflict'
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        assert scalar(cur.execute('SELECT count(*) FROM soloestate.approvals WHERE lead_id=%s',
                                (d1['lead']['id'],)).fetchone()) == 1
        payload = scalar(cur.execute('SELECT payload FROM soloestate.approvals WHERE id=%s', (first,)).fetchone())
        assert payload['snapshot_version'] == 2 and payload['resource_id'] == str(OWNER_RESOURCE_ID)
    owner_key = d1['key']()
    d1['crm'].prepare(s.AppointmentRequest(request_key=owner_key, lead_id=d1['lead']['id'],
        property_id=d1['prop']['id'], slot_id=slot['id'], resource_id=OWNER_RESOURCE_ID))
    with pytest.raises(CRMError, match='different operation'):
        d1['crm'].prepare(s.AppointmentRequest(request_key=owner_key, lead_id=d1['lead']['id'],
            property_id=d1['prop']['id'], slot_id=slot['id'], resource_id=d1['agent']['id']))
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        assert scalar(cur.execute('SELECT count(*) FROM soloestate.crm_operations WHERE request_key=%s',
                                (owner_key,)).fetchone()) == 1
        assert scalar(cur.execute('SELECT count(*) FROM soloestate.approvals WHERE lead_id=%s',
                                (d1['lead']['id'],)).fetchone()) == 2


def test_leg12_mixed_version_session_branches_deterministically(d1):
    tools, ctx = _tool_run(d1)
    one = mkslot(d1, *clean_window(d1['conn'], weekday=4, hour=14))
    two = mkslot(d1, *clean_window(d1['conn'], weekday=5, hour=11), resource_id=d1['agent']['id'])
    v1_key = d1['key']()
    v1_args = dict(kind='booking', lead_id=d1['lead']['id'], property_id=d1['prop']['id'], slot_id=one['id'],
                   idempotency_key=v1_key)
    replayed = tools.execute('request_approval', v1_args, ctx).data['approval']['id']
    assert tools.execute('request_approval', v1_args, ctx).data['approval']['id'] == replayed
    v2_args = dict(kind='booking', lead_id=d1['lead']['id'], property_id=d1['prop']['id'], slot_id=two['id'],
                   resource_id=str(d1['agent']['id']), idempotency_key=d1['key']())
    created = tools.execute('request_approval', v2_args, ctx).data['approval']['id']
    assert created != replayed
    conflicted = tools.execute('request_approval', dict(v1_args, slot_id=two['id']), ctx)
    assert conflicted.status == 'error' and conflicted.code == 'idempotency_conflict'
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        assert scalar(cur.execute('SELECT count(*) FROM soloestate.approvals WHERE lead_id=%s',
                                (d1['lead']['id'],)).fetchone()) == 2


def test_leg_appointment_detail_carries_resource_and_result(d1):
    booking = owner_book(d1, mkslot(d1, *clean_window(d1['conn'], weekday=5, hour=11))['id'])
    detail = d1['crm'].detail('appointments', booking['id'])['record']
    assert detail['resource_id'] == str(OWNER_RESOURCE_ID) and detail['visit_result'] == 'scheduled'


def test_leg_journey_consent_syncs_suppression(d1):
    from app.journey import JourneyUpdate, update
    phone = '+15550008888'
    identity_links.suppress(d1['crm'], s.SuppressionCreate(
        request_key=d1['key'](), channel='whatsapp', destination=phone, evidence_ref='Fictional D1 prior STOP'))
    lead = d1['crm'].detail('leads', d1['lead']['id'])['record']
    update(d1['crm'], UUID(lead['id']), JourneyUpdate(request_key=d1['key'](), expected_version=lead['version'],
        phone=phone, whatsapp_permission='opted_in', reason='Fictional D1 fresh consent'))
    assert d1['conn'].execute('SELECT count(*) FROM soloestate.suppression_list WHERE channel=%s AND destination=%s',
                              ('whatsapp', phone)).fetchone()[0] == 0
    lead = d1['crm'].detail('leads', d1['lead']['id'])['record']
    update(d1['crm'], UUID(lead['id']), JourneyUpdate(request_key=d1['key'](), expected_version=lead['version'],
        whatsapp_permission='opted_out', reason='Fictional D1 consent withdrawn'))
    assert d1['conn'].execute('SELECT count(*) FROM soloestate.suppression_list WHERE channel=%s AND destination=%s',
                              ('whatsapp', phone)).fetchone()[0] == 1


def test_leg_committed_booking_takes_outcome_and_resists_cancel(d1):
    from app.visits import record_outcome
    row = propose(d1, *clean_window(d1['conn'], weekday=3, hour=11))['record']
    booking_id = commit_req(d1, row)['booking_id']
    booking = d1['crm'].detail('appointments', booking_id)['record']
    updated = record_outcome(d1['crm'], UUID(booking_id), s.OutcomeUpdate(
        request_key=d1['key'](), expected_version=booking['version'], visit_outcome='completed',
        outcome_note='Fictional D1 walkthrough finished', followup_state='pending',
        followup_note='Fictional D1 send quotation', reason='Fictional D1 visit wrap-up'))['record']
    assert updated['visit_outcome'] == 'completed' and updated['followup_state'] == 'pending'
    assert d1['crm'].detail('appointments', booking_id)['record']['visit_result'] == 'completed'
    with pytest.raises(CRMError, match='recorded outcome'):
        d1['crm'].cancel(UUID(booking_id), s.Cancel(request_key=d1['key'](), expected_version=updated['version'],
                                                   reason='Fictional D1 late cancel'))


def test_leg_duplicate_slot_row_rejected_per_resource(d1):
    start, end = clean_window(d1['conn'], weekday=4, hour=14)
    mkslot(d1, start, end)
    with pytest.raises(CRMError, match='Overlapping slot'):
        mkslot(d1, start, end)
    assert mkslot(d1, start, end, resource_id=d1['agent']['id'])['resource_id'] == d1['agent']['id']


# --- MIG: migration 0011 ledger and seeds ---

def test_mig01_policy_migration_applied_with_seeded_defaults(d1):
    assert d1['conn'].execute("SELECT count(*) FROM soloestate.schema_migrations WHERE version=%s",
                              ('0011_policy_resources_identity.sql',)).fetchone()[0] == 1
    with d1['conn'].cursor(row_factory=dict_row) as cur:
        policy = cur.execute("SELECT * FROM soloestate.business_policies WHERE id='default'").fetchone()
        assert policy['timezone'] == 'Asia/Kolkata' and policy['notice_hours'] == 16
        assert policy['default_duration_min'] == 30 and policy['weekly_holiday'] == 2
        assert scalar(cur.execute("SELECT emergency_stop FROM soloestate.automation_controls WHERE id='default'").fetchone()) is False
        owner = cur.execute('SELECT * FROM soloestate.resources WHERE id=%s', (str(OWNER_RESOURCE_ID),)).fetchone()
        assert owner['name'] == 'Owner' and owner['active'] is True
        tables = ('business_policies', 'resources', 'automation_controls', 'automation_policies',
                  'booking_requests', 'identity_links', 'suppression_list', 'delivery_caps')
        for table in tables:
            assert scalar(cur.execute('SELECT rowsecurity FROM pg_tables WHERE schemaname=%s AND tablename=%s',
                                    ('soloestate', table)).fetchone()) is True


def test_leg_d1_read_endpoints_serve_envelopes(d1):
    assert pol.get_policy(d1['crm'])['record']['id'] == 'default'
    assert 'emergency_stop' in pol.get_controls(d1['crm'])['record']
    assert resources.listing(d1['crm'])['total'] >= 2
    row = propose(d1, *clean_window(d1['conn'], weekday=3, hour=11))['record']
    assert booking_requests.listing(d1['crm'], lead_id=d1['lead']['id'])['total'] == 1
    assert booking_requests.detail(d1['crm'], UUID(row['id']))['record']['id'] == row['id']
    identity_links.receive(d1['crm'], 'whatsapp', '15550009999', d1['key']())
    assert identity_links.listing(d1['crm'])['total'] >= 1
    identity_links.suppress(d1['crm'], s.SuppressionCreate(request_key=d1['key'](), channel='whatsapp',
        destination='+15550009999', evidence_ref='Fictional D1 read check'))
    assert identity_links.suppression_listing(d1['crm'])['total'] >= 1
    assert pol.list_automation_policies(d1['crm'])['total'] >= 0
