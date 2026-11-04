"""D2 submission-time business-fact revalidation tests (final correction).

Staged bodies are rendered from pinned business facts; the staged ->
submitting transition must revalidate those facts and refuse to submit a
stale body. Rollback fixtures by default; the concurrency case uses
committed rows with exact-ID cleanup.
"""
import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Thread
from uuid import uuid4

import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app import crm_schemas as s
from app import dispatch, receptionist
from app.catalog import CATALOG_VERSION
from app.config import Settings
from app.crm import CRM
from app.database import connect
from app.services.meta_model import ModelResult

pytestmark = pytest.mark.d2_database


class StubModel:
    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = 0

    async def generate(self, prompt):
        self.calls += 1
        return ModelResult(response_id='resp-%d' % self.calls, model='test-meta',
                           text=self.texts[min(self.calls - 1, len(self.texts) - 1)])


class StubSubmit:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, recipient, text):
        self.calls.append((recipient, text))
        outcome = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _enable(b, categories=None):
    from app.policies import ROUTINE_CATEGORIES, update_reply_policy
    with b['conn'].cursor() as cur:
        version = cur.execute("SELECT version FROM soloestate.immediate_reply_policies WHERE id='whatsapp'").fetchone()[0]
    return update_reply_policy(b['crm'], s.ReplyPolicyUpdate(
        request_key=b['key'](), expected_version=version, enabled=True,
        categories=list(categories if categories is not None else ROUTINE_CATEGORIES),
        reason='Fictional D2 test enable'))


@pytest.fixture
def fx(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    with connect(Settings()) as conn:
        with conn.transaction(force_rollback=True):
            @contextmanager
            def factory():
                with conn.transaction():
                    yield conn
            prefix = 'd2fx-' + str(uuid4())
            b = dict(crm=CRM(factory), conn=conn, factory=factory, prefix=prefix,
                     key=lambda: prefix + ':' + str(uuid4()))
            _enable(b)
            yield b


def _inbound(b, sender, body='Fictional hello'):
    from app import identity_links
    now = datetime.now(timezone.utc)
    with b['conn'].cursor(row_factory=dict_row) as cur:
        link, _ = identity_links.observe(cur, 'whatsapp', sender, b['key']())
        convo = cur.execute('SELECT * FROM soloestate.conversations WHERE lead_id=%s ORDER BY created_at DESC LIMIT 1',
                            (link['lead_id'],)).fetchone()
        wamid = 'wamid.FAC' + uuid4().hex[:12]
        op_key = 'wa:inbound:' + wamid
        response = {'provider_ts': now.isoformat(),
                    'inbound_evidence': {'channel': 'whatsapp', 'provider_ref': sender,
                                         'link_id': str(link['id'])}}
        cur.execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) '
                    'VALUES (%s,%s,%s,%s,%s,%s)',
                    (op_key, 'channel:inbound', 'wa:' + wamid, 'channel', link['id'], Jsonb(response)))
        seq = cur.execute('SELECT coalesce(max(seq),0)+1 AS next FROM soloestate.conversation_messages '
                          'WHERE conversation_id=%s', (convo['id'],)).fetchone()['next']
        message = cur.execute('INSERT INTO soloestate.conversation_messages(id,conversation_id,seq,role,body,request_key) '
                              'VALUES (%s,%s,%s,%s,%s,%s) RETURNING *',
                              (uuid4(), convo['id'], seq, 'customer', body, op_key)).fetchone()
        cur.execute("UPDATE soloestate.conversations SET channel='whatsapp' WHERE id=%s", (convo['id'],))
    with b['conn'].cursor(row_factory=dict_row) as cur:
        lead = cur.execute('SELECT * FROM soloestate.leads WHERE id=%s', (link['lead_id'],)).fetchone()
        convo = cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s', (convo['id'],)).fetchone()
    return dict(link=link, lead=lead, convo=convo, message=message, wamid=wamid)


def _outbox(b, message_id):
    with b['conn'].cursor(row_factory=dict_row) as cur:
        return cur.execute('SELECT * FROM soloestate.delivery_outbox WHERE source_id=%s',
                           (str(message_id),)).fetchone()


def _outbox_by_id(b, outbox_id):
    with b['conn'].cursor(row_factory=dict_row) as cur:
        return cur.execute('SELECT * FROM soloestate.delivery_outbox WHERE id=%s', (outbox_id,)).fetchone()


def _attempts(b, outbox_id):
    with b['conn'].cursor(row_factory=dict_row) as cur:
        return cur.execute('SELECT * FROM soloestate.delivery_attempts WHERE outbox_id=%s ORDER BY attempt_no',
                           (outbox_id,)).fetchall()


def _used(b, channel='whatsapp'):
    with b['conn'].cursor() as cur:
        row = cur.execute('SELECT used FROM soloestate.delivery_caps WHERE channel=%s ORDER BY day DESC LIMIT 1',
                          (channel,)).fetchone()
        return row[0] if row else 0


def _out(category, segments, **extra):
    return json.dumps(dict(catalog_version=CATALOG_VERSION, category=category,
                           segments=segments, **extra))


def _q(qid, args=None):
    return dict(question_id=qid, args=args or {})


def _prop(b, **kw):
    base = dict(request_key=b['key'](), title=b['prefix'], locality=b['prefix'], price=150, bedrooms=2)
    base.update(kw)
    return b['crm'].save('properties', s.PropertyCreate(**base))['record']


def _owner_save_property(b, prop_id, **changes):
    with b['conn'].cursor(row_factory=dict_row) as cur:
        row = cur.execute('SELECT * FROM soloestate.properties WHERE id=%s', (prop_id,)).fetchone()
    values = dict(request_key=b['key'](), title=row['title'], locality=row['locality'],
                  price=row['price'], bedrooms=row['bedrooms'], description=row['description'] or '',
                  active=row['active'], concurrent_visit_capacity=row['concurrent_visit_capacity'],
                  expected_version=row['version'])
    values.update(changes)
    return b['crm'].save('properties', s.PropertyUpdate(**values), prop_id)['record']


def _stage_recommendation(b, sender):
    """Production turn: completing requirements stages a REC_MATCHES body
    citing the one matching property (price 150)."""
    box = _inbound(b, sender, 'We need a 2 bedroom home')
    prop = _prop(b)
    reply = receptionist.respond_turn(
        b['factory'], box['convo']['id'], box['message']['id'],
        model=StubModel([_out('qualification', [_q('Q_BHK')], requirements={
            'preferred_localities': [b['prefix']], 'budget_min': '100', 'budget_max': '200',
            'bedrooms': 2, 'purchase_timeframe': 'within_3_months'})]))
    assert reply['replied'] is True and 'current matches' in reply['payload'], reply
    outbox = _outbox(b, box['message']['id'])
    assert outbox is not None and outbox['status'] == 'staged', outbox
    return box, prop, outbox


# --- Case 1: property deactivated after staging ---

def test_fac01_property_deactivated_after_staging_blocks(fx):
    box, prop, outbox = _stage_recommendation(fx, '15550004101')
    deps = outbox['fact_deps']
    from decimal import Decimal
    assert any(d['type'] == 'property' and d['id'] == str(prop['id'])
               and Decimal(d['price']) == 150 for d in deps), deps
    _owner_save_property(fx, prop['id'], active=False)
    submit = StubSubmit(['wamid.FAC01'])
    result = dispatch.drive_row(fx['factory'], outbox['id'], worker='fac01', submit=submit)
    assert submit.calls == [], 'stale payload reached the provider'
    assert result['status'] == 'stale'
    row = _outbox_by_id(fx, outbox['id'])
    assert row['status'] == 'skipped' and 'stale' in (row['skip_reason'] or '')
    attempts = _attempts(fx, outbox['id'])
    assert attempts[-1]['status'] == 'blocked' and 'stale' in (attempts[-1]['outcome'] or '')
    assert _used(fx) == 0  # reservation refunded, never submitted


# --- Case 2: property price changed after staging ---

def test_fac02_property_price_changed_after_staging_blocks(fx):
    box, prop, outbox = _stage_recommendation(fx, '15550004102')
    _owner_save_property(fx, prop['id'], price=175)
    submit = StubSubmit(['wamid.FAC02'])
    result = dispatch.drive_row(fx['factory'], outbox['id'], worker='fac02', submit=submit)
    assert submit.calls == [], 'stale payload reached the provider'
    assert result['status'] == 'stale'
    row = _outbox_by_id(fx, outbox['id'])
    assert row['status'] == 'skipped' and 'details changed' in (row['skip_reason'] or '')
    assert 'price 150' in row['payload']['body']  # original payload preserved verbatim


# --- Case 6: unrelated property field changed -> still eligible ---

def test_fac06_unrelated_property_field_change_still_submits(fx):
    box, prop, outbox = _stage_recommendation(fx, '15550004106')
    _owner_save_property(fx, prop['id'], description='Fictional repainted description')
    submit = StubSubmit(['wamid.FAC06'])
    result = dispatch.drive_row(fx['factory'], outbox['id'], worker='fac06', submit=submit)
    assert result['status'] == 'sent'
    assert len(submit.calls) == 1 and 'price 150' in submit.calls[0][1]


# --- Case 8 + 10: fresh facts, provider timeout -> unknown semantics intact;
# a stale payload never reaches the provider (zero-call proof) ---

def test_fac08_provider_unknown_recovery_unchanged(fx):
    box, prop, outbox = _stage_recommendation(fx, '15550004108')
    submit = StubSubmit([dispatch.SubmitUnknown('timeout')])
    result = dispatch.drive_row(fx['factory'], outbox['id'], worker='fac08', submit=submit)
    assert result == {'status': 'unknown'}
    assert len(submit.calls) == 1
    row = _outbox_by_id(fx, outbox['id'])
    assert row['status'] == 'unknown'
    attempts = _attempts(fx, outbox['id'])
    assert attempts[-1]['status'] == 'unknown'


def test_fac10_stale_payload_makes_zero_provider_calls(fx):
    box, prop, outbox = _stage_recommendation(fx, '15550004110')
    _owner_save_property(fx, prop['id'], price=999)
    submit = StubSubmit(['wamid.FAC10'])
    result = dispatch.drive_row(fx['factory'], outbox['id'], worker='fac10', submit=submit)
    assert result['status'] == 'stale'
    assert submit.calls == []
    row = _outbox_by_id(fx, outbox['id'])
    assert row['status'] == 'skipped' and 'stale_facts' in (row['skip_reason'] or '')
    assert row['provider_msg_id'] is None


# --- Case 7: no fact changes -> submits successfully ---

def test_fac07_no_fact_changes_submits(fx):
    box, prop, outbox = _stage_recommendation(fx, '15550004107')
    submit = StubSubmit(['wamid.FAC07'])
    result = dispatch.drive_row(fx['factory'], outbox['id'], worker='fac07', submit=submit)
    assert result['status'] == 'sent'
    assert len(submit.calls) == 1 and 'price 150' in submit.calls[0][1]
    assert _outbox_by_id(fx, outbox['id'])['status'] == 'sent'


# --- Case 3: FAQ version changed after staging ---

def test_fac03_faq_version_changed_after_staging_blocks(fx):
    from app import faq as faq_service
    from app import respond
    faq = faq_service.create(fx['crm'], s.FaqCreate(
        request_key=fx['key'](), scope='business', question='Do you charge a buyer fee?',
        answer='SoloEstate charges no buyer fee for site visits.'))['record']
    box = _inbound(fx, '15550004103', 'Do you charge a buyer fee?')
    with fx['conn'].cursor(row_factory=dict_row) as cur:
        policy = cur.execute("SELECT * FROM soloestate.business_policies WHERE id='default'").fetchone()
        now = cur.execute('SELECT clock_timestamp() AS now').fetchone()['now']
        lead = cur.execute('SELECT * FROM soloestate.leads WHERE id=%s', (box['lead']['id'],)).fetchone()
        registry = respond.build_registry(cur, box['convo'], lead, box['message']['body'], dict(policy), now)
        ref = next(ref for ref, fact in registry.items() if fact['ftype'] == 'faq_quotes')
    reply = receptionist.respond_turn(
        fx['factory'], box['convo']['id'], box['message']['id'],
        model=StubModel([_out('faq', [dict(phrase_id='FAQ_ANSWER', args={'quotes': ref})])]))
    assert reply['replied'] is True and 'no buyer fee' in reply['payload'], reply
    outbox = _outbox(fx, box['message']['id'])
    assert outbox is not None and outbox['status'] == 'staged', outbox
    faq_service.new_version(fx['crm'], faq['id'], s.FaqVersion(
        request_key=fx['key'](), expected_version=faq['version'],
        question='Do you charge a buyer fee?', answer='A buyer fee now applies.'))
    submit = StubSubmit(['wamid.FAC03'])
    result = dispatch.drive_row(fx['factory'], outbox['id'], worker='fac03', submit=submit)
    assert submit.calls == [], 'stale FAQ payload reached the provider'
    assert result['status'] == 'stale'
    row = _outbox_by_id(fx, outbox['id'])
    assert row['status'] == 'skipped' and 'version changed' in (row['skip_reason'] or '')


# --- Booking journeys (cases 4-5) ---

def _say_linked(b, box, body, seq):
    from psycopg.types.json import Jsonb as _Jsonb
    request_key = 'test:' + str(uuid4())
    with b['conn'].cursor(row_factory=dict_row) as cur:
        evidence = cur.execute('SELECT response FROM soloestate.crm_operations WHERE request_key=%s',
                               (box['message']['request_key'],)).fetchone()['response']['inbound_evidence']
        cur.execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) '
                    'VALUES (%s,%s,%s,%s,%s,%s)',
                    (request_key, 'channel:inbound', 'test:' + request_key, 'channel',
                     box['link']['id'], _Jsonb({'inbound_evidence': evidence})))
        return cur.execute('INSERT INTO soloestate.conversation_messages(id,conversation_id,seq,role,body,request_key) '
                           'VALUES (%s,%s,%s,%s,%s,%s) RETURNING *',
                           (uuid4(), box['convo']['id'], seq, 'customer', body, request_key)).fetchone()


def _booked_confirmation(fx, sender):
    """Two production turns: propose+confirm stages a BOOK_CONFIRMED body.
    Returns (box, prop, request_row, booking_row, confirm_outbox)."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    ist = ZoneInfo('Asia/Kolkata')
    box = _inbound(fx, sender, 'I want to visit')
    lead = box['lead']
    qualified = fx['crm'].save('leads', s.LeadUpdate(
        request_key=fx['key'](), expected_version=lead['version'], name=lead['name'], email=lead['email'],
        preferred_localities=[fx['prefix']], budget_min=100, budget_max=200, bedrooms=2,
        purchase_timeframe='within_3_months', status=lead['status'], notes=lead['notes'] or ''),
        lead['id'])['record']
    box['lead'] = qualified
    prop = _prop(fx)
    today = datetime.now(ist).date()
    day = today + timedelta(days=14)
    day += timedelta(days=(3 - day.isoweekday()) % 7)  # next Wednesday
    start = datetime(day.year, day.month, day.day, 11, 0, tzinfo=ist)
    end = start + timedelta(minutes=30)
    selection = _out('availability', [_q('Q_CLARIFY')],
                     booking={'start': start.isoformat(), 'end': end.isoformat()})
    first = receptionist.respond_turn(fx['factory'], box['convo']['id'], box['message']['id'],
                                      model=StubModel([selection]))
    assert 'Just to confirm' in first['payload'], first
    first_outbox = _outbox(fx, box['message']['id'])
    assert first_outbox is not None and first_outbox['status'] == 'staged', first_outbox
    driven = dispatch.drive_row(fx['factory'], first_outbox['id'], worker='fac-drive1',
                                submit=StubSubmit(['wamid.FACQ1']))
    assert driven['status'] == 'sent', driven  # production drives each turn before the next
    with fx['conn'].cursor() as cur:
        # The customer confirms the next day: age the ack out of the 24h
        # lead cooldown so the confirmation itself may stage.
        cur.execute("UPDATE soloestate.delivery_attempts SET submitted_at=clock_timestamp()-interval '25 hours' "
                    'WHERE outbox_id=%s', (first_outbox['id'],))
    with fx['conn'].cursor(row_factory=dict_row) as cur:
        request = cur.execute('SELECT * FROM soloestate.booking_requests WHERE conversation_id=%s',
                              (box['convo']['id'],)).fetchone()
    second_msg = _say_linked(fx, box, 'Yes, confirm', 3)
    confirm = _out('availability', [_q('Q_CLARIFY')], confirm_request=str(request['id']))
    second = receptionist.respond_turn(fx['factory'], box['convo']['id'], second_msg['id'],
                                       model=StubModel([confirm]))
    assert 'confirmed for' in second['payload'], second
    outbox = _outbox(fx, second_msg['id'])
    assert outbox is not None and outbox['status'] == 'staged', outbox
    with fx['conn'].cursor(row_factory=dict_row) as cur:
        booking = cur.execute('SELECT b.* FROM soloestate.bookings b JOIN soloestate.booking_requests r '
                              'ON r.booking_id=b.id WHERE r.id=%s', (request['id'],)).fetchone()
    return box, prop, request, booking, outbox


def test_fac04_booking_cancelled_after_staging_blocks(fx):
    box, prop, request, booking, outbox = _booked_confirmation(fx, '15550004104')
    fx['crm'].cancel(booking['id'], s.Cancel(request_key=fx['key'](),
                     expected_version=booking['version'], reason='Fictional owner cancellation'))
    submit = StubSubmit(['wamid.FAC04'])
    result = dispatch.drive_row(fx['factory'], outbox['id'], worker='fac04', submit=submit)
    assert submit.calls == [], 'cancelled-booking payload reached the provider'
    assert result['status'] == 'stale'
    row = _outbox_by_id(fx, outbox['id'])
    assert row['status'] == 'skipped' and 'cancelled' in (row['skip_reason'] or '')


def test_fac05_appointment_rescheduled_after_staging_blocks(fx):
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    ist = ZoneInfo('Asia/Kolkata')
    box, prop, request, booking, outbox = _booked_confirmation(fx, '15550004105')
    today = datetime.now(ist).date()
    day = today + timedelta(days=21)
    day += timedelta(days=(3 - day.isoweekday()) % 7)
    start = datetime(day.year, day.month, day.day, 12, 0, tzinfo=ist)
    slot = fx['crm'].slot(s.SlotCreate(request_key=fx['key'](), starts_at=start,
                                       ends_at=start + timedelta(minutes=30)))['record']
    review = fx['crm'].prepare(s.AppointmentRequest(
        request_key=fx['key'](), lead_id=box['lead']['id'], property_id=prop['id'], slot_id=slot['id'],
        replaces_booking_id=booking['id'], expected_version=booking['version']))['record']
    fx['crm'].commit(review['id'], s.Confirm(request_key=fx['key'](),
                     expected_payload_hash=review['payload_hash']))
    submit = StubSubmit(['wamid.FAC05'])
    result = dispatch.drive_row(fx['factory'], outbox['id'], worker='fac05', submit=submit)
    assert submit.calls == [], 'rescheduled-appointment payload reached the provider'
    assert result['status'] == 'stale'
    row = _outbox_by_id(fx, outbox['id'])
    assert row['status'] == 'skipped' and 'stale_facts' in (row['skip_reason'] or '')


def test_fac05b_slot_time_changed_after_staging_blocks(fx):
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    from app.identity_links import normalize_sender
    ist = ZoneInfo('Asia/Kolkata')
    box = _inbound(fx, '15550004155', 'Show me visit times')
    today = datetime.now(ist).date()
    start = datetime(today.year, today.month, today.day, 11, 0, tzinfo=ist) + timedelta(days=2)
    slot = fx['crm'].slot(s.SlotCreate(request_key=fx['key'](), starts_at=start,
                                       ends_at=start + timedelta(minutes=30)))['record']
    with fx['conn'].cursor(row_factory=dict_row) as cur:
        link = cur.execute('SELECT * FROM soloestate.identity_links WHERE id=%s', (box['link']['id'],)).fetchone()
        slot_row = cur.execute('SELECT * FROM soloestate.visit_slots WHERE id=%s', (slot['id'],)).fetchone()
    staged = dispatch.stage(
        fx['crm'], request_key=fx['key'](), channel='whatsapp', purpose='reply',
        source_id=str(box['message']['id']), lead_id=box['lead']['id'],
        conversation_id=box['convo']['id'], link_id=str(box['link']['id']),
        destination=normalize_sender('whatsapp', '15550004155'), category='availability',
        payload={'body': 'Available visit times (fictional)', 'catalog_version': CATALOG_VERSION},
        content_ref='test:fac05b', catalog_version=CATALOG_VERSION,
        fact_deps=[{'type': 'slot', 'id': str(slot['id']),
                    'starts_at': slot_row['starts_at'].isoformat(),
                    'ends_at': slot_row['ends_at'].isoformat()}])
    assert staged['record']['status'] == 'staged', staged
    with fx['conn'].cursor() as cur:
        cur.execute("UPDATE soloestate.visit_slots SET starts_at=starts_at+interval '2 hours', "
                    'ends_at=ends_at+interval \'2 hours\' WHERE id=%s', (slot['id'],))
    submit = StubSubmit(['wamid.FAC05B'])
    result = dispatch.drive_row(fx['factory'], staged['record']['id'], worker='fac05b', submit=submit)
    assert submit.calls == [], 'moved-slot payload reached the provider'
    assert result['status'] == 'stale'
    row = _outbox_by_id(fx, staged['record']['id'])
    assert row['status'] == 'skipped' and 'time changed' in (row['skip_reason'] or '')


# --- Case 9: concurrent owner update vs dispatch (committed, exact cleanup) ---

@contextmanager
def _committed_factory():
    with connect(Settings()) as conn:
        yield conn


def test_fac09_owner_update_wins_race_no_stale_submit():
    from app import identity_links
    from app.policies import ROUTINE_CATEGORIES, update_reply_policy
    crm = CRM(_committed_factory)
    prefix = 'd2fx9-' + str(uuid4())
    key = lambda: prefix + ':' + str(uuid4())  # noqa: E731
    with connect(Settings()) as conn:
        policy_before = conn.execute("SELECT enabled, categories, version FROM soloestate.immediate_reply_policies "
                                     "WHERE id='whatsapp'").fetchone()
        caps_before = conn.execute("SELECT day, used, cap FROM soloestate.delivery_caps "
                                   "WHERE channel='whatsapp' ORDER BY day").fetchall()
    created = {}
    try:
        update_reply_policy(crm, s.ReplyPolicyUpdate(
            request_key=key(), expected_version=policy_before[2], enabled=True,
            categories=list(ROUTINE_CATEGORIES), reason='Fictional fac09 enable'))
        prop = crm.save('properties', s.PropertyCreate(
            request_key=key(), title=prefix, locality=prefix, price=150, bedrooms=2))['record']
        created['property'] = str(prop['id'])
        sender = '1555%07d' % (uuid4().int % 10000000)
        observe_key = key()
        with _committed_factory() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                link, _ = identity_links.observe(cur, 'whatsapp', sender, observe_key)
                # Stamp the provider instant from the database clock: a
                # Python wall-clock stamp can land after the DB-stamped
                # receipt instant and read as incoherent evidence.
                provider_ts = cur.execute('SELECT clock_timestamp() AS ts').fetchone()['ts']
                cur.execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) '
                            'VALUES (%s,%s,%s,%s,%s,%s)',
                            (observe_key, 'channel:inbound', 'wa:fac09', 'channel', link['id'],
                             Jsonb({'provider_ts': provider_ts.isoformat(),
                                    'inbound_evidence': {'channel': 'whatsapp', 'provider_ref': sender,
                                                         'link_id': str(link['id'])}})))
                convo = cur.execute('SELECT * FROM soloestate.conversations WHERE lead_id=%s '
                                    'ORDER BY created_at DESC LIMIT 1', (link['lead_id'],)).fetchone()
                cur.execute('INSERT INTO soloestate.conversation_messages(id,conversation_id,seq,role,body,request_key) '
                            'VALUES (%s,%s,1,%s,%s,%s)',
                            (uuid4(), convo['id'], 'customer', 'Fictional hello', observe_key))
                cur.execute("UPDATE soloestate.conversations SET channel='whatsapp' WHERE id=%s", (convo['id'],))
        created['link'] = str(link['id'])
        created['lead'] = str(link['lead_id'])
        created['convo'] = str(convo['id'])
        staged = dispatch.stage(
            crm, request_key=key(), channel='whatsapp', purpose='reply', source_id=str(uuid4()),
            lead_id=link['lead_id'], conversation_id=convo['id'], link_id=str(link['id']),
            destination=identity_links.normalize_sender('whatsapp', sender), category='recommendation',
            payload={'body': 'Matches: %s (%s), price 150, 2 BHK' % (prefix, prefix),
                     'catalog_version': CATALOG_VERSION},
            content_ref='test:fac09', catalog_version=CATALOG_VERSION,
            fact_deps=[{'type': 'property', 'id': str(prop['id']), 'title': prefix,
                        'locality': prefix, 'price': '150', 'bedrooms': 2}])
        assert staged['record']['status'] == 'staged', staged
        outbox_id = staged['record']['id']
        created['outbox'] = str(outbox_id)
        # The owner update commits while dispatch is inside its transition:
        # whichever order the locks serialize in, the stale body never submits.
        owner = connect(Settings())
        owner.execute('UPDATE soloestate.properties SET active=false WHERE id=%s', (prop['id'],))
        submit = StubSubmit(['wamid.FAC09'])
        outcome = {}

        def drive():
            try:
                outcome['result'] = dispatch.drive_row(_committed_factory, outbox_id,
                                                       worker='fac09', submit=submit)
            except Exception as exc:  # noqa: BLE001 - surfaced below
                outcome['error'] = exc
        worker = Thread(target=drive)
        worker.start()
        time.sleep(2)
        owner.commit()
        owner.close()
        worker.join(timeout=120)
        assert not worker.is_alive(), 'drive hung'
        assert 'error' not in outcome, outcome.get('error')
        assert submit.calls == [], 'stale payload reached the provider'
        assert outcome['result']['status'] == 'stale'
        with _committed_factory() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                row = cur.execute('SELECT * FROM soloestate.delivery_outbox WHERE id=%s',
                                  (outbox_id,)).fetchone()
        assert row['status'] == 'skipped' and 'deactivated' in (row['skip_reason'] or '')
    finally:
        with connect(Settings()) as conn:
            version = conn.execute("SELECT version FROM soloestate.immediate_reply_policies "
                                   "WHERE id='whatsapp'").fetchone()[0]
        update_reply_policy(crm, s.ReplyPolicyUpdate(
            request_key=key(), expected_version=version, enabled=policy_before[0],
            categories=list(policy_before[1]), reason='Fictional fac09 restore'))
        with connect(Settings()) as conn:
            if 'outbox' in created:
                conn.execute('DELETE FROM soloestate.delivery_outbox WHERE id=%s', (created['outbox'],))
            if 'convo' in created:
                conn.execute('DELETE FROM soloestate.conversation_messages WHERE conversation_id=%s',
                             (created['convo'],))
                conn.execute('DELETE FROM soloestate.conversations WHERE id=%s', (created['convo'],))
            if 'link' in created:
                conn.execute('DELETE FROM soloestate.identity_links WHERE id=%s', (created['link'],))
            if 'lead' in created:
                conn.execute('DELETE FROM soloestate.lead_activities WHERE lead_id=%s', (created['lead'],))
                conn.execute('DELETE FROM soloestate.leads WHERE id=%s', (created['lead'],))
            if 'property' in created:
                conn.execute('DELETE FROM soloestate.properties WHERE id=%s', (created['property'],))
            conn.execute("DELETE FROM soloestate.lead_activities WHERE request_key LIKE %s", (prefix + ':%',))
            conn.execute("DELETE FROM soloestate.crm_operations WHERE request_key LIKE %s", (prefix + ':%',))
            conn.execute("DELETE FROM soloestate.delivery_caps WHERE channel='whatsapp'")
            for day, used, cap in caps_before:
                conn.execute('INSERT INTO soloestate.delivery_caps(day, channel, used, cap) VALUES (%s,%s,%s,%s)',
                             (day, 'whatsapp', used, cap))


# --- Legacy-row bypass: empty validation evidence must fail closed ---

def _make_legacy(b, outbox_id):
    """Rewrite a production-staged row into its pre-0018/0019 legacy
    state: no pinned dependencies and no validation evidence."""
    with b['conn'].cursor() as cur:
        cur.execute("UPDATE soloestate.delivery_outbox SET fact_deps='[]'::jsonb, "
                    'fact_validated=false WHERE id=%s', (outbox_id,))


def test_fac11_legacy_recommendation_price_change_blocks(fx):
    box, prop, outbox = _stage_recommendation(fx, '15550004111')
    original_body = outbox['payload']['body']
    _make_legacy(fx, outbox['id'])
    _owner_save_property(fx, prop['id'], price=175)
    submit = StubSubmit(['wamid.FAC11'])
    result = dispatch.drive_row(fx['factory'], outbox['id'], worker='fac11', submit=submit)
    assert submit.calls == [], 'legacy payload reached the provider'
    assert result['status'] == 'stale'
    row = _outbox_by_id(fx, outbox['id'])
    assert row['status'] == 'skipped' and 'stale_facts' in (row['skip_reason'] or '')
    assert 'fact validation' in (row['skip_reason'] or '')
    assert row['payload']['body'] == original_body  # payload preserved verbatim
    attempts = _attempts(fx, outbox['id'])
    assert attempts[-1]['status'] == 'blocked' and 'fact validation' in (attempts[-1]['outcome'] or '')
    assert _used(fx) == 0  # reservation refunded exactly once


def test_fac11b_legacy_recommendation_deactivated_blocks(fx):
    box, prop, outbox = _stage_recommendation(fx, '15550004112')
    _make_legacy(fx, outbox['id'])
    _owner_save_property(fx, prop['id'], active=False)
    submit = StubSubmit(['wamid.FAC11B'])
    result = dispatch.drive_row(fx['factory'], outbox['id'], worker='fac11b', submit=submit)
    assert submit.calls == [], 'legacy payload reached the provider'
    assert result['status'] == 'stale'
    row = _outbox_by_id(fx, outbox['id'])
    assert row['status'] == 'skipped' and 'fact validation' in (row['skip_reason'] or '')
    assert _used(fx) == 0


def test_fac12_validated_fact_free_reply_still_submits(fx):
    box = _inbound(fx, '15550004113', 'Fictional hello')
    reply = receptionist.respond_turn(
        fx['factory'], box['convo']['id'], box['message']['id'],
        model=StubModel([_out('welcome', [dict(phrase_id='ACK_RECEIVED', args={})])]))
    assert reply['replied'] is True, reply
    outbox = _outbox(fx, box['message']['id'])
    assert outbox is not None and outbox['status'] == 'staged', outbox
    assert (outbox['fact_deps'] or []) == []  # genuinely fact-free, yet validated
    assert outbox['fact_validated'] is True
    submit = StubSubmit(['wamid.FAC12'])
    result = dispatch.drive_row(fx['factory'], outbox['id'], worker='fac12', submit=submit)
    assert result['status'] == 'sent'
    assert len(submit.calls) == 1


def test_fac13_requeue_cannot_bypass_legacy_block(fx):
    box, prop, outbox = _stage_recommendation(fx, '15550004114')
    _make_legacy(fx, outbox['id'])
    _owner_save_property(fx, prop['id'], price=175)
    submit = StubSubmit(['wamid.FAC13'])
    first = dispatch.drive_row(fx['factory'], outbox['id'], worker='fac13', submit=submit)
    assert first['status'] == 'stale' and submit.calls == []
    requeued = dispatch.requeue(fx['crm'], outbox['id'], dispatch.OutboxRequeue(
        request_key=fx['key'](), reason='Fictional requeue of legacy row'))
    assert requeued['record']['status'] == 'staged'
    second = dispatch.drive_row(fx['factory'], outbox['id'], worker='fac13b', submit=submit)
    assert second['status'] == 'stale'
    assert submit.calls == [], 'requeued legacy payload reached the provider'
    row = _outbox_by_id(fx, outbox['id'])
    assert row['status'] == 'skipped' and 'fact validation' in (row['skip_reason'] or '')
    assert _used(fx) == 0  # requeue reservation refunded once, never submitted
