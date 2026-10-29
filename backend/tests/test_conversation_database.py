"""B3 real PostgreSQL conversation checks; fictional rollback records, except race FAQs committed on a separate connection and deleted by exact ID."""
import asyncio
import json
import threading
import time
from contextlib import contextmanager
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.database import connect
from app.crm import CRM, CRMError
from app.crm_schemas import (Archive, ConversationOpen, DraftRequest, FaqCreate, HandoffUpdate, LeadCreate,
    MessageAppend, PropertyCreate, ReviewUpdate)
from app.conversation import (FIXED_UNSUPPORTED_BODY, GROUNDED_PREAMBLE, append, detail, draft, handoff, listing,
    open_conversation, review)
from app.faq import archive as faq_archive, create as faq_create, new_version as faq_version
from app.crm_schemas import FaqArchive, FaqVersion
from app.journey import JourneyUpdate, update as journey_update
from app.services.meta_model import ModelResult, ModelServiceError
from app.main import create_app
from app.crm_api import service
from app.owner_api import require_owner

pytestmark = pytest.mark.integrity_database

ANSWER = 'No buyer fee applies in Bengaluru for this listing.'
QUESTION = 'Is there a buyer fee on this listing?'

@pytest.fixture
def b3c(request):
    if not request.config.getoption('--run-integrity-db-tests'): pytest.skip('Use --run-integrity-db-tests')
    committed = []
    with connect(Settings()) as conn:
        with conn.transaction(force_rollback=True):
            @contextmanager
            def factory():
                with conn.transaction(): yield conn
            crm = CRM(factory); prefix = 'B3-fictional-' + str(uuid4())
            lead = crm.save('leads', LeadCreate(request_key=str(uuid4()), name=prefix, email=prefix + '@example.invalid'))['record']
            prop = crm.save('properties', PropertyCreate(request_key=str(uuid4()), title=prefix, locality=prefix, price=150, bedrooms=2))['record']
            faq = faq_create(crm, FaqCreate(request_key=str(uuid4()), scope='business', question=QUESTION, answer=ANSWER))['record']
            yield dict(crm=crm, conn=conn, lead=lead, prop=prop, faq=faq, prefix=prefix, key=lambda: str(uuid4()), committed=committed)
    for faq_id, keys in committed:
        with connect(Settings()) as cleanup_conn:
            cleanup_conn.execute('DELETE FROM soloestate.faq_versions WHERE faq_id=%s', (faq_id,))
            cleanup_conn.execute('DELETE FROM soloestate.faqs WHERE id=%s', (faq_id,))
            for key in keys:
                cleanup_conn.execute('DELETE FROM soloestate.crm_operations WHERE request_key=%s', (key,))

class StubModel:
    def __init__(self, text=None, error=None):
        self.text = text; self.error = error; self.calls = 0
    async def generate(self, prompt):
        self.calls += 1
        assert len(prompt) <= 20000
        if self.error:
            raise self.error
        return ModelResult(response_id='resp-test', model='test-meta', text=self.text)

def client(b):
    app = create_app(); app.dependency_overrides[require_owner] = lambda: 'owner'; app.dependency_overrides[service] = lambda: b['crm']
    return TestClient(app)

def open_chat(b, **kw):
    base = dict(request_key=b['key'](), lead_id=b['lead']['id'])
    return open_conversation(b['crm'], ConversationOpen(**{**base, **kw}))['record']

def say(b, convo, role='customer', body='Is there a buyer fee?', **kw):
    return append(b['crm'], convo['id'], MessageAppend(request_key=b['key'](), expected_version=convo['version'],
        role=role, body=body, **kw))['record']

def grounded_text(b):
    return json.dumps({'answer': 'No buyer fee applies here.', 'citations': [
        {'faq_id': str(b['faq']['id']), 'version': 1, 'quote': 'No buyer fee applies in Bengaluru for this listing.'}], 'confidence': 'high'})

def run_draft(b, convo, message, model):
    return asyncio.run(draft(b['crm'], convo['id'], DraftRequest(request_key=b['key'](),
        expected_version=convo['version'], message_id=message['id']), model))

def test_open_listing_detail_and_lead_guards(b3c):
    b = b3c
    convo = open_chat(b)
    assert convo['status'] == 'open' and convo['handoff_state'] == 'none' and convo['version'] == 1
    found = detail(b['crm'], convo['id'])
    assert found['messages'] == [] and not found['truncated'] and found['drafting_blocked'] is None
    assert listing(b['crm'], lead_id=b['lead']['id'])['total'] == 1
    with pytest.raises(CRMError) as exc:
        open_conversation(b['crm'], ConversationOpen(request_key=b['key'](), lead_id=uuid4()))
    assert exc.value.status == 404
    archived = b['crm'].archive(b['lead']['id'], Archive(
        request_key=b['key'](), expected_version=1, archived=True))['record']
    assert archived['archived_at'] is not None
    with pytest.raises(CRMError, match='Restore'):
        open_chat(b)

def test_append_roles_supersede_and_replay(b3c):
    b = b3c; convo = open_chat(b)
    customer = say(b, convo)
    assert customer['role'] == 'customer' and customer['grounding'] == 'ungrounded'
    owner = say(b, convo, role='owner', body='Thanks, checking now.', supersedes_message_id=customer['id'])
    assert owner['supersedes_message_id'] == customer['id']
    other = open_chat(b)
    with pytest.raises(CRMError) as exc:
        say(b, other, role='owner', body='Wrong thread.', supersedes_message_id=customer['id'])
    assert exc.value.status == 404
    with pytest.raises(CRMError, match='Only owner'):
        say(b, convo, body='Linking as customer.', supersedes_message_id=customer['id'])
    req = MessageAppend(request_key=b['key'](), expected_version=convo['version'], role='customer', body='Repeat?')
    first = append(b['crm'], convo['id'], req)['record']
    assert append(b['crm'], convo['id'], req)['record'] == first
    with pytest.raises(CRMError, match='changed'):
        append(b['crm'], convo['id'], req.model_copy(update={'request_key': b['key'](), 'expected_version': 99}))

def test_resolved_conversation_blocks_new_messages(b3c):
    b = b3c; convo = open_chat(b)
    resolved = review(b['crm'], convo['id'], ReviewUpdate(request_key=b['key'](), expected_version=1,
        action='resolve', reason='Fictional review complete'))['record']
    assert resolved['status'] == 'resolved' and resolved['version'] == 2
    with pytest.raises(CRMError, match='Reopen'):
        say(b, resolved)
    reopened = review(b['crm'], resolved['id'], ReviewUpdate(request_key=b['key'](), expected_version=2,
        action='reopen', reason='Fictional follow-up'))['record']
    assert reopened['status'] == 'open'
    assert say(b, reopened)['role'] == 'customer'

def test_draft_grounded_pins_citations_and_preserves_lead_state(b3c):
    b = b3c; convo = open_chat(b); message = say(b, convo)
    stub = StubModel(grounded_text(b))
    out = run_draft(b, convo, message, stub)
    assert stub.calls == 1
    answer, updated = out['record'], out['conversation']
    assert answer['grounding'] == 'grounded' and answer['unsupported_reason'] is None
    assert answer['citations'] == [{'faq_id': str(b['faq']['id']), 'version': 1,
        'quote': 'No buyer fee applies in Bengaluru for this listing.'}]
    assert (answer['confidence'], answer['model_name'], answer['response_id']) == ('high', 'test-meta', 'resp-test')
    assert updated['status'] == 'open' and updated['version'] == 1
    lead = b['crm'].detail('leads', b['lead']['id'])['record']
    assert lead['contact_permission'] == 'unknown' and lead['journey_stage'] == 'new'

def test_simulation_ignores_but_never_changes_contact_permission(b3c):
    b = b3c
    opted = journey_update(b['crm'], b['lead']['id'], JourneyUpdate(request_key=b['key'](), expected_version=1,
        journey_stage='qualifying', contact_permission='opted_out', reason='Fictional B3 opt-out'))['record']
    assert opted['contact_permission'] == 'opted_out'
    convo = open_chat(b); message = say(b, convo)
    out = run_draft(b, convo, message, StubModel(grounded_text(b)))
    assert out['record']['grounding'] == 'grounded'
    lead = b['crm'].detail('leads', b['lead']['id'])['record']
    assert lead['contact_permission'] == 'opted_out' and lead['journey_stage'] == 'qualifying'

@pytest.mark.parametrize('kind,text', [
    ('missing_citations', json.dumps({'answer': 'No idea.', 'citations': [], 'confidence': 'low'})),
    ('invalid_citation', json.dumps({'answer': 'Fee?', 'citations': [{'faq_id': str(uuid4()), 'version': 1, 'quote': 'x'}], 'confidence': 'low'})),
    ('quote_mismatch', json.dumps({'answer': 'Fee?', 'citations': [{'faq_id': '__FAQ__', 'version': 1, 'quote': 'Huge buyer fees everywhere.'}], 'confidence': 'low'})),
    ('invalid_response', 'not json at all'),
    ('invalid_response', json.dumps({'answer': '   ', 'citations': [], 'confidence': 'low'})),
])
def test_draft_validation_failures_persist_unsupported(b3c, kind, text):
    b = b3c; convo = open_chat(b); message = say(b, convo)
    out = run_draft(b, convo, message, StubModel(text.replace('__FAQ__', str(b['faq']['id']))))
    answer, updated = out['record'], out['conversation']
    assert answer['grounding'] == 'unsupported' and answer['unsupported_reason'] == kind
    assert answer['body'] == FIXED_UNSUPPORTED_BODY and answer['citations'] == [] and answer['confidence'] is None
    assert 'Huge buyer fees' not in answer['body']
    assert updated['status'] == 'pending_owner' and updated['version'] == 2
    with b['crm'].connect() as conn:
        row = conn.execute("SELECT reason,after_state FROM soloestate.lead_activities WHERE lead_id=%s ORDER BY created_at DESC LIMIT 1",
                           (b['lead']['id'],)).fetchone()
    assert row[0] == 'Assistant answer needs owner review'
    assert row[1]['conversation']['status'] == 'pending_owner'

def test_draft_unretrieved_and_archived_versions_are_unauthorized(b3c):
    b = b3c
    faq_version(b['crm'], b['faq']['id'], FaqVersion(request_key=b['key'](), expected_version=1,
        question=QUESTION, answer=ANSWER + ' Updated.'))
    convo = open_chat(b); message = say(b, convo)
    stale = json.dumps({'answer': 'Old.', 'citations': [{'faq_id': str(b['faq']['id']), 'version': 1,
        'quote': 'No buyer fee applies in Bengaluru for this listing.'}], 'confidence': 'low'})
    out = run_draft(b, convo, message, StubModel(stale))
    assert out['record']['unsupported_reason'] == 'unauthorized_faq'

def test_draft_inference_failures_fail_safe_without_model_text(b3c):
    b = b3c
    for code, reason in [('timeout', 'inference_timeout'), ('connection', 'inference_failed')]:
        convo = open_chat(b); message = say(b, convo)
        stub = StubModel(error=ModelServiceError(code, 'Simulated outage'))
        out = run_draft(b, convo, message, stub)
        assert stub.calls == 1
        assert out['record']['grounding'] == 'unsupported' and out['record']['unsupported_reason'] == reason
        assert out['record']['body'] == FIXED_UNSUPPORTED_BODY
        assert out['record']['model_name'] is None and out['conversation']['status'] == 'pending_owner'

def test_draft_gates_block_without_wasted_inference(b3c):
    b = b3c; convo = open_chat(b); message = say(b, convo)
    stub = StubModel(grounded_text(b))
    held = handoff(b['crm'], convo['id'], HandoffUpdate(request_key=b['key'](), expected_version=1,
        action='request', reason='Fictional handoff'))['record']
    assert held['handoff_state'] == 'requested'
    with pytest.raises(CRMError, match='handoff'):
        run_draft(b, held, message, stub)
    assert stub.calls == 0
    with pytest.raises(CRMError, match='changed'):
        asyncio.run(draft(b['crm'], convo['id'], DraftRequest(request_key=b['key'](), expected_version=99, message_id=message['id']), stub))
    assert stub.calls == 0

def test_draft_replay_returns_identical_answer_without_redraft(b3c):
    b = b3c; convo = open_chat(b); message = say(b, convo)
    stub = StubModel(grounded_text(b))
    req = DraftRequest(request_key=b['key'](), expected_version=1, message_id=message['id'])
    first = asyncio.run(draft(b['crm'], convo['id'], req, stub))
    assert asyncio.run(draft(b['crm'], convo['id'], req, stub)) == first
    assert stub.calls == 1

def test_handoff_and_review_transitions_with_history(b3c):
    b = b3c; convo = open_chat(b)
    requested = handoff(b['crm'], convo['id'], HandoffUpdate(request_key=b['key'](), expected_version=1,
        action='request', reason='Fictional handoff'))['record']
    assert requested['handoff_state'] == 'requested' and requested['version'] == 2
    with pytest.raises(CRMError, match='Invalid handoff'):
        handoff(b['crm'], convo['id'], HandoffUpdate(request_key=b['key'](), expected_version=2, action='request', reason='Duplicate'))
    resolved = handoff(b['crm'], convo['id'], HandoffUpdate(request_key=b['key'](), expected_version=2,
        action='resolve', reason='Fictional resolution'))['record']
    assert resolved['handoff_state'] == 'resolved'
    with pytest.raises(CRMError, match='Invalid review'):
        review(b['crm'], convo['id'], ReviewUpdate(request_key=b['key'](), expected_version=3, action='acknowledge', reason='Nothing pending'))
    done = review(b['crm'], convo['id'], ReviewUpdate(request_key=b['key'](), expected_version=3,
        action='resolve', reason='Fictional done'))['record']
    assert done['status'] == 'resolved'
    with b['crm'].connect() as conn:
        reasons = [r[0] for r in conn.execute('SELECT reason FROM soloestate.lead_activities WHERE lead_id=%s ORDER BY created_at',
                                              (b['lead']['id'],)).fetchall()]
    assert reasons == ['Owner opened conversation', 'Fictional handoff', 'Fictional resolution', 'Fictional done']

def test_superseded_citations_derived_at_read(b3c):
    b = b3c; convo = open_chat(b); message = say(b, convo)
    out = run_draft(b, convo, message, StubModel(grounded_text(b)))
    assert out['record']['grounding'] == 'grounded'
    assert detail(b['crm'], convo['id'])['messages'][-1]['citations_superseded'] is False
    faq_version(b['crm'], b['faq']['id'], FaqVersion(request_key=b['key'](), expected_version=1, question=QUESTION, answer=ANSWER + ' Updated.'))
    assert detail(b['crm'], convo['id'])['messages'][-1]['citations_superseded'] is True

def test_audit_failure_rolls_back_handoff(b3c, monkeypatch):
    import app.conversation as convo_mod
    b = b3c; convo = open_chat(b)
    def fail(*args, **kwargs):
        import psycopg
        raise psycopg.OperationalError('Simulated activity outage')
    monkeypatch.setattr(convo_mod, 'log_activity', fail)
    with pytest.raises(Exception, match='Simulated activity outage'):
        handoff(b['crm'], convo['id'], HandoffUpdate(request_key=b['key'](), expected_version=1, action='request', reason='Fictional'))
    assert detail(b['crm'], convo['id'])['record']['handoff_state'] == 'none'
    with b['crm'].connect() as conn:
        assert conn.execute('SELECT count(*) FROM soloestate.lead_activities WHERE lead_id=%s', (b['lead']['id'],)).fetchone()[0] == 1

def test_archived_lead_blocks_conversation_mutations(b3c):
    b = b3c; convo = open_chat(b); message = say(b, convo)
    b['crm'].archive(b['lead']['id'], Archive(request_key=b['key'](), expected_version=1, archived=True))
    with pytest.raises(CRMError, match='Restore'):
        say(b, convo)
    with pytest.raises(CRMError, match='Restore'):
        run_draft(b, convo, message, StubModel(grounded_text(b)))
    with pytest.raises(CRMError, match='Restore'):
        handoff(b['crm'], convo['id'], HandoffUpdate(request_key=b['key'](), expected_version=1, action='request', reason='Fictional'))
    with pytest.raises(CRMError, match='Restore'):
        review(b['crm'], convo['id'], ReviewUpdate(request_key=b['key'](), expected_version=1, action='resolve', reason='Fictional'))

def test_owner_http_conversation_flow(b3c, monkeypatch):
    b = b3c
    stub = StubModel(grounded_text(b))
    class Service:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return stub
        async def __aexit__(self, *exc): return False
    monkeypatch.setattr('app.services.meta_model.MetaModelService', Service)
    with client(b) as api:
        convo = api.post('/owner/crm/conversations', json=dict(request_key=b['key'](), lead_id=b['lead']['id'])).json()['record']
        assert api.get('/owner/crm/conversations?lead_id=' + b['lead']['id']).json()['total'] == 1
        message = api.post(f"/owner/crm/conversations/{convo['id']}/messages", json=dict(
            request_key=b['key'](), expected_version=1, role='customer', body='Is there a buyer fee?')).json()['record']
        assert api.post(f"/owner/crm/conversations/{convo['id']}/messages", json=dict(
            request_key=b['key'](), expected_version=1, role='assistant', body='Hi.')).status_code == 422
        answer = api.post(f"/owner/crm/conversations/{convo['id']}/draft", json=dict(
            request_key=b['key'](), expected_version=1, message_id=message['id'])).json()['record']
        assert answer['grounding'] == 'grounded' and stub.calls == 1
        assert api.get(f'/owner/crm/conversations/{uuid4()}').status_code == 404

PETS_Q = 'Are pets allowed in this property?'
PETS_A = 'Pets are not allowed in this property.'

def make_pets_faq(b):
    return faq_create(b['crm'], FaqCreate(request_key=b['key'](), scope='business', question=PETS_Q, answer=PETS_A))['record']

def expected_body(*parts):
    return GROUNDED_PREAMBLE + '\n\n' + '\n\n'.join(f'[FAQ {faq_id} v{version}] {quote}' for faq_id, version, quote in parts)

def test_contradictory_model_prose_is_discarded_from_grounded_answers(b3c):
    b = b3c; pets = make_pets_faq(b)
    convo = open_chat(b); message = say(b, convo, body='Can I bring my dogs? Are pets allowed in this property?')
    text = json.dumps({'answer': 'Pets are allowed in this property. You may bring three dogs.', 'citations': [
        {'faq_id': str(pets['id']), 'version': 1, 'quote': PETS_A}], 'confidence': 'high'})
    answer = run_draft(b, convo, message, StubModel(text))['record']
    assert answer['grounding'] == 'grounded'
    assert answer['body'] == expected_body((pets['id'], 1, PETS_A))
    assert 'three dogs' not in answer['body'] and 'Pets are allowed' not in answer['body']
    assert PETS_A in answer['body']

def test_additional_unsupported_claim_is_stripped_from_grounded_answers(b3c):
    b = b3c; convo = open_chat(b); message = say(b, convo)
    text = json.dumps({'answer': ANSWER + ' Also, bring three dogs for a discount.', 'citations': [
        {'faq_id': str(b['faq']['id']), 'version': 1, 'quote': ANSWER}], 'confidence': 'high'})
    answer = run_draft(b, convo, message, StubModel(text))['record']
    assert answer['grounding'] == 'grounded'
    assert answer['body'] == expected_body((b['faq']['id'], 1, ANSWER))
    assert 'three dogs' not in answer['body'] and 'discount' not in answer['body']

def test_grounded_body_pins_template_for_multiple_citations(b3c):
    b = b3c; pets = make_pets_faq(b)
    convo = open_chat(b); message = say(b, convo, body='Buyer fee and pets?')
    text = json.dumps({'answer': 'Ignored prose.', 'citations': [
        {'faq_id': str(b['faq']['id']), 'version': 1, 'quote': ANSWER},
        {'faq_id': str(pets['id']), 'version': 1, 'quote': PETS_A}], 'confidence': 'low'})
    answer = run_draft(b, convo, message, StubModel(text))['record']
    assert answer['body'] == expected_body((b['faq']['id'], 1, ANSWER), (pets['id'], 1, PETS_A))

@pytest.mark.parametrize('kind,text', [
    ('missing_citations', json.dumps({'answer': 'Confident lie.', 'citations': [], 'confidence': 'high'})),
    ('invalid_citation', json.dumps({'answer': 'Confident lie.', 'citations': [{'faq_id': str(uuid4()), 'version': 1, 'quote': 'x'}], 'confidence': 'high'})),
])
def test_high_confidence_never_authorizes_grounding(b3c, kind, text):
    b = b3c; convo = open_chat(b); message = say(b, convo)
    answer = run_draft(b, convo, message, StubModel(text))['record']
    assert answer['grounding'] == 'unsupported' and answer['unsupported_reason'] == kind

def test_overlong_assembled_answer_persists_unsupported(b3c):
    b = b3c
    long_answer = 'Lodging policy details: ' + 'verified terms apply. ' * 88
    assert 1800 < len(long_answer) <= 2000
    faqs = [faq_create(b['crm'], FaqCreate(request_key=b['key'](), scope='business',
        question=f'Lodging policy {index}?', answer=long_answer))['record'] for index in range(3)]
    convo = open_chat(b); message = say(b, convo, body='Tell me the lodging policy details please.')
    text = json.dumps({'answer': 'Long.', 'citations': [
        {'faq_id': str(faq['id']), 'version': 1, 'quote': long_answer} for faq in faqs], 'confidence': 'high'})
    out = run_draft(b, convo, message, StubModel(text))
    assert out['record']['grounding'] == 'unsupported' and out['record']['unsupported_reason'] == 'invalid_response'
    assert out['conversation']['status'] == 'pending_owner'

def committed_faq(b, question, answer):
    key = str(uuid4())
    with connect(Settings()) as conn2:
        @contextmanager
        def factory2():
            with conn2.transaction(): yield conn2
        faq = faq_create(CRM(factory2), FaqCreate(request_key=key, scope='business', question=question, answer=answer))['record']
    entry = [faq['id'], [key]]
    b['committed'].append(entry)
    return faq, entry[1]

class RevisingStub(StubModel):
    def __init__(self, faq, keys, action, text):
        super().__init__(text)
        self.faq = faq; self.keys = keys; self.action = action
    async def generate(self, prompt):
        self.calls += 1
        assert len(prompt) <= 20000
        key = str(uuid4())
        with connect(Settings()) as conn2:
            @contextmanager
            def factory2():
                with conn2.transaction(): yield conn2
            crm2 = CRM(factory2)
            if self.action == 'revise':
                faq_version(crm2, self.faq['id'], FaqVersion(request_key=key, expected_version=1,
                    question=self.faq['question'], answer=self.faq['answer'] + ' Revised.'))
            else:
                faq_archive(crm2, self.faq['id'], FaqArchive(request_key=key, expected_version=1, archived=True))
        self.keys.append(key)
        return ModelResult(response_id='resp-test', model='test-meta', text=self.text)

def test_faq_revision_during_inference_persists_unsupported(b3c):
    b = b3c
    faq2, keys = committed_faq(b, f'What buyer fees apply {uuid4()}?', 'A buyer fee of one percent applies in Whitefield.')
    convo = open_chat(b); message = say(b, convo)
    text = json.dumps({'answer': 'Ignored.', 'citations': [
        {'faq_id': str(faq2['id']), 'version': 1, 'quote': faq2['answer']}], 'confidence': 'high'})
    stub = RevisingStub(faq2, keys, 'revise', text)
    out = run_draft(b, convo, message, stub)
    assert stub.calls == 1
    assert out['record']['grounding'] == 'unsupported' and out['record']['unsupported_reason'] == 'unauthorized_faq'
    assert out['record']['body'] == FIXED_UNSUPPORTED_BODY
    assert out['conversation']['status'] == 'pending_owner'

def test_faq_archival_during_inference_persists_unsupported(b3c):
    b = b3c
    faq2, keys = committed_faq(b, f'What buyer fees apply {uuid4()}?', 'A buyer fee of one percent applies in Whitefield.')
    convo = open_chat(b); message = say(b, convo)
    text = json.dumps({'answer': 'Ignored.', 'citations': [
        {'faq_id': str(faq2['id']), 'version': 1, 'quote': faq2['answer']}], 'confidence': 'high'})
    stub = RevisingStub(faq2, keys, 'archive', text)
    out = run_draft(b, convo, message, stub)
    assert out['record']['grounding'] == 'unsupported' and out['record']['unsupported_reason'] == 'unauthorized_faq'
    assert out['record']['body'] == FIXED_UNSUPPORTED_BODY
    assert out['conversation']['status'] == 'pending_owner'

def test_concurrent_faq_revision_serializes_with_draft_commit(b3c):
    b = b3c
    faq2, keys = committed_faq(b, f'What buyer fees apply {uuid4()}?', 'A buyer fee of one percent applies in Whitefield.')
    convo = open_chat(b); message = say(b, convo)
    text = json.dumps({'answer': 'Ignored.', 'citations': [
        {'faq_id': str(faq2['id']), 'version': 1, 'quote': faq2['answer']}], 'confidence': 'high'})
    held = threading.Event(); release = threading.Event(); outcome = {}; key = str(uuid4())
    keys.append(key)
    def publish():
        with connect(Settings()) as conn2:
            with conn2.transaction():
                @contextmanager
                def factory2():
                    with conn2.transaction(): yield conn2
                with factory2() as raw:
                    raw.execute('SELECT id FROM soloestate.faqs WHERE id=%s FOR UPDATE', (faq2['id'],))
                    held.set()
                    assert release.wait(30)
                faq_version(CRM(factory2), faq2['id'], FaqVersion(request_key=key, expected_version=1,
                    question=faq2['question'], answer=faq2['answer'] + ' Raced.'))
        outcome['published'] = True
    def run():
        try:
            outcome['draft'] = run_draft(b, convo, message, StubModel(text))
        except Exception as exc:
            outcome['error'] = exc
    publisher = threading.Thread(target=publish, daemon=True)
    drafter = threading.Thread(target=run, daemon=True)
    publisher.start()
    assert held.wait(30)
    drafter.start()
    time.sleep(1)
    release.set()
    drafter.join(60); publisher.join(60)
    assert not drafter.is_alive() and not publisher.is_alive()
    assert outcome.get('published') is True
    assert 'error' not in outcome, outcome.get('error')
    assert outcome['draft']['record']['unsupported_reason'] == 'unauthorized_faq'
