"""Phase C real PostgreSQL website-chat checks; fictional rollback records, except concurrency fixtures committed on separate connections with exact-ID cleanup."""
import asyncio
import secrets
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event, Thread
from zoneinfo import ZoneInfo
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from app import chat_schemas as s
from app.chat import matches as chat_matches
from app.chat import request_handoff, requirements, resume, scope, send, set_context, slots as chat_slots, start
from app.chat_api import chat_settings
from app.config import Settings
from app.conversation import detail as owner_detail
from app.conversation import handoff as owner_handoff
from app.conversation import draft as owner_draft
from app.conversation import review as owner_review
from app.crm import CRM, CRMError
from app.crm_api import service
from app.crm_schemas import DraftRequest, FaqCreate, HandoffUpdate, LeadCreate, MessageAppend, PropertyCreate, ReviewUpdate, SlotCreate
from pydantic import ValidationError
from app.chat import RATE_LIMIT_PER_HOUR
from app.conversation import FIXED_UNSUPPORTED_BODY, append
from app.faq import create as faq_create
from app.main import create_app
from app.services.meta_model import ModelResult, ModelServiceError
from app.database import connect
from app.visitor_tokens import mint, verify

pytestmark = pytest.mark.integrity_database

ANSWER = 'No buyer fee applies in Bengaluru for this listing.'
QUESTION = 'Is there a buyer fee on this listing?'


class StubModel:
    def __init__(self, text=None, error=None):
        self.text = text
        self.error = error
        self.calls = 0

    async def generate(self, prompt):
        self.calls += 1
        assert len(prompt) <= 20000
        if self.error:
            raise self.error
        return ModelResult(response_id='resp-test', model='test-meta', text=self.text)


def grounded_text(faq_id):
    return ('{"answer": "A verified reply.", "citations": [{"faq_id": "%s", "version": 1, '
            '"quote": "%s"}], "confidence": "high"}') % (faq_id, ANSWER)


@pytest.fixture
def chat(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    with connect(Settings()) as conn:
        with conn.transaction(force_rollback=True):
            @contextmanager
            def factory():
                with conn.transaction():
                    yield conn
            settings = Settings(visitor_token_secret=secrets.token_urlsafe(32))
            prefix = 'C-fictional-' + str(uuid4())
            yield dict(crm=CRM(factory), conn=conn, settings=settings, prefix=prefix,
                       key=lambda: prefix + ':' + str(uuid4()))


def begin(b, name=None, email=None):
    return start(b['crm'], s.ChatStart(request_key=b['key'](), name=name or b['prefix'],
                                       email=email or (b['prefix'] + '@example.invalid')), b['settings'])


def faq(b):
    return faq_create(b['crm'], FaqCreate(request_key=b['key'](), scope='business',
                                          question=QUESTION, answer=ANSWER))['record']


def cleanup_committed(prefix, cid, faq_id, lead_id, property_ids=()):
    with connect(Settings()) as conn:
        ids = [row[0] for row in conn.execute('SELECT id FROM soloestate.conversation_messages WHERE conversation_id=%s',
                                              (cid,)).fetchall()]
        conn.execute('DELETE FROM soloestate.conversation_messages WHERE conversation_id=%s', (cid,))
        conn.execute('DELETE FROM soloestate.conversations WHERE id=%s', (cid,))
        conn.execute('DELETE FROM soloestate.lead_activities WHERE lead_id=%s', (lead_id,))
        conn.execute('DELETE FROM soloestate.crm_operations WHERE request_key LIKE %s', (prefix + ':%',))
        for message_id in ids:
            conn.execute('DELETE FROM soloestate.crm_operations WHERE request_key=%s', (f'chat:draft:{message_id}',))
        conn.execute('DELETE FROM soloestate.faq_versions WHERE faq_id=%s', (faq_id,))
        conn.execute('DELETE FROM soloestate.faqs WHERE id=%s', (faq_id,))
        for property_id in property_ids:
            conn.execute('DELETE FROM soloestate.properties WHERE id=%s', (property_id,))
        conn.execute('DELETE FROM soloestate.leads WHERE id=%s', (lead_id,))


EMPTY = '{"answer": "x", "citations": [], "confidence": "low"}'


def says(b, opened, body='Is there a buyer fee?', model=None, **kw):
    base = dict(request_key=b['key'](), expected_version=opened['conversation']['version'], body=body)
    return asyncio.run(send(b['crm'], opened['conversation']['id'],
                            s.ChatSend(**{**base, **kw}), model or StubModel(EMPTY)))


def test_start_opens_isolated_lead_conversation_and_token(chat):
    b = chat
    opened = begin(b)
    assert set(opened) == {'lead', 'conversation', 'messages', 'token'}
    assert set(opened['lead']) == {'id', 'version', 'name', 'preferred_localities', 'budget_min',
                                   'budget_max', 'bedrooms', 'purchase_timeframe', 'readiness'}
    assert set(opened['conversation']) == {'id', 'lead_id', 'status', 'handoff_state', 'property_id', 'version', 'created_at'}
    assert opened['messages'] == []
    assert (opened['conversation']['status'], opened['conversation']['version']) == ('open', 1)
    assert opened['lead']['version'] == 1 and opened['lead']['readiness']['matching_ready'] is False
    assert str(verify(opened['token'], b['settings'])) == opened['conversation']['id']
    thread = resume(b['crm'], opened['conversation']['id'])
    assert thread['conversation']['id'] == opened['conversation']['id'] and thread['messages'] == []
    assert thread['truncated'] is False and thread['drafting_blocked'] is None
    row = b['conn'].execute('SELECT status,journey_stage,contact_permission,email,notes FROM soloestate.leads WHERE id=%s',
                            (opened['lead']['id'],)).fetchone()
    assert tuple(row) == ('new', 'new', 'unknown', (b['prefix'] + '@example.invalid').lower(), '')


def test_start_replay_returns_identical_lead_conversation_token(chat):
    b = chat
    req = s.ChatStart(request_key=b['key'](), name=b['prefix'], email=b['prefix'] + '@example.invalid')
    first = start(b['crm'], req, b['settings'])
    assert start(b['crm'], req, b['settings']) == first
    assert b['conn'].execute('SELECT count(*) FROM soloestate.leads WHERE name=%s', (b['prefix'],)).fetchone()[0] == 1
    assert b['conn'].execute('SELECT count(*) FROM soloestate.conversations WHERE lead_id=%s',
                             (first['lead']['id'],)).fetchone()[0] == 1


def test_existing_archived_repeat_emails_indistinguishable_without_access(chat):
    b = chat
    taken = 'C-taken-' + str(uuid4()) + '@example.invalid'
    dormant = 'C-dormant-' + str(uuid4()) + '@example.invalid'
    kept = b['crm'].save('leads', LeadCreate(request_key=b['key'](), name='C kept', email=taken))['record']
    rest = b['crm'].save('leads', LeadCreate(request_key=b['key'](), name='C dormant', email=dormant))['record']
    b['conn'].execute('UPDATE soloestate.leads SET archived_at=clock_timestamp() WHERE id=%s', (rest['id'],))
    fresh, collision, archived, repeat = (begin(b, email=email)['conversation'] for email in
                                          (b['prefix'] + '@example.invalid', taken, dormant, taken))
    for convo in (fresh, collision, archived, repeat):
        assert (convo['status'], convo['version']) == ('open', 1)
    assert len({fresh['id'], collision['id'], archived['id'], repeat['id']}) == 4
    assert len({fresh['lead_id'], collision['lead_id'], archived['lead_id'], repeat['lead_id']}) == 4
    assert kept['id'] not in (fresh['lead_id'], collision['lead_id'], archived['lead_id'], repeat['lead_id'])
    assert rest['id'] not in (fresh['lead_id'], collision['lead_id'], archived['lead_id'], repeat['lead_id'])
    stored = b['conn'].execute('SELECT email, notes FROM soloestate.leads WHERE id=%s', (collision['lead_id'],)).fetchone()
    assert stored[0].startswith(taken.lower().split('@')[0] + '+chat-')
    assert stored[1] == f'Visitor asserted contact: {taken.lower()}'
    assert b['conn'].execute('SELECT version, notes FROM soloestate.leads WHERE id=%s', (kept['id'],)).fetchone() == (1, '')
    assert b['conn'].execute('SELECT count(*) FROM soloestate.lead_activities WHERE lead_id IN (%s,%s)',
                             (kept['id'], rest['id'])).fetchone()[0] == 0


def test_invalid_start_rejected_without_writes(chat):
    b = chat
    with pytest.raises(ValidationError):
        s.ChatStart(request_key=b['key'](), name=b['prefix'], email='not-an-email')
    with pytest.raises(ValidationError):
        s.ChatStart(request_key=b['key'](), name='   ', email=b['prefix'] + '@example.invalid')
    assert b['conn'].execute('SELECT count(*) FROM soloestate.leads WHERE name=%s', (b['prefix'],)).fetchone()[0] == 0
    assert b['conn'].execute('SELECT count(*) FROM soloestate.crm_operations WHERE request_key LIKE %s',
                             (b['prefix'] + ':%',)).fetchone()[0] == 0


def test_token_scope_and_missing_conversation_rejected_without_writes(chat):
    b = chat
    first, second = begin(b), begin(b)
    with pytest.raises(CRMError) as exc:
        scope(verify(first['token'], b['settings']), second['conversation']['id'])
    assert exc.value.status == 403
    ghost = mint(uuid4(), b['settings'])
    with pytest.raises(CRMError) as exc:
        resume(b['crm'], verify(ghost, b['settings']))
    assert exc.value.status == 404
    assert b['conn'].execute('SELECT count(*) FROM soloestate.conversation_messages WHERE conversation_id IN (%s,%s)',
                             (first['conversation']['id'], second['conversation']['id'])).fetchone()[0] == 0


def test_owner_and_chat_credentials_reject_each_other(chat):
    b = chat
    opened = begin(b)
    app = create_app()
    app.dependency_overrides[service] = lambda: b['crm']
    app.dependency_overrides[chat_settings] = lambda: b['settings']
    with TestClient(app) as api:
        assert api.get('/owner/crm/summary', headers={'Authorization': 'Bearer ' + opened['token']}).status_code == 401
        assert api.get('/chat/conversations/' + opened['conversation']['id']).status_code == 401
        assert api.get('/chat/conversations/' + opened['conversation']['id'],
                       headers={'Authorization': 'Bearer sess_owner_credential'}).status_code == 401
        mine = api.get('/chat/conversations/' + opened['conversation']['id'],
                       headers={'Authorization': 'Bearer ' + opened['token']}).json()
        assert mine['conversation']['id'] == opened['conversation']['id']
        assert set(mine) == {'lead', 'conversation', 'messages', 'truncated', 'drafting_blocked'}


def test_send_returns_grounded_reply_with_pinned_citations(chat):
    b = chat
    opened = begin(b)
    record = faq(b)
    stub = StubModel(grounded_text(record['id']))
    result = says(b, opened, model=stub)
    assert set(result) == {'message', 'reply', 'conversation'}
    assert set(result['reply']) == {'id', 'role', 'body', 'grounding', 'unsupported_reason', 'citations',
                                    'citations_superseded', 'confidence', 'supersedes_message_id', 'created_at'}
    assert (result['reply']['grounding'], result['reply']['confidence']) == ('grounded', 'high')
    assert result['reply']['citations'] == [{'faq_id': str(record['id']), 'version': 1, 'quote': ANSWER}]
    assert result['reply']['citations_superseded'] is False
    assert result['conversation']['status'] == 'open' and stub.calls == 1


def test_unsupported_question_moves_to_pending_owner_and_blocks(chat):
    b = chat
    opened = begin(b)
    result = says(b, opened, model=StubModel(EMPTY))
    assert (result['reply']['grounding'], result['reply']['unsupported_reason']) == ('unsupported', 'missing_citations')
    assert result['reply']['body'] == FIXED_UNSUPPORTED_BODY
    assert result['conversation']['status'] == 'pending_owner'
    entry = b['conn'].execute('SELECT actor,reason FROM soloestate.lead_activities WHERE lead_id=%s',
                              (opened['lead']['id'],)).fetchall()
    assert ('visitor', 'Assistant answer needs owner review') in [tuple(row) for row in entry]
    blocked = StubModel(grounded_text(faq(b)['id']))
    with pytest.raises(CRMError, match='Owner review is pending'):
        says(b, opened, body='Another question?', expected_version=2, model=blocked)
    assert blocked.calls == 0
    assert b['conn'].execute('SELECT count(*) FROM soloestate.conversation_messages WHERE conversation_id=%s',
                             (opened['conversation']['id'],)).fetchone()[0] == 2


@pytest.mark.parametrize('failure,reason', [
    (ModelServiceError('timeout', 'Simulated timeout'), 'inference_timeout'),
    (None, 'invalid_response'),
])
def test_model_timeout_and_malformed_use_unsupported_path(chat, failure, reason):
    b = chat
    opened = begin(b)
    stub = StubModel(error=failure) if failure else StubModel('not json')
    result = says(b, opened, model=stub)
    assert (result['reply']['grounding'], result['reply']['unsupported_reason']) == ('unsupported', reason)
    assert result['conversation']['status'] == 'pending_owner'


def test_duplicate_reply_attempts_with_new_keys_create_no_second_reply(chat):
    b = chat
    opened = begin(b)
    record = faq(b)
    stub = StubModel(grounded_text(record['id']))
    first = says(b, opened, model=stub)
    key = b['key']()
    again = asyncio.run(send(b['crm'], opened['conversation']['id'],
                             s.ChatSend(request_key=key, expected_version=1, body='Is there a buyer fee?'), stub))
    assert again['reply']['id'] != first['reply']['id']
    replay = asyncio.run(send(b['crm'], opened['conversation']['id'],
                              s.ChatSend(request_key=key, expected_version=1, body='Is there a buyer fee?'), stub))
    assert replay == again
    rows = b['conn'].execute("SELECT role, count(*) FROM soloestate.conversation_messages WHERE conversation_id=%s "
                             'GROUP BY role', (opened['conversation']['id'],)).fetchall()
    assert sorted((row[0], row[1]) for row in rows) == [('assistant', 2), ('customer', 2)]
    assert stub.calls == 2


def test_replay_at_rate_limit_returns_stored_reply_without_inference(chat):
    b = chat
    opened = begin(b)
    cid = opened['conversation']['id']
    for _ in range(RATE_LIMIT_PER_HOUR - 1):
        append(b['crm'], cid, MessageAppend(request_key=b['key'](), expected_version=1,
                                            role='customer', body='Filler question?'), actor='visitor',
               rate_limit_per_hour=RATE_LIMIT_PER_HOUR)
    record = faq(b)
    stub = StubModel(grounded_text(record['id']))
    key = b['key']()
    stored = asyncio.run(send(b['crm'], cid, s.ChatSend(request_key=key, expected_version=1,
                                                        body='Is there a buyer fee?'), stub))
    assert stub.calls == 1
    with pytest.raises(CRMError) as exc:
        says(b, opened, body='Over the limit?')
    assert exc.value.status == 429
    assert asyncio.run(send(b['crm'], cid, s.ChatSend(request_key=key, expected_version=1,
                                                      body='Is there a buyer fee?'), stub)) == stored
    assert stub.calls == 1
    assert b['conn'].execute('SELECT count(*) FROM soloestate.conversation_messages WHERE conversation_id=%s',
                             (cid,)).fetchone()[0] == RATE_LIMIT_PER_HOUR + 1


def test_concurrent_sends_serialize_with_one_reply_each(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    settings = Settings(visitor_token_secret=secrets.token_urlsafe(32))
    with connect(Settings()) as conn:
        prefix = 'C-race-' + str(uuid4())
        key = lambda: prefix + ':' + str(uuid4())
        crm = CRM()
        opened = start(crm, s.ChatStart(request_key=key(), name=prefix, email=prefix + '@example.invalid'), settings)
        record = faq_create(crm, FaqCreate(request_key=key(), scope='business', question=QUESTION, answer=ANSWER))['record']
    cid, faq_id, lead_id = opened['conversation']['id'], record['id'], opened['lead']['id']
    try:
        gate = Barrier(2)
        bodies = ['First concurrent question?', 'Second concurrent question?']
        keys = [prefix + ':send-a', prefix + ':send-b']

        def attempt(index):
            gate.wait(timeout=30)
            stub = StubModel(grounded_text(faq_id))
            return asyncio.run(send(CRM(), cid, s.ChatSend(request_key=keys[index], expected_version=1,
                                                           body=bodies[index]), stub))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, [0, 1]))
        assert results[0]['reply']['id'] != results[1]['reply']['id']
        with connect(Settings()) as conn:
            rows = conn.execute('SELECT role, count(*) FROM soloestate.conversation_messages WHERE conversation_id=%s '
                                'GROUP BY role', (cid,)).fetchall()
            assert sorted((row[0], row[1]) for row in rows) == [('assistant', 2), ('customer', 2)]
    finally:
        cleanup_committed(prefix, cid, faq_id, lead_id)


def test_concurrent_same_key_sends_execute_once(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    settings = Settings(visitor_token_secret=secrets.token_urlsafe(32))
    with connect(Settings()) as conn:
        prefix = 'C-replay-' + str(uuid4())
        key = lambda: prefix + ':' + str(uuid4())
        crm = CRM()
        opened = start(crm, s.ChatStart(request_key=key(), name=prefix, email=prefix + '@example.invalid'), settings)
        record = faq_create(crm, FaqCreate(request_key=key(), scope='business', question=QUESTION, answer=ANSWER))['record']
    cid, faq_id, lead_id = opened['conversation']['id'], record['id'], opened['lead']['id']
    try:
        gate = Barrier(2)
        shared = prefix + ':shared-send'
        stub = StubModel(grounded_text(faq_id))

        def attempt(_):
            gate.wait(timeout=30)
            return asyncio.run(send(CRM(), cid, s.ChatSend(request_key=shared, expected_version=1,
                                                           body='Is there a buyer fee?'), stub))
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(attempt, [0, 1]))
        assert first == second
        assert stub.calls == 1
        with connect(Settings()) as conn:
            assert conn.execute('SELECT count(*) FROM soloestate.conversation_messages WHERE conversation_id=%s',
                                (cid,)).fetchone()[0] == 2
    finally:
        cleanup_committed(prefix, cid, faq_id, lead_id)


def test_crash_between_append_and_draft_resumes_exactly_one_reply(chat):
    b = chat
    opened = begin(b)
    record = faq(b)
    key = b['key']()
    orphan = append(b['crm'], opened['conversation']['id'],
                    MessageAppend(request_key=key, expected_version=1, role='customer',
                                  body='Is there a buyer fee?'), actor='visitor',
                    rate_limit_per_hour=RATE_LIMIT_PER_HOUR)['record']
    stub = StubModel(grounded_text(record['id']))
    resumed = asyncio.run(send(b['crm'], opened['conversation']['id'],
                               s.ChatSend(request_key=key, expected_version=1, body='Is there a buyer fee?'), stub))
    assert resumed['message']['id'] == orphan['id']
    assert resumed['reply']['grounding'] == 'grounded' and stub.calls == 1
    assert asyncio.run(send(b['crm'], opened['conversation']['id'],
                            s.ChatSend(request_key=key, expected_version=1, body='Is there a buyer fee?'), stub)) == resumed
    assert stub.calls == 1


def test_stale_send_returns_resync_without_writes(chat):
    b = chat
    opened = begin(b)
    says(b, opened, model=StubModel('{"answer": "x", "citations": []}'))
    with pytest.raises(CRMError, match='Record changed'):
        says(b, opened, body='Stale question?', expected_version=1)
    assert b['conn'].execute('SELECT count(*) FROM soloestate.conversation_messages WHERE conversation_id=%s',
                             (opened['conversation']['id'],)).fetchone()[0] == 2


def test_requirements_update_refines_own_lead_only(chat):
    b = chat
    opened = begin(b)
    cid = opened['conversation']['id']
    updated = requirements(b['crm'], cid, s.ChatRequirements(
        request_key=b['key'](), expected_version=1, preferred_localities=['  Bellandur ', 'bellandur'],
        budget_min='100.00', budget_max='200.00', bedrooms=2, purchase_timeframe='within_3_months'))['lead']
    assert updated['version'] == 2 and updated['preferred_localities'] == ['Bellandur']
    assert updated['readiness']['matching_ready'] is True
    assert set(updated) == {'id', 'version', 'name', 'preferred_localities', 'budget_min', 'budget_max',
                            'bedrooms', 'purchase_timeframe', 'readiness'}
    entry = b['conn'].execute('SELECT actor,reason FROM soloestate.lead_activities WHERE lead_id=%s ORDER BY created_at DESC',
                              (opened['lead']['id'],)).fetchone()
    assert tuple(entry) == ('visitor', 'Visitor updated chat requirements')
    assert b['conn'].execute('SELECT qualification_fingerprint FROM soloestate.leads WHERE id=%s',
                             (opened['lead']['id'],)).fetchone()[0] is None
    with pytest.raises(ValidationError):
        s.ChatRequirements(request_key=b['key'](), expected_version=2, budget_min='300.00', budget_max='200.00')


def test_matches_and_slots_scoped_to_own_lead(chat):
    b = chat
    opened = begin(b)
    cid = opened['conversation']['id']
    empty = chat_matches(b['crm'], cid)
    assert empty['matching_ready'] is False and empty['matches'] == []
    requirements(b['crm'], cid, s.ChatRequirements(
        request_key=b['key'](), expected_version=1, preferred_localities=[b['prefix']], budget_min='100',
        budget_max='200', bedrooms=2))
    prop = b['crm'].save('properties', PropertyCreate(request_key=b['key'](), title=b['prefix'], locality=b['prefix'],
                                                      price=150, bedrooms=2))['record']
    found = chat_matches(b['crm'], cid)
    assert found['matching_ready'] is True and [match['id'] for match in found['matches']] == [prop['id']]
    assert found['lead_id'] == opened['lead']['id']
    base = b['conn'].execute("SELECT greatest(coalesce(max(ends_at),clock_timestamp()),clock_timestamp())+interval '20 years' "
                               'FROM soloestate.visit_slots').fetchone()[0].astimezone(ZoneInfo('Asia/Kolkata'))
    start = (base + timedelta(days=(2 - base.weekday()) % 7)).replace(hour=11, minute=0, second=0, microsecond=0)
    if start <= base:
        start += timedelta(days=7)
    slot = b['crm'].slot(SlotCreate(request_key=b['key'](), starts_at=start,
                                   ends_at=start + timedelta(minutes=30)))['record']
    window = chat_slots(b['crm'], start - timedelta(days=1), start + timedelta(days=1))
    assert window['provisional'] is True and slot['id'] in [item['id'] for item in window['items']]
    with pytest.raises(CRMError) as exc:
        chat_slots(b['crm'], start, start + timedelta(days=32))
    assert exc.value.status == 422


def test_handoff_request_and_owner_resolve_freeze_chat(chat):
    b = chat
    opened = begin(b)
    cid = opened['conversation']['id']
    requested = request_handoff(b['crm'], cid, s.ChatHandoff(request_key=b['key'](), expected_version=1,
                                                             reason='Fictional visitor handoff'))['conversation']
    assert requested['handoff_state'] == 'requested'
    with pytest.raises(CRMError, match='handoff'):
        says(b, opened, body='Blocked question?', expected_version=2)
    assert b['conn'].execute('SELECT count(*) FROM soloestate.conversation_messages WHERE conversation_id=%s',
                             (cid,)).fetchone()[0] == 0
    with pytest.raises(CRMError, match='Invalid handoff transition'):
        request_handoff(b['crm'], cid, s.ChatHandoff(request_key=b['key'](), expected_version=2,
                                                     reason='Fictional duplicate handoff'))
    resolved = owner_handoff(b['crm'], cid, HandoffUpdate(request_key=b['key'](), expected_version=2,
                                                          action='resolve', reason='Fictional owner resolve'))['record']
    assert resolved['handoff_state'] == 'resolved'
    actors = [tuple(row) for row in b['conn'].execute('SELECT actor,reason FROM soloestate.lead_activities WHERE lead_id=%s ORDER BY created_at',
                                                      (opened['lead']['id'],)).fetchall()]
    assert ('visitor', 'Fictional visitor handoff') in actors and ('owner', 'Fictional owner resolve') in actors


def test_mixed_visitor_and_owner_thread_with_truthful_provenance(chat):
    b = chat
    opened = begin(b)
    cid = opened['conversation']['id']
    faq_key = b['key']()
    record = faq_create(b['crm'], FaqCreate(request_key=faq_key, scope='business', question=QUESTION,
                                            answer=ANSWER))['record']
    says(b, opened, model=StubModel(grounded_text(record['id'])))
    owner_key = b['key']()
    append(b['crm'], cid, MessageAppend(request_key=owner_key, expected_version=1, role='owner',
                                        body='Fictional owner follow-up.'))['record']
    thread = resume(b['crm'], cid)
    assert [message['role'] for message in thread['messages']] == ['customer', 'assistant', 'owner']
    assert thread['messages'][2]['body'] == 'Fictional owner follow-up.'
    actors = {row[0]: row[1] for row in b['conn'].execute(
        'SELECT request_key,actor FROM soloestate.crm_operations WHERE request_key LIKE %s', (b['prefix'] + ':%',)).fetchall()}
    assert actors[owner_key] == 'owner' and actors[faq_key] == 'owner'
    assert all(actor == 'visitor' for key, actor in actors.items() if key not in (owner_key, faq_key))
    assert b['conn'].execute("SELECT count(*) FROM soloestate.crm_operations WHERE request_key LIKE 'chat:draft:%' "
                             "AND actor<>'visitor'").fetchone()[0] == 0


def test_activity_failure_rolls_back_start_and_send_draft(chat, monkeypatch):
    import app.conversation as conversation_mod
    b = chat
    opened = begin(b)
    record = faq(b)
    def fail(*args, **kwargs):
        raise RuntimeError('Simulated activity outage')
    monkeypatch.setattr(conversation_mod, 'log_activity', fail)
    with pytest.raises(RuntimeError, match='Simulated activity outage'):
        begin(b)
    assert b['conn'].execute('SELECT count(*) FROM soloestate.leads WHERE name=%s', (b['prefix'],)).fetchone()[0] == 1
    failing = StubModel(EMPTY)
    with pytest.raises(RuntimeError, match='Simulated activity outage'):
        says(b, opened, model=failing)
    rows = b['conn'].execute('SELECT role FROM soloestate.conversation_messages WHERE conversation_id=%s ORDER BY created_at',
                             (opened['conversation']['id'],)).fetchall()
    assert [row[0] for row in rows] == ['customer']
    assert b['conn'].execute("SELECT count(*) FROM soloestate.crm_operations WHERE request_key LIKE 'chat:draft:%'",
                             ).fetchone()[0] == 0


def test_owner_http_chat_flow(chat, monkeypatch):
    import app.services.meta_model as meta_model
    b = chat
    opened = begin(b)
    record = faq(b)
    stub = StubModel(grounded_text(record['id']))
    class Service:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return stub
        async def __aexit__(self, *exc):
            return False
    monkeypatch.setattr(meta_model, 'MetaModelService', Service)
    app = create_app()
    app.dependency_overrides[service] = lambda: b['crm']
    app.dependency_overrides[chat_settings] = lambda: b['settings']
    headers = {'Authorization': 'Bearer ' + opened['token']}
    cid = opened['conversation']['id']
    with TestClient(app) as api:
        sent = api.post(f'/chat/conversations/{cid}/send', headers=headers,
                        json={'request_key': b['key'](), 'expected_version': 1,
                              'body': 'Is there a buyer fee?'}).json()
        assert sent['reply']['grounding'] == 'grounded'
        assert api.post(f'/chat/conversations/{cid}/requirements', headers=headers,
                        json={'request_key': b['key'](), 'expected_version': 1,
                              'preferred_localities': [b['prefix']], 'budget_min': '100',
                              'budget_max': '200', 'bedrooms': 2}).json()['lead']['version'] == 2
        assert api.get(f'/chat/conversations/{cid}/matches', headers=headers).json()['matching_ready'] is True
        start_at = datetime.now(timezone.utc) + timedelta(days=7)
        end_at = start_at + timedelta(days=1)
        slots = api.get(f'/chat/conversations/{cid}/slots', headers=headers,
                        params={'since': start_at.isoformat(), 'until': end_at.isoformat()}).json()
        assert slots['provisional'] is True
        assert api.post(f'/chat/conversations/{cid}/handoff', headers=headers,
                        json={'request_key': b['key'](), 'expected_version': 1,
                              'reason': 'Fictional visitor handoff'}).json()['conversation']['handoff_state'] == 'requested'
        other = begin(b)
        assert api.get('/chat/conversations/' + other['conversation']['id'], headers=headers).status_code == 403
        assert api.get('/owner/crm/summary', headers=headers).status_code == 401
        assert api.post('/chat/start', json={'request_key': b['key']()}).status_code == 422


def snapshot(b, cid, lead_id):
    conn = b['conn']
    return (
        conn.execute('SELECT count(*) FROM soloestate.conversation_messages WHERE conversation_id=%s', (cid,)).fetchone()[0],
        conn.execute('SELECT version FROM soloestate.leads WHERE id=%s', (lead_id,)).fetchone()[0],
        conn.execute('SELECT version, status FROM soloestate.conversations WHERE id=%s', (cid,)).fetchone(),
        conn.execute('SELECT count(*) FROM soloestate.lead_activities WHERE lead_id=%s', (lead_id,)).fetchone()[0],
        conn.execute('SELECT count(*) FROM soloestate.crm_operations WHERE request_key LIKE %s', (b['prefix'] + ':%',)).fetchone()[0],
    )


def resolve_conversation(b, cid, version):
    return owner_review(b['crm'], cid, ReviewUpdate(request_key=b['key'](), expected_version=version,
                                                    action='resolve', reason='Fictional owner resolve'))['record']


def test_resolved_conversation_rejects_all_visitor_writes(chat):
    b = chat
    opened = begin(b)
    cid, lead_id = opened['conversation']['id'], opened['lead']['id']
    record = faq(b)
    stub = StubModel(grounded_text(record['id']))
    send_key = b['key']()
    stored = asyncio.run(send(b['crm'], cid, s.ChatSend(request_key=send_key, expected_version=1,
                                                        body='Is there a buyer fee?'), stub))
    assert stub.calls == 1
    resolved = resolve_conversation(b, cid, 1)
    assert (resolved['status'], resolved['version']) == ('resolved', 2)
    before = snapshot(b, cid, lead_id)
    with pytest.raises(CRMError, match='Reopen'):
        says(b, opened, body='After resolve?', expected_version=2, model=stub)
    with pytest.raises(CRMError, match='resolved') as exc:
        requirements(b['crm'], cid, s.ChatRequirements(request_key=b['key'](), expected_version=1,
                                                       preferred_localities=['Bellandur']))
    assert exc.value.status == 409
    with pytest.raises(CRMError, match='resolved'):
        request_handoff(b['crm'], cid, s.ChatHandoff(request_key=b['key'](), expected_version=2,
                                                     reason='Fictional late handoff'))
    assert stub.calls == 1
    assert snapshot(b, cid, lead_id) == before
    replayed = asyncio.run(send(b['crm'], cid, s.ChatSend(request_key=send_key, expected_version=1,
                                                           body='Is there a buyer fee?'), stub))
    assert replayed == stored and stub.calls == 1
    assert snapshot(b, cid, lead_id) == before


def test_pending_and_handoff_states_gate_sends_but_allow_requirements(chat):
    b = chat
    opened = begin(b)
    cid, lead_id = opened['conversation']['id'], opened['lead']['id']
    says(b, opened, model=StubModel(EMPTY))
    assert snapshot(b, cid, lead_id)[2] == (2, 'pending_owner')
    fresh = StubModel(EMPTY)
    with pytest.raises(CRMError, match='Owner review is pending'):
        says(b, opened, body='Still blocked?', expected_version=2, model=fresh)
    assert fresh.calls == 0
    assert snapshot(b, cid, lead_id)[0] == 2
    updated = requirements(b['crm'], cid, s.ChatRequirements(
        request_key=b['key'](), expected_version=1, preferred_localities=[b['prefix']]))['lead']
    assert updated['version'] == 2
    requested = request_handoff(b['crm'], cid, s.ChatHandoff(request_key=b['key'](), expected_version=2,
                                                             reason='Fictional visitor handoff'))['conversation']
    assert (requested['handoff_state'], requested['version']) == ('requested', 3)
    with pytest.raises(CRMError, match='handoff'):
        says(b, opened, body='Handoff blocked?', expected_version=3, model=fresh)
    assert fresh.calls == 0
    assert snapshot(b, cid, lead_id)[0] == 2
    owner_handoff(b['crm'], cid, HandoffUpdate(request_key=b['key'](), expected_version=3,
                                               action='resolve', reason='Fictional owner resolve'))
    with pytest.raises(CRMError, match='Owner review is pending'):
        says(b, opened, body='Pending again?', expected_version=4, model=fresh)
    owner_review(b['crm'], cid, ReviewUpdate(request_key=b['key'](), expected_version=4,
                                              action='acknowledge', reason='Fictional owner acknowledge'))
    record = faq(b)
    resumed = says(b, opened, body='Is there a buyer fee?', expected_version=5,
                   model=StubModel(grounded_text(record['id'])))
    assert resumed['reply']['grounding'] == 'grounded'


def test_concurrent_owner_resolve_and_visitor_write_serializes(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    settings = Settings(visitor_token_secret=secrets.token_urlsafe(32))
    with connect(Settings()) as conn:
        prefix = 'C-freeze-' + str(uuid4())
        key = lambda: prefix + ':' + str(uuid4())
        crm = CRM()
        opened = start(crm, s.ChatStart(request_key=key(), name=prefix, email=prefix + '@example.invalid'), settings)
    cid, lead_id = opened['conversation']['id'], opened['lead']['id']
    holder = connect(Settings())
    try:
        errors = []

        def attempt_send():
            try:
                asyncio.run(send(CRM(), cid, s.ChatSend(request_key=prefix + ':racy-send', expected_version=1,
                                                        body='Racy question?'), StubModel(EMPTY)))
            except Exception as exc:
                errors.append(exc)

        def attempt_requirements(version):
            try:
                requirements(CRM(), cid, s.ChatRequirements(request_key=prefix + ':racy-req', expected_version=version,
                                                            preferred_localities=['Bellandur']))
            except Exception as exc:
                errors.append(exc)

        holder.execute('SELECT * FROM soloestate.conversations WHERE id=%s FOR UPDATE', (cid,))
        worker = Thread(target=attempt_send)
        worker.start()
        worker.join(timeout=5)
        assert worker.is_alive()
        holder.execute("UPDATE soloestate.conversations SET status='resolved' WHERE id=%s", (cid,))
        holder.commit()
        worker.join(timeout=30)
        assert not worker.is_alive() and len(errors) == 1
        assert isinstance(errors[0], CRMError) and errors[0].status == 409
        with connect(Settings()) as conn:
            assert conn.execute('SELECT count(*) FROM soloestate.conversation_messages WHERE conversation_id=%s',
                                (cid,)).fetchone()[0] == 0
            assert conn.execute('SELECT count(*) FROM soloestate.crm_operations WHERE request_key=%s',
                                (prefix + ':racy-send',)).fetchone()[0] == 0
        owner_review(CRM(), cid, ReviewUpdate(request_key=key(), expected_version=2,
                                               action='reopen', reason='Fictional owner reopen'))
        errors.clear()
        holder.execute('SELECT * FROM soloestate.conversations WHERE id=%s FOR UPDATE', (cid,))
        worker = Thread(target=attempt_requirements, args=(1,))
        worker.start()
        worker.join(timeout=5)
        assert worker.is_alive()
        holder.execute("UPDATE soloestate.conversations SET status='resolved' WHERE id=%s", (cid,))
        holder.commit()
        worker.join(timeout=30)
        assert not worker.is_alive() and len(errors) == 1
        assert isinstance(errors[0], CRMError) and errors[0].status == 409
        with connect(Settings()) as conn:
            assert conn.execute('SELECT version FROM soloestate.leads WHERE id=%s', (lead_id,)).fetchone()[0] == 1
            assert conn.execute('SELECT count(*) FROM soloestate.crm_operations WHERE request_key=%s',
                                (prefix + ':racy-req',)).fetchone()[0] == 0
    finally:
        holder.close()
        cleanup_committed(prefix, cid, uuid4(), lead_id)


def test_stale_send_on_blocked_chat_rejects_without_writes(chat):
    b = chat
    opened = begin(b)
    cid = opened['conversation']['id']
    says(b, opened, model=StubModel(EMPTY))
    with pytest.raises(CRMError, match='Record changed'):
        says(b, opened, body='Stale question?', expected_version=1)
    resolve_conversation(b, cid, 2)
    with pytest.raises(CRMError, match='Record changed'):
        says(b, opened, body='Stale on resolved?', expected_version=1)
    assert b['conn'].execute('SELECT count(*) FROM soloestate.conversation_messages WHERE conversation_id=%s',
                             (cid,)).fetchone()[0] == 2


def test_state_change_between_append_and_draft_discloses_saved_message(chat):
    b = chat
    opened = begin(b)
    cid = opened['conversation']['id']
    key = b['key']()
    orphan = append(b['crm'], cid, MessageAppend(request_key=key, expected_version=1, role='customer',
                                                 body='Is there a buyer fee?'), actor='visitor',
                    rate_limit_per_hour=RATE_LIMIT_PER_HOUR)['record']
    resolve_conversation(b, cid, 1)
    stub = StubModel(EMPTY)
    with pytest.raises(CRMError, match='Your message was saved') as exc:
        asyncio.run(send(b['crm'], cid, s.ChatSend(request_key=key, expected_version=1,
                                                   body='Is there a buyer fee?'), stub))
    assert exc.value.status == 409
    assert stub.calls == 0
    rows = b['conn'].execute('SELECT id, role FROM soloestate.conversation_messages WHERE conversation_id=%s', (cid,)).fetchall()
    assert [(str(row[0]), row[1]) for row in rows] == [(orphan['id'], 'customer')]
    assert b['conn'].execute("SELECT count(*) FROM soloestate.crm_operations WHERE request_key LIKE 'chat:draft:%'",
                             ).fetchone()[0] == 0


def test_state_change_during_inference_discloses_saved_message(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    settings = Settings(visitor_token_secret=secrets.token_urlsafe(32))
    with connect(Settings()) as conn:
        prefix = 'C-midflight-' + str(uuid4())
        key = lambda: prefix + ':' + str(uuid4())
        crm = CRM()
        opened = start(crm, s.ChatStart(request_key=key(), name=prefix, email=prefix + '@example.invalid'), settings)
    cid, lead_id = opened['conversation']['id'], opened['lead']['id']
    entered, release = Event(), Event()
    calls = []

    class BlockingModel:
        async def generate(self, prompt):
            calls.append(prompt)
            entered.set()
            assert release.wait(timeout=60)
            return ModelResult(response_id='resp-test', model='test-meta', text=EMPTY)

    errors = []

    def attempt():
        try:
            asyncio.run(send(CRM(), cid, s.ChatSend(request_key=prefix + ':midflight', expected_version=1,
                                                    body='Is there a buyer fee?'), BlockingModel()))
        except Exception as exc:
            errors.append(exc)

    worker = Thread(target=attempt)
    try:
        worker.start()
        assert entered.wait(timeout=60)
        owner_review(CRM(), cid, ReviewUpdate(request_key=key(), expected_version=1,
                                               action='resolve', reason='Fictional owner resolve'))
        release.set()
        worker.join(timeout=60)
        assert not worker.is_alive() and len(errors) == 1
        assert isinstance(errors[0], CRMError) and errors[0].status == 409
        assert 'Your message was saved' in str(errors[0])
        assert len(calls) == 1
        with connect(Settings()) as conn:
            rows = conn.execute('SELECT role, count(*) FROM soloestate.conversation_messages WHERE conversation_id=%s '
                                'GROUP BY role', (cid,)).fetchall()
            assert sorted((row[0], row[1]) for row in rows) == [('customer', 1)]
    finally:
        release.set()
        worker.join(timeout=30)
        cleanup_committed(prefix, cid, uuid4(), lead_id)


def matching_setup(b, tag='A'):
    cid = begin(b)['conversation']['id']
    requirements(b['crm'], cid, s.ChatRequirements(
        request_key=b['key'](), expected_version=1, preferred_localities=[b['prefix']],
        budget_min='100', budget_max='200', bedrooms=2))
    prop = b['crm'].save('properties', PropertyCreate(request_key=b['key'](), title=f'{b["prefix"]}-{tag}',
                                                       locality=b['prefix'], price=150, bedrooms=2))['record']
    return cid, prop


def property_faq(b, prop, answer):
    return faq_create(b['crm'], FaqCreate(request_key=b['key'](), scope='property', property_id=prop['id'],
                                          question=f'{b["prefix"]} fee terms?', answer=answer))['record']


def test_property_context_lifecycle_and_replay(chat):
    b = chat
    cid, prop = matching_setup(b)
    key = b['key']()
    first = set_context(b['crm'], cid, s.ChatContext(request_key=key, expected_version=1, property_id=prop['id']))
    assert (first['conversation']['property_id'], first['conversation']['version']) == (prop['id'], 2)
    entry = b['conn'].execute('SELECT actor,reason FROM soloestate.lead_activities WHERE request_key=%s', (key,)).fetchone()
    assert tuple(entry) == ('visitor', 'Visitor selected property focus')
    assert set_context(b['crm'], cid, s.ChatContext(request_key=key, expected_version=1,
                                                    property_id=prop['id'])) == first
    assert b['conn'].execute('SELECT count(*) FROM soloestate.lead_activities WHERE request_key=%s', (key,)).fetchone()[0] == 1
    with pytest.raises(CRMError, match='Record changed'):
        set_context(b['crm'], cid, s.ChatContext(request_key=b['key'](), expected_version=1, property_id=prop['id']))
    resolve_conversation(b, cid, 2)
    with pytest.raises(CRMError, match='resolved') as exc:
        set_context(b['crm'], cid, s.ChatContext(request_key=b['key'](), expected_version=3, property_id=prop['id']))
    assert exc.value.status == 409


def test_property_context_rejects_invalid_or_unauthorized(chat):
    b = chat
    cid, prop = matching_setup(b)
    with pytest.raises(CRMError) as exc:
        set_context(b['crm'], cid, s.ChatContext(request_key=b['key'](), expected_version=1, property_id=uuid4()))
    assert exc.value.status == 422
    b['conn'].execute('UPDATE soloestate.properties SET active=false WHERE id=%s', (prop['id'],))
    with pytest.raises(CRMError, match='active'):
        set_context(b['crm'], cid, s.ChatContext(request_key=b['key'](), expected_version=1, property_id=prop['id']))
    b['conn'].execute('UPDATE soloestate.properties SET active=true WHERE id=%s', (prop['id'],))
    other = b['crm'].save('properties', PropertyCreate(request_key=b['key'](), title=b['prefix'] + '-far',
                                                        locality='Elsewhere', price=150, bedrooms=2))['record']
    with pytest.raises(CRMError, match='current matches'):
        set_context(b['crm'], cid, s.ChatContext(request_key=b['key'](), expected_version=1, property_id=other['id']))
    bare = begin(b)['conversation']['id']
    with pytest.raises(CRMError, match='current matches'):
        set_context(b['crm'], bare, s.ChatContext(request_key=b['key'](), expected_version=1, property_id=prop['id']))
    assert b['conn'].execute('SELECT property_id FROM soloestate.conversations WHERE id IN (%s,%s)',
                             (cid, bare)).fetchall() == [(None,), (None,)]


def test_property_context_grounds_scoped_answers(chat):
    b = chat
    cid, prop_a = matching_setup(b, 'A')
    prop_b = b['crm'].save('properties', PropertyCreate(request_key=b['key'](), title=b['prefix'] + '-B',
                                                         locality=b['prefix'], price=150, bedrooms=2))['record']
    business = faq(b)
    record_a = property_faq(b, prop_a, 'This home charges no buyer fee and includes parking.')
    property_faq(b, prop_b, 'The other home charges a buyer fee.')
    opened = {'conversation': {'id': cid, 'version': 1}}
    business_first = says(b, opened, body=QUESTION, model=StubModel(grounded_text(business['id'])))
    assert business_first['reply']['grounding'] == 'grounded'
    assert business_first['reply']['citations'] == [{'faq_id': str(business['id']), 'version': 1, 'quote': ANSWER}]
    set_context(b['crm'], cid, s.ChatContext(request_key=b['key'](), expected_version=1, property_id=prop_a['id']))
    scoped = says(b, {'conversation': {'id': cid, 'version': 2}}, body=f'{b["prefix"]} fee terms?',
                  model=StubModel(grounded_text(record_a['id']).replace(ANSWER, 'This home charges no buyer fee and includes parking.')))
    assert scoped['reply']['grounding'] == 'grounded'
    assert scoped['reply']['citations'] == [{'faq_id': str(record_a['id']), 'version': 1,
                                             'quote': 'This home charges no buyer fee and includes parking.'}]


def test_other_property_faqs_cannot_be_cited(chat):
    b = chat
    cid, prop_a = matching_setup(b, 'A')
    prop_b = b['crm'].save('properties', PropertyCreate(request_key=b['key'](), title=b['prefix'] + '-B',
                                                         locality=b['prefix'], price=150, bedrooms=2))['record']
    record_b = property_faq(b, prop_b, 'The other home charges a buyer fee.')
    set_context(b['crm'], cid, s.ChatContext(request_key=b['key'](), expected_version=1, property_id=prop_a['id']))
    result = says(b, {'conversation': {'id': cid, 'version': 2}}, body='What is the fee?',
                  model=StubModel(grounded_text(record_b['id']).replace(ANSWER, 'The other home charges a buyer fee.')))
    assert (result['reply']['grounding'], result['reply']['unsupported_reason']) == ('unsupported', 'unauthorized_faq')
    assert result['conversation']['status'] == 'pending_owner'


def test_old_citations_stay_pinned_after_focus_change(chat):
    b = chat
    cid, prop_a = matching_setup(b, 'A')
    prop_b = b['crm'].save('properties', PropertyCreate(request_key=b['key'](), title=b['prefix'] + '-B',
                                                         locality=b['prefix'], price=150, bedrooms=2))['record']
    record_a = property_faq(b, prop_a, 'This home charges no buyer fee and includes parking.')
    record_b = property_faq(b, prop_b, 'The other home charges a buyer fee.')
    set_context(b['crm'], cid, s.ChatContext(request_key=b['key'](), expected_version=1, property_id=prop_a['id']))
    first = says(b, {'conversation': {'id': cid, 'version': 2}}, body=f'{b["prefix"]} fee terms?',
                 model=StubModel(grounded_text(record_a['id']).replace(ANSWER, 'This home charges no buyer fee and includes parking.')))
    assert first['reply']['grounding'] == 'grounded'
    set_context(b['crm'], cid, s.ChatContext(request_key=b['key'](), expected_version=2, property_id=prop_b['id']))
    second = says(b, {'conversation': {'id': cid, 'version': 3}}, body='What is the fee?',
                  model=StubModel(grounded_text(record_b['id']).replace(ANSWER, 'The other home charges a buyer fee.')))
    assert second['reply']['grounding'] == 'grounded'
    thread = resume(b['crm'], cid)
    assert thread['conversation']['property_id'] == prop_b['id']
    bodies = [(message['body'], message['citations'], message['citations_superseded']) for message in thread['messages']
              if message['role'] == 'assistant']
    assert bodies[0][1] == [{'faq_id': str(record_a['id']), 'version': 1,
                             'quote': 'This home charges no buyer fee and includes parking.'}]
    assert bodies[0][0] == first['reply']['body'] and bodies[0][2] is False
    assert bodies[1][1] == [{'faq_id': str(record_b['id']), 'version': 1,
                             'quote': 'The other home charges a buyer fee.'}]


def test_context_change_during_inference_rejects_stale_scope(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    settings = Settings(visitor_token_secret=secrets.token_urlsafe(32))
    with connect(Settings()) as conn:
        prefix = 'C-focus-' + str(uuid4())
        key = lambda: prefix + ':' + str(uuid4())
        crm = CRM()
        opened = start(crm, s.ChatStart(request_key=key(), name=prefix, email=prefix + '@example.invalid'), settings)
        cid = opened['conversation']['id']
        requirements(crm, cid, s.ChatRequirements(request_key=key(), expected_version=1, preferred_localities=[prefix],
                                                  budget_min='100', budget_max='200', bedrooms=2))
        prop_a = crm.save('properties', PropertyCreate(request_key=key(), title=prefix + '-A', locality=prefix,
                                                        price=150, bedrooms=2))['record']
        prop_b = crm.save('properties', PropertyCreate(request_key=key(), title=prefix + '-B', locality=prefix,
                                                        price=150, bedrooms=2))['record']
        set_context(crm, cid, s.ChatContext(request_key=key(), expected_version=1, property_id=prop_a['id']))
    lead_id = opened['lead']['id']
    entered, release = Event(), Event()
    calls = []

    class BlockingModel:
        async def generate(self, prompt):
            calls.append(prompt)
            entered.set()
            assert release.wait(timeout=60)
            return ModelResult(response_id='resp-test', model='test-meta', text=EMPTY)

    errors = []

    def attempt():
        try:
            asyncio.run(send(CRM(), cid, s.ChatSend(request_key=prefix + ':midfocus', expected_version=2,
                                                    body='Fee terms?'), BlockingModel()))
        except Exception as exc:
            errors.append(exc)

    worker = Thread(target=attempt)
    try:
        worker.start()
        assert entered.wait(timeout=60)
        focused = set_context(CRM(), cid, s.ChatContext(request_key=key(), expected_version=2, property_id=prop_b['id']))
        assert focused['conversation']['property_id'] == prop_b['id']
        release.set()
        worker.join(timeout=60)
        assert not worker.is_alive() and len(errors) == 1
        assert isinstance(errors[0], CRMError) and errors[0].status == 409
        assert 'Property focus changed' in str(errors[0]) and 'Your message was saved' in str(errors[0])
        assert len(calls) == 1
        with connect(Settings()) as conn:
            rows = conn.execute('SELECT role, count(*) FROM soloestate.conversation_messages WHERE conversation_id=%s '
                                'GROUP BY role', (cid,)).fetchall()
            assert sorted((row[0], row[1]) for row in rows) == [('customer', 1)]
    finally:
        release.set()
        worker.join(timeout=30)
        cleanup_committed(prefix, cid, uuid4(), lead_id, [prop_a['id'], prop_b['id']])


def test_draft_claim_holds_concurrent_retry_without_inference(request):
    import time as clock
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    settings = Settings(visitor_token_secret=secrets.token_urlsafe(32))
    with connect(Settings()) as conn:
        prefix = 'C-claim-' + str(uuid4())
        key = lambda: prefix + ':' + str(uuid4())
        crm = CRM()
        opened = start(crm, s.ChatStart(request_key=key(), name=prefix, email=prefix + '@example.invalid'), settings)
        record = faq_create(crm, FaqCreate(request_key=key(), scope='business', question=QUESTION,
                                            answer=ANSWER))['record']
    cid, faq_id, lead_id = opened['conversation']['id'], record['id'], opened['lead']['id']
    send_key = prefix + ':claimed-send'
    message = append(CRM(), cid, MessageAppend(request_key=send_key, expected_version=1, role='customer',
                                               body='Is there a buyer fee?'), actor='visitor')['record']
    draft_key = f"chat:draft:{message['id']}"
    stub = StubModel(grounded_text(faq_id))
    holder = connect(Settings())
    holder.execute('SELECT pg_advisory_lock(hashtextextended(%s, 0))', ('draft-claim:' + draft_key,))
    holder.commit()
    errors, done = [], Event()

    def attempt():
        try:
            asyncio.run(send(CRM(), cid, s.ChatSend(request_key=send_key, expected_version=1,
                                                    body='Is there a buyer fee?'), stub))
        except Exception as exc:
            errors.append(exc)
        finally:
            done.set()

    worker = Thread(target=attempt)
    try:
        worker.start()
        deadline = clock.monotonic() + 6
        while stub.calls == 0 and clock.monotonic() < deadline and not done.is_set():
            clock.sleep(0.1)
        assert stub.calls == 0 and not done.is_set()
        holder.execute('SELECT pg_advisory_unlock(hashtextextended(%s, 0))', ('draft-claim:' + draft_key,))
        holder.commit()
        assert done.wait(timeout=60)
        worker.join(timeout=30)
        assert errors == [] and stub.calls == 1
        with connect(Settings()) as conn:
            rows = conn.execute('SELECT role, count(*) FROM soloestate.conversation_messages WHERE conversation_id=%s '
                                'GROUP BY role', (cid,)).fetchall()
            assert sorted((row[0], row[1]) for row in rows) == [('assistant', 1), ('customer', 1)]
    finally:
        holder.close()
        cleanup_committed(prefix, cid, faq_id, lead_id)


def test_claimant_disconnect_releases_claim_for_recovery(chat):
    import time as clock
    b = chat
    opened = begin(b)
    cid = opened['conversation']['id']
    record = faq(b)
    key = b['key']()
    message = append(b['crm'], cid, MessageAppend(request_key=key, expected_version=1, role='customer',
                                                  body='Is there a buyer fee?'), actor='visitor')['record']
    draft_key = f"chat:draft:{message['id']}"
    with connect(Settings()) as doomed:
        assert doomed.execute('SELECT pg_advisory_lock(hashtextextended(%s, 0))',
                              ('draft-claim:' + draft_key,)).fetchone()[0] is not False
    with connect(Settings()) as probe:
        assert probe.execute('SELECT pg_try_advisory_lock(hashtextextended(%s, 0))',
                             ('draft-claim:' + draft_key,)).fetchone()[0] is True
        probe.execute('SELECT pg_advisory_unlock(hashtextextended(%s, 0))', ('draft-claim:' + draft_key,))
    stub = StubModel(grounded_text(record['id']))
    started = clock.monotonic()
    result = asyncio.run(send(b['crm'], cid, s.ChatSend(request_key=key, expected_version=1,
                                                        body='Is there a buyer fee?'), stub))
    assert clock.monotonic() - started < 60
    assert result['reply']['grounding'] == 'grounded' and stub.calls == 1


def test_claim_timeout_replays_or_rejects_without_inference(request, monkeypatch):
    import app.conversation as conversation_mod
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    monkeypatch.setattr(conversation_mod, 'CLAIM_WAIT_SECONDS', 2)
    settings = Settings(visitor_token_secret=secrets.token_urlsafe(32))
    with connect(Settings()) as conn:
        prefix = 'C-claimtimeout-' + str(uuid4())
        key = lambda: prefix + ':' + str(uuid4())
        crm = CRM()
        opened = start(crm, s.ChatStart(request_key=key(), name=prefix, email=prefix + '@example.invalid'), settings)
        record = faq_create(crm, FaqCreate(request_key=key(), scope='business', question=QUESTION,
                                            answer=ANSWER))['record']
    cid, faq_id, lead_id = opened['conversation']['id'], record['id'], opened['lead']['id']
    send_key = prefix + ':timeout-send'
    message = append(CRM(), cid, MessageAppend(request_key=send_key, expected_version=1, role='customer',
                                               body='Is there a buyer fee?'), actor='visitor')['record']
    draft_key = f"chat:draft:{message['id']}"
    stub = StubModel(grounded_text(faq_id))
    holder = connect(Settings())
    holder.execute('SELECT pg_advisory_lock(hashtextextended(%s, 0))', ('draft-claim:' + draft_key,))
    holder.commit()
    try:
        with pytest.raises(CRMError) as exc:
            asyncio.run(send(CRM(), cid, s.ChatSend(request_key=send_key, expected_version=1,
                                                    body='Is there a buyer fee?'), stub))
        assert exc.value.status == 503
        assert 'Retry the same saved request' in str(exc.value)
        assert 'Your message was saved' in str(exc.value)
        assert stub.calls == 0
        with connect(Settings()) as conn:
            assert conn.execute('SELECT count(*) FROM soloestate.crm_operations WHERE request_key=%s',
                                (draft_key,)).fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM soloestate.conversation_messages WHERE conversation_id=%s AND role='assistant'",
                                (cid,)).fetchone()[0] == 0
        with connect(Settings()) as probe:
            assert probe.execute('SELECT pg_try_advisory_lock(hashtextextended(%s, 0))',
                                 ('draft-claim:' + draft_key,)).fetchone()[0] is False
        holder.execute('SELECT pg_advisory_unlock(hashtextextended(%s, 0))', ('draft-claim:' + draft_key,))
        holder.commit()
        completed = asyncio.run(send(CRM(), cid, s.ChatSend(request_key=send_key, expected_version=1,
                                                             body='Is there a buyer fee?'), stub))
        assert completed['reply']['grounding'] == 'grounded' and stub.calls == 1
        with pytest.raises(CRMError, match='Request key already belongs to a different operation'):
            asyncio.run(send(CRM(), cid, s.ChatSend(request_key=send_key, expected_version=1,
                                                    body='Changed body'), stub))
        holder.execute('SELECT pg_advisory_lock(hashtextextended(%s, 0))', ('draft-claim:' + draft_key,))
        holder.commit()
        try:
            replayed = asyncio.run(send(CRM(), cid, s.ChatSend(request_key=send_key, expected_version=1,
                                                                body='Is there a buyer fee?'), stub))
            assert replayed == completed and stub.calls == 1
            with pytest.raises(CRMError, match='Request key already belongs to a different operation'):
                asyncio.run(owner_draft(CRM(), cid, DraftRequest(request_key=draft_key, expected_version=99,
                                                                 message_id=message['id']), stub))
            assert stub.calls == 1
        finally:
            holder.execute('SELECT pg_advisory_unlock(hashtextextended(%s, 0))', ('draft-claim:' + draft_key,))
            holder.commit()
        with connect(Settings()) as conn:
            rows = conn.execute('SELECT role, count(*) FROM soloestate.conversation_messages WHERE conversation_id=%s '
                                'GROUP BY role', (cid,)).fetchall()
            assert sorted((row[0], row[1]) for row in rows) == [('assistant', 1), ('customer', 1)]
    finally:
        holder.close()
        cleanup_committed(prefix, cid, faq_id, lead_id)


def test_claimant_failure_recovers_with_exactly_one_reply(chat):
    b = chat
    opened = begin(b)
    cid = opened['conversation']['id']
    record = faq(b)

    class FlakyModel:
        def __init__(self):
            self.calls = 0

        async def generate(self, prompt):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError('Simulated claimant crash')
            return ModelResult(response_id='resp-test', model='test-meta', text=grounded_text(record['id']))

    stub = FlakyModel()
    key = b['key']()
    with pytest.raises(RuntimeError, match='Simulated claimant crash'):
        asyncio.run(send(b['crm'], cid, s.ChatSend(request_key=key, expected_version=1,
                                                   body='Is there a buyer fee?'), stub))
    result = asyncio.run(send(b['crm'], cid, s.ChatSend(request_key=key, expected_version=1,
                                                        body='Is there a buyer fee?'), stub))
    assert result['reply']['grounding'] == 'grounded' and stub.calls == 2
    rows = b['conn'].execute('SELECT role, count(*) FROM soloestate.conversation_messages WHERE conversation_id=%s '
                             'GROUP BY role', (cid,)).fetchall()
    assert sorted((row[0], row[1]) for row in rows) == [('assistant', 1), ('customer', 1)]
    assert b['conn'].execute("SELECT count(*) FROM soloestate.crm_operations WHERE request_key LIKE 'chat:draft:%'",
                             ).fetchone()[0] == 1


def test_visitor_actor_constraints_and_access_boundaries(chat):
    import psycopg
    from psycopg.types.json import Jsonb
    b = chat
    opened = begin(b)
    lead_id = opened['lead']['id']
    for table in ('crm_operations', 'lead_activities'):
        definition = b['conn'].execute('SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname=%s',
                                       (f'{table}_actor_check',)).fetchone()[0]
        assert "'owner'" in definition and "'visitor'" in definition
    op_key = b['key']()
    b['conn'].execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) '
                      'VALUES (%s,%s,%s,%s,%s,%s)', (op_key, 'probe:actor', 'probe', 'owner', lead_id, Jsonb({})))
    b['conn'].execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) '
                      'VALUES (%s,%s,%s,%s,%s,%s)', (b['key'](), 'probe:actor', 'probe', 'visitor', lead_id, Jsonb({})))
    with pytest.raises(psycopg.errors.CheckViolation):
        with b['conn'].transaction():
            b['conn'].execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) '
                              'VALUES (%s,%s,%s,%s,%s,%s)', (b['key'](), 'probe:actor', 'probe', 'agent', lead_id, Jsonb({})))
    with pytest.raises(psycopg.errors.CheckViolation):
        with b['conn'].transaction():
            b['conn'].execute('INSERT INTO soloestate.lead_activities(lead_id,actor,reason,before_state,after_state,request_key) '
                              'VALUES (%s,%s,%s,%s,%s,%s)', (lead_id, 'agent', 'probe', Jsonb({}), Jsonb({}), op_key))
    actors = b['conn'].execute('SELECT DISTINCT actor FROM soloestate.crm_operations '
                               'UNION SELECT DISTINCT actor FROM soloestate.lead_activities').fetchall()
    assert {row[0] for row in actors} <= {'owner', 'visitor'}
    secured = b['conn'].execute("SELECT relname FROM pg_class WHERE relnamespace='soloestate'::regnamespace AND relname IN "
                                "('crm_operations','lead_activities') AND relrowsecurity").fetchall()
    assert {row[0] for row in secured} == {'crm_operations', 'lead_activities'}
    public = b['conn'].execute("SELECT count(*) FROM information_schema.role_table_grants WHERE grantee='PUBLIC' AND "
                               "table_schema='soloestate' AND table_name IN ('crm_operations','lead_activities')").fetchone()[0]
    assert public == 0


def test_visitor_operation_activity_message_fk_chains(chat):
    b = chat
    opened = begin(b)
    cid, lead_id = opened['conversation']['id'], opened['lead']['id']
    faq(b)
    asyncio.run(send(b['crm'], cid, s.ChatSend(request_key=b['key'](), expected_version=1,
                                               body='Is there a buyer fee?'), StubModel(EMPTY)))
    orphan_messages = b['conn'].execute(
        """SELECT count(*) FROM soloestate.conversation_messages m LEFT JOIN soloestate.crm_operations o
           ON o.request_key=m.request_key WHERE m.conversation_id=%s AND (o.request_key IS NULL OR o.actor<>'visitor')""",
        (cid,)).fetchone()[0]
    assert orphan_messages == 0
    orphan_activities = b['conn'].execute(
        """SELECT count(*) FROM soloestate.lead_activities a LEFT JOIN soloestate.crm_operations o
           ON o.request_key=a.request_key WHERE a.lead_id=%s AND (o.request_key IS NULL OR o.actor<>'visitor')""",
        (lead_id,)).fetchone()[0]
    assert orphan_activities == 0
    deferred = b['conn'].execute(
        """SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_class f ON f.oid=c.confrelid
           WHERE t.relname IN ('conversation_messages','lead_activities') AND f.relname='crm_operations'
           AND c.contype='f' AND c.condeferrable""").fetchone()[0]
    assert deferred == 2


def test_activity_failure_rolls_back_requirements_and_context(chat, monkeypatch):
    import app.conversation as conversation_mod
    import app.qualification as qualification_mod
    b = chat
    cid, prop = matching_setup(b)
    lead_id = b['conn'].execute('SELECT lead_id FROM soloestate.conversations WHERE id=%s', (cid,)).fetchone()[0]

    def fail(*args, **kwargs):
        raise RuntimeError('Simulated activity outage')

    monkeypatch.setattr(qualification_mod, 'activity', fail)
    monkeypatch.setattr(conversation_mod, 'log_activity', fail)
    with pytest.raises(RuntimeError, match='Simulated activity outage'):
        requirements(b['crm'], cid, s.ChatRequirements(request_key=b['key'](), expected_version=2,
                                                       budget_min='120.00'))
    with pytest.raises(RuntimeError, match='Simulated activity outage'):
        set_context(b['crm'], cid, s.ChatContext(request_key=b['key'](), expected_version=1, property_id=prop['id']))
    kept = b['conn'].execute('SELECT version, budget_min FROM soloestate.leads WHERE id=%s', (lead_id,)).fetchone()
    assert kept[0] == 2 and kept[1] == 100
    assert b['conn'].execute('SELECT version, property_id FROM soloestate.conversations WHERE id=%s', (cid,)).fetchone() == (1, None)
    assert b['conn'].execute('SELECT count(*) FROM soloestate.crm_operations WHERE request_key LIKE %s',
                             (b['prefix'] + ':%',)).fetchone()[0] == 3
