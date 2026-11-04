"""Website/WhatsApp shared-engine parity. Rollback fixtures, fictional records.

Website sends run the same receptionist turn engine as WhatsApp: equivalent
requests must yield consistent validated business decisions (qualification,
matching, FAQ, booking, exceptions, escalation, fallback), while channel
transport stays distinct (website replies persist inline only and never
stage outbox rows or touch the provider; WhatsApp turns stage deliveries)
and provenance stays truthful (visitor vs channel actors).

Opt-out/suppression scoping note: suppression lists and push-delivery
authorization apply at WhatsApp stage/drive time (covered by the D2
limits/delivery suites). Website visitors have no push destination, so
there is no website opt-out state; website turns share the freeze,
consent/handoff, archived, stop and policy gates through the same engine
and shared services instead.
"""
import asyncio
import json
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app import chat_schemas as cs
from app import crm_schemas as s
from app import receptionist, respond
from app.catalog import CATALOG_VERSION, FALLBACK_TEXT
from app.chat import send as website_send
from app.chat import start as website_start
from app.config import Settings
from app.crm import CRM, CRMError, scalar
from app.database import connect

pytestmark = pytest.mark.d2_database

IST = ZoneInfo('Asia/Kolkata')


class StubModel:
    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = 0

    async def generate(self, prompt):
        self.calls += 1
        assert len(prompt) <= 20000
        from app.services.meta_model import ModelResult
        return ModelResult(response_id='resp-%d' % self.calls, model='test-meta',
                           text=self.texts[min(self.calls - 1, len(self.texts) - 1)])


@pytest.fixture
def pp(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    with connect(Settings()) as conn:
        with conn.transaction(force_rollback=True):
            @contextmanager
            def factory():
                with conn.transaction():
                    yield conn
            settings = Settings(visitor_token_secret=secrets.token_urlsafe(32))
            prefix = 'pp-' + str(uuid4())
            yield dict(crm=CRM(factory), conn=conn, factory=factory, settings=settings, prefix=prefix,
                       key=lambda: prefix + ':' + str(uuid4()))


def _tout(category, segments, **extra):
    return json.dumps(dict(catalog_version=CATALOG_VERSION, category=category, segments=segments, **extra))


def _web(b):
    return website_start(b['crm'], cs.ChatStart(request_key=b['key'](), name=b['prefix'],
                                                email=b['prefix'] + '@example.invalid'), b['settings'])


def _wa(b, sender, body='Fictional hello'):
    from app import identity_links
    now = datetime.now(timezone.utc)
    with b['conn'].cursor(row_factory=dict_row) as cur:
        link, _ = identity_links.observe(cur, 'whatsapp', sender, b['key']())
        convo = cur.execute('SELECT * FROM soloestate.conversations WHERE lead_id=%s ORDER BY created_at DESC LIMIT 1',
                            (link['lead_id'],)).fetchone()
        wamid = 'wamid.PP' + uuid4().hex[:12]
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
    return dict(link=link, lead=lead, convo=convo, message=message)


def _enable_policy(b):
    from app.policies import ROUTINE_CATEGORIES, update_reply_policy
    with b['conn'].cursor(row_factory=dict_row) as cur:
        version = cur.execute("SELECT version FROM soloestate.immediate_reply_policies WHERE id='whatsapp'").fetchone()['version']
    return update_reply_policy(b['crm'], s.ReplyPolicyUpdate(
        request_key=b['key'](), expected_version=version, enabled=True,
        categories=sorted(ROUTINE_CATEGORIES), reason='Fictional PP enable'))


def _faq_text(conn, cid, body):
    with conn.cursor(row_factory=dict_row) as cur:
        convo = cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s', (cid,)).fetchone()
        lead = cur.execute('SELECT * FROM soloestate.leads WHERE id=%s', (convo['lead_id'],)).fetchone()
        policy = cur.execute("SELECT * FROM soloestate.business_policies WHERE id='default'").fetchone()
        now = cur.execute('SELECT clock_timestamp() AS now').fetchone()['now']
        registry = respond.build_registry(cur, convo, lead, body, dict(policy), now)
    ref = next(ref for ref, fact in registry.items() if fact['ftype'] == 'faq_quotes')
    return _tout('faq', [dict(phrase_id='FAQ_ANSWER', args={'quotes': ref})])


def _thread(b, cid):
    with b['conn'].cursor(row_factory=dict_row) as cur:
        return cur.execute('SELECT role,body,grounding,unsupported_reason,citations FROM soloestate.conversation_messages '
                           'WHERE conversation_id=%s ORDER BY seq', (cid,)).fetchall()


def _outbox_count(b, cid):
    with b['conn'].cursor() as cur:
        return scalar(cur.execute('SELECT count(*) FROM soloestate.delivery_outbox WHERE conversation_id=%s',
                                  (cid,)).fetchone())


def _reply_actor(b, message_id):
    with b['conn'].cursor(row_factory=dict_row) as cur:
        return cur.execute('SELECT actor FROM soloestate.crm_operations WHERE request_key=%s',
                           ('channel:reply:' + str(message_id),)).fetchone()['actor']


def _requirements(b, lead_id):
    with b['conn'].cursor(row_factory=dict_row) as cur:
        row = cur.execute('SELECT preferred_localities,budget_min,budget_max,bedrooms,purchase_timeframe,version '
                          'FROM soloestate.leads WHERE id=%s', (lead_id,)).fetchone()
    return dict(row)


def _sender(b):
    return '1555%07d' % (abs(hash((b['prefix'], uuid4()))) % 10000000)


def clean_window(conn, **kwargs):
    from datetime import timedelta as _td
    holidays = set(conn.execute("SELECT holiday_dates FROM soloestate.business_policies WHERE id='default'").fetchone()[0] or [])
    today = datetime.now(IST).date()
    base = today + _td(days=kwargs.get('min_days_ahead', 14))
    day = base + _td(days=(kwargs['weekday'] - base.isoweekday()) % 7)
    start = datetime(day.year, day.month, day.day, kwargs.get('hour', 11), kwargs.get('minute', 0), tzinfo=IST)
    end = start + _td(minutes=kwargs.get('duration', 30))
    while start.date() in holidays or end.date() in holidays:
        start, end = start + _td(days=7), end + _td(days=7)
    return start, end


def test_pp01_partial_qualification_parity(pp):
    b = pp
    _enable_policy(b)
    opened = _web(b)
    wcid, wlead = opened['conversation']['id'], opened['lead']['id']
    wa = _wa(b, _sender(b), 'My budget is 1cr to 1.5cr in Whitefield')
    for lead_id, email in ((wlead, b['prefix'] + '@example.invalid'),
                             (wa['lead']['id'], 'wa-' + b['prefix'] + '@example.invalid')):
        b['crm'].save('leads', s.LeadUpdate(
            request_key=b['key'](), expected_version=1, name=b['prefix'], email=email,
            preferred_localities=['Whitefield'], bedrooms=2,
            purchase_timeframe='within_3_months'), lead_id)
    text = _tout('qualification', [dict(question_id='Q_BUDGET', args={})],
                 requirements={'budget_min': '10000000', 'budget_max': '15000000'})
    web_stub, wa_stub = StubModel([text]), StubModel([text])
    web = asyncio.run(website_send(b['crm'], wcid, cs.ChatSend(
        request_key=b['key'](), expected_version=1, body='My budget is 1cr to 1.5cr in Whitefield'), web_stub))
    wa_out = receptionist.respond_turn(b['factory'], wa['convo']['id'], wa['message']['id'], model=wa_stub)
    assert web['reply']['grounding'] == 'validated' and wa_out['grounding'] == 'validated'
    assert web['reply']['body'] == wa_out['payload']
    assert _requirements(b, wlead) == _requirements(b, wa['lead']['id'])
    assert _requirements(b, wlead)['budget_min'] == 10000000
    assert web_stub.calls == wa_stub.calls == 1
    assert _outbox_count(b, wcid) == 0
    assert wa_out['delivery']['status'] == 'staged' and _outbox_count(b, wa['convo']['id']) == 1
    assert _reply_actor(b, web['message']['id']) == 'visitor'
    assert _reply_actor(b, wa['message']['id']) == 'channel'


def test_pp02_matching_parity(pp):
    b = pp
    _enable_policy(b)
    opened = _web(b)
    wcid = opened['conversation']['id']
    prop = b['crm'].save('properties', s.PropertyCreate(
        request_key=b['key'](), title=b['prefix'] + ' Residency', locality=b['prefix'], price=150,
        bedrooms=2))['record']
    b['crm'].save('leads', s.LeadUpdate(
        request_key=b['key'](), expected_version=1, name=b['prefix'], email=b['prefix'] + '@example.invalid',
        preferred_localities=[b['prefix']], budget_min='100', budget_max='200',
        purchase_timeframe='within_3_months'), opened['lead']['id'])
    wa = _wa(b, _sender(b), 'We need 2 bedrooms')
    b['crm'].save('leads', s.LeadUpdate(
        request_key=b['key'](), expected_version=1, name=b['prefix'], email='wa-' + b['prefix'] + '@example.invalid',
        preferred_localities=[b['prefix']], budget_min='100', budget_max='200',
        purchase_timeframe='within_3_months'), wa['lead']['id'])
    text = _tout('qualification', [dict(question_id='Q_BHK', args={})], requirements={'bedrooms': 2})
    web_stub, wa_stub = StubModel([text]), StubModel([text])
    web = asyncio.run(website_send(b['crm'], wcid, cs.ChatSend(
        request_key=b['key'](), expected_version=1, body='We need 2 bedrooms'), web_stub))
    wa_out = receptionist.respond_turn(b['factory'], wa['convo']['id'], wa['message']['id'], model=wa_stub)
    assert web['reply']['grounding'] == 'validated' and wa_out['grounding'] == 'validated'
    assert 'current matches' in web['reply']['body'] and 'current matches' in wa_out['payload']
    assert prop['title'] in web['reply']['body'] and prop['title'] in wa_out['payload']
    assert _outbox_count(b, wcid) == 0 and _outbox_count(b, wa['convo']['id']) == 1


def test_pp03_faq_parity(pp):
    from app.faq import create as faq_create
    b = pp
    _enable_policy(b)
    answer = 'No buyer fee applies in Bengaluru for this listing.'
    faq_create(b['crm'], s.FaqCreate(request_key=b['key'](), scope='business',
                                     question='Is there a buyer fee?', answer=answer))
    opened = _web(b)
    wcid = opened['conversation']['id']
    wa = _wa(b, _sender(b), 'Is there a buyer fee?')
    web_text = _faq_text(b['conn'], wcid, 'Is there a buyer fee?')
    wa_text = _faq_text(b['conn'], wa['convo']['id'], 'Is there a buyer fee?')
    web_stub, wa_stub = StubModel([web_text]), StubModel([wa_text])
    web = asyncio.run(website_send(b['crm'], wcid, cs.ChatSend(
        request_key=b['key'](), expected_version=1, body='Is there a buyer fee?'), web_stub))
    wa_out = receptionist.respond_turn(b['factory'], wa['convo']['id'], wa['message']['id'], model=wa_stub)
    web_thread, wa_thread = _thread(b, wcid), _thread(b, wa['convo']['id'])
    assert web['reply']['grounding'] == 'grounded' and wa_out['grounding'] == 'grounded'
    assert web_thread[1]['citations'] == wa_thread[1]['citations']
    assert web_thread[1]['citations'][0]['quote'] == answer
    assert web['reply']['body'] == wa_out['payload']
    assert _outbox_count(b, wcid) == 0 and _outbox_count(b, wa['convo']['id']) == 1


def _book_sides(b):
    opened = _web(b)
    wcid = opened['conversation']['id']
    prop = b['crm'].save('properties', s.PropertyCreate(
        request_key=b['key'](), title=b['prefix'] + ' Residency', locality=b['prefix'], price=150,
        bedrooms=2))['record']
    b['crm'].save('leads', s.LeadUpdate(
        request_key=b['key'](), expected_version=1, name=b['prefix'], email=b['prefix'] + '@example.invalid',
        preferred_localities=[b['prefix']], budget_min='100', budget_max='200', bedrooms=2,
        purchase_timeframe='within_3_months'), opened['lead']['id'])
    wa = _wa(b, _sender(b), 'I want to visit')
    b['crm'].save('leads', s.LeadUpdate(
        request_key=b['key'](), expected_version=1, name=b['prefix'], email='wa-' + b['prefix'] + '@example.invalid',
        preferred_localities=[b['prefix']], budget_min='100', budget_max='200', bedrooms=2,
        purchase_timeframe='within_3_months'), wa['lead']['id'])
    return opened, wcid, wa, prop


def _request_row(b, cid):
    with b['conn'].cursor(row_factory=dict_row) as cur:
        return cur.execute('SELECT * FROM soloestate.booking_requests WHERE conversation_id=%s', (cid,)).fetchone()


def _booking_for(b, lead_id):
    with b['conn'].cursor(row_factory=dict_row) as cur:
        return cur.execute('SELECT * FROM soloestate.bookings WHERE lead_id=%s', (lead_id,)).fetchone()


def test_pp04_booking_propose_confirm_commit_parity(pp):
    b = pp
    _enable_policy(b)
    web_start, web_end = clean_window(b['conn'], weekday=3, hour=11)
    wa_start, wa_end = clean_window(b['conn'], weekday=3, hour=14)
    opened, wcid, wa, prop = _book_sides(b)
    web_first = asyncio.run(website_send(b['crm'], wcid, cs.ChatSend(
        request_key=b['key'](), expected_version=1, body='I want to visit'), StubModel([
            _tout('availability', [dict(question_id='Q_CLARIFY', args={})],
                  booking={'start': web_start.isoformat(), 'end': web_end.isoformat()})])))
    wa_first = receptionist.respond_turn(b['factory'], wa['convo']['id'], wa['message']['id'], model=StubModel([
        _tout('availability', [dict(question_id='Q_CLARIFY', args={})],
              booking={'start': wa_start.isoformat(), 'end': wa_end.isoformat()})]))
    assert 'Just to confirm' in web_first['reply']['body'] and 'Just to confirm' in wa_first['payload']
    web_req, wa_req = _request_row(b, wcid), _request_row(b, wa['convo']['id'])
    assert (web_req['status'], wa_req['status']) == ('approved', 'approved')
    assert (web_req['channel'], wa_req['channel']) == ('website', 'whatsapp')
    web_second = asyncio.run(website_send(b['crm'], wcid, cs.ChatSend(
        request_key=b['key'](), expected_version=web_first['conversation']['version'], body='Yes, confirm'),
        StubModel([_tout('availability', [dict(question_id='Q_CLARIFY', args={})],
                         confirm_request=str(web_req['id']))])))
    with b['conn'].cursor(row_factory=dict_row) as cur:
        seq = cur.execute('SELECT coalesce(max(seq),0)+1 AS next FROM soloestate.conversation_messages '
                          'WHERE conversation_id=%s', (wa['convo']['id'],)).fetchone()['next']
        wa_msg2 = cur.execute('INSERT INTO soloestate.conversation_messages(id,conversation_id,seq,role,body,request_key) '
                              'VALUES (%s,%s,%s,%s,%s,%s) RETURNING *',
                              (uuid4(), wa['convo']['id'], seq, 'customer', 'Yes, confirm', b['key']())).fetchone()
    wa_second = receptionist.respond_turn(b['factory'], wa['convo']['id'], wa_msg2['id'], model=StubModel([
        _tout('availability', [dict(question_id='Q_CLARIFY', args={})], confirm_request=str(wa_req['id']))]))
    assert 'confirmed for' in web_second['reply']['body'] and 'confirmed for' in wa_second['payload']
    web_booking = _booking_for(b, opened['lead']['id'])
    wa_booking = _booking_for(b, wa['lead']['id'])
    assert (web_booking['status'], wa_booking['status']) == ('confirmed', 'confirmed')
    assert (web_booking['slot_id'], wa_booking['slot_id']) != (None, None)
    assert web_booking['slot_id'] != wa_booking['slot_id']
    # Each WhatsApp reply stages its own delivery row; lead-wide cooldown
    # gates resend of the same logical row, not distinct replies. Website
    # stays inline-only: it persists both replies with zero outbox rows.
    assert _outbox_count(b, wcid) == 0 and _outbox_count(b, wa['convo']['id']) == 2


def test_pp05_booking_exception_parity(pp):
    b = pp
    _enable_policy(b)
    web_start, web_end = clean_window(b['conn'], weekday=2, hour=11)
    wa_start, wa_end = clean_window(b['conn'], weekday=2, hour=14)
    opened, wcid, wa, prop = _book_sides(b)
    web = asyncio.run(website_send(b['crm'], wcid, cs.ChatSend(
        request_key=b['key'](), expected_version=1, body='I want to visit'), StubModel([
            _tout('availability', [dict(question_id='Q_CLARIFY', args={})],
                  booking={'start': web_start.isoformat(), 'end': web_end.isoformat()})])))
    wa_out = receptionist.respond_turn(b['factory'], wa['convo']['id'], wa['message']['id'], model=StubModel([
        _tout('availability', [dict(question_id='Q_CLARIFY', args={})],
              booking={'start': wa_start.isoformat(), 'end': wa_end.isoformat()})]))
    assert web['conversation']['status'] == 'pending_owner'
    with b['conn'].cursor(row_factory=dict_row) as cur:
        wa_status = cur.execute('SELECT status FROM soloestate.conversations WHERE id=%s',
                                (wa['convo']['id'],)).fetchone()['status']
    assert wa_status == 'pending_owner'
    assert _request_row(b, wcid)['status'] == 'awaiting_owner'
    assert _request_row(b, wa['convo']['id'])['status'] == 'awaiting_owner'
    assert _booking_for(b, opened['lead']['id']) is None and _booking_for(b, wa['lead']['id']) is None


def test_pp06_sensitive_escalation_parity(pp):
    b = pp
    opened = _web(b)
    wcid = opened['conversation']['id']
    wa = _wa(b, _sender(b), 'Give me a discount on the price')
    web_stub, wa_stub = StubModel([_tout('faq', [])]), StubModel([_tout('faq', [])])
    with pytest.raises(CRMError, match='Your message was saved') as exc:
        asyncio.run(website_send(b['crm'], wcid, cs.ChatSend(
            request_key=b['key'](), expected_version=1, body='Give me a discount on the price'), web_stub))
    assert exc.value.status == 409
    wa_out = receptionist.respond_turn(b['factory'], wa['convo']['id'], wa['message']['id'], model=wa_stub)
    assert wa_out == {'replied': False, 'reason': 'sensitive_escalated', 'markers': ['discount']}
    assert web_stub.calls == wa_stub.calls == 0
    assert len(_thread(b, wcid)) == 1 and len(_thread(b, wa['convo']['id'])) == 1
    with b['conn'].cursor(row_factory=dict_row) as cur:
        assert cur.execute('SELECT status FROM soloestate.conversations WHERE id=%s', (wcid,)).fetchone()['status'] == 'pending_owner'
        assert cur.execute('SELECT status FROM soloestate.conversations WHERE id=%s',
                           (wa['convo']['id'],)).fetchone()['status'] == 'pending_owner'


def test_pp07_malformed_output_parity(pp):
    b = pp
    _enable_policy(b)
    opened = _web(b)
    wcid = opened['conversation']['id']
    wa = _wa(b, _sender(b), 'Is there a buyer fee?')
    web_stub, wa_stub = StubModel(['not json']), StubModel(['not json'])
    web = asyncio.run(website_send(b['crm'], wcid, cs.ChatSend(
        request_key=b['key'](), expected_version=1, body='Is there a buyer fee?'), web_stub))
    wa_out = receptionist.respond_turn(b['factory'], wa['convo']['id'], wa['message']['id'], model=wa_stub)
    assert web['reply']['body'] == wa_out['payload'] == FALLBACK_TEXT
    assert web['reply']['unsupported_reason'] == 'invalid_response'
    assert web_stub.calls == wa_stub.calls == 2
    assert _outbox_count(b, wcid) == 0 and _outbox_count(b, wa['convo']['id']) == 1


def test_pp08_replay_creates_no_duplicates_on_either_channel(pp):
    b = pp
    opened = _web(b)
    wcid = opened['conversation']['id']
    wa = _wa(b, _sender(b), 'My budget is 1cr to 1.5cr')
    for lead_id, email in ((opened['lead']['id'], b['prefix'] + '@example.invalid'),
                             (wa['lead']['id'], 'wa-' + b['prefix'] + '@example.invalid')):
        b['crm'].save('leads', s.LeadUpdate(
            request_key=b['key'](), expected_version=1, name=b['prefix'], email=email,
            preferred_localities=['Whitefield'], bedrooms=2,
            purchase_timeframe='within_3_months'), lead_id)
    text = _tout('qualification', [dict(question_id='Q_BUDGET', args={})],
                 requirements={'budget_min': '10000000', 'budget_max': '15000000'})
    web_stub, wa_stub = StubModel([text]), StubModel([text])
    key = b['key']()
    first = asyncio.run(website_send(b['crm'], wcid, cs.ChatSend(
        request_key=key, expected_version=1, body='My budget is 1cr to 1.5cr'), web_stub))
    assert asyncio.run(website_send(b['crm'], wcid, cs.ChatSend(
        request_key=key, expected_version=1, body='My budget is 1cr to 1.5cr'), web_stub)) == first
    wa_first = receptionist.respond_turn(b['factory'], wa['convo']['id'], wa['message']['id'], model=wa_stub)
    wa_again = receptionist.respond_turn(b['factory'], wa['convo']['id'], wa['message']['id'], model=wa_stub)
    assert wa_again.get('replay') is True and wa_again['payload'] == wa_first['payload']
    assert web_stub.calls == wa_stub.calls == 1
    assert _requirements(b, opened['lead']['id'])['version'] == 3
    assert _requirements(b, wa['lead']['id'])['version'] == 3
    assert len(_thread(b, wcid)) == 2 and len(_thread(b, wa['convo']['id'])) == 2


def test_pp09_cross_conversation_token_rejected_without_writes(pp):
    from app.chat import scope
    from app.visitor_tokens import verify
    b = pp
    first, second = _web(b), _web(b)
    cid_a = verify(first['token'], b['settings'])
    cid_b = second['conversation']['id']
    assert str(cid_a) != str(cid_b)
    with b['conn'].cursor() as cur:
        before = (scalar(cur.execute('SELECT count(*) FROM soloestate.conversation_messages WHERE conversation_id=%s',
                                     (cid_b,)).fetchone()),
                  scalar(cur.execute('SELECT count(*) FROM soloestate.crm_operations WHERE entity_id=%s',
                                     (cid_b,)).fetchone()))
    with pytest.raises(CRMError, match='does not authorize') as exc:
        scope(cid_a, cid_b)
    assert exc.value.status == 403
    with b['conn'].cursor() as cur:
        after = (scalar(cur.execute('SELECT count(*) FROM soloestate.conversation_messages WHERE conversation_id=%s',
                                    (cid_b,)).fetchone()),
                 scalar(cur.execute('SELECT count(*) FROM soloestate.crm_operations WHERE entity_id=%s',
                                    (cid_b,)).fetchone()))
    assert after == before


def test_pp10_freeze_parity(pp):
    from app.conversation import review as owner_review
    from app.crm_schemas import ReviewUpdate
    b = pp
    opened = _web(b)
    wcid = opened['conversation']['id']
    wa = _wa(b, _sender(b), 'Is there a buyer fee?')
    owner_review(b['crm'], wcid, ReviewUpdate(request_key=b['key'](), expected_version=1,
                                               action='resolve', reason='Fictional owner resolve'))
    with b['conn'].cursor() as cur:
        cur.execute("UPDATE soloestate.conversations SET status='resolved' WHERE id=%s", (wa['convo']['id'],))
    web_stub, wa_stub = StubModel([_tout('faq', [])]), StubModel([_tout('faq', [])])
    with pytest.raises(CRMError) as exc:
        asyncio.run(website_send(b['crm'], wcid, cs.ChatSend(
            request_key=b['key'](), expected_version=2, body='After resolve?'), web_stub))
    assert exc.value.status == 409
    message = None
    with b['conn'].cursor(row_factory=dict_row) as cur:
        message = cur.execute('INSERT INTO soloestate.conversation_messages(id,conversation_id,seq,role,body,request_key) '
                              'VALUES (%s,%s,%s,%s,%s,%s) RETURNING *',
                              (uuid4(), wa['convo']['id'], 2, 'customer', 'After resolve?', b['key']())).fetchone()
    wa_out = receptionist.respond_turn(b['factory'], wa['convo']['id'], message['id'], model=wa_stub)
    assert wa_out == {'replied': False, 'reason': 'resolved'}
    assert web_stub.calls == wa_stub.calls == 0
    assert len(_thread(b, wcid)) == 0
    with b['conn'].cursor(row_factory=dict_row) as cur:
        assert cur.execute("SELECT count(*) FROM soloestate.delivery_outbox WHERE conversation_id=%s",
                           (wa['convo']['id'],)).fetchone()['count'] == 0


def test_pp11_website_turn_never_stages_or_calls_provider(pp, monkeypatch):
    import app.dispatch as dispatch_mod
    b = pp
    _enable_policy(b)
    web_start, web_end = clean_window(b['conn'], weekday=3, hour=11)
    opened = _web(b)
    wcid = opened['conversation']['id']
    b['crm'].save('properties', s.PropertyCreate(
        request_key=b['key'](), title=b['prefix'] + ' Residency', locality=b['prefix'], price=150, bedrooms=2))
    b['crm'].save('leads', s.LeadUpdate(
        request_key=b['key'](), expected_version=1, name=b['prefix'], email=b['prefix'] + '@example.invalid',
        preferred_localities=[b['prefix']], budget_min='100', budget_max='200', bedrooms=2,
        purchase_timeframe='within_3_months'), opened['lead']['id'])

    def explode(*args, **kwargs):
        raise AssertionError('website turn must never stage a delivery')

    monkeypatch.setattr(dispatch_mod, 'stage_in_txn', explode)
    first = asyncio.run(website_send(b['crm'], wcid, cs.ChatSend(
        request_key=b['key'](), expected_version=1, body='I want to visit'), StubModel([
            _tout('availability', [dict(question_id='Q_CLARIFY', args={})],
                  booking={'start': web_start.isoformat(), 'end': web_end.isoformat()})])))
    assert 'Just to confirm' in first['reply']['body']
    request = _request_row(b, wcid)
    second = asyncio.run(website_send(b['crm'], wcid, cs.ChatSend(
        request_key=b['key'](), expected_version=first['conversation']['version'], body='Yes, confirm'),
        StubModel([_tout('availability', [dict(question_id='Q_CLARIFY', args={})],
                         confirm_request=str(request['id']))])))
    assert 'confirmed for' in second['reply']['body']
    assert _booking_for(b, opened['lead']['id'])['status'] == 'confirmed'
    assert _outbox_count(b, wcid) == 0
