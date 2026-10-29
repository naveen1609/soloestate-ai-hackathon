import json
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.conversation import (HANDOFF, REVIEW, Citation, GROUNDED_PREAMBLE, assemble_grounded,
    blocked_reason, draft_prompt, gate, normalize, parse_candidate, validate_citations, InvalidDraft)
from app.crm import CRMError
from app.crm_schemas import ConversationOpen, DraftRequest, HandoffUpdate, MessageAppend, ReviewUpdate
from app.main import create_app


def test_conversation_schemas_accept_valid_requests():
    assert ConversationOpen(request_key='t', lead_id=uuid4()).property_id is None
    assert MessageAppend(request_key='t', expected_version=1, role='owner', body='Hello').supersedes_message_id is None
    assert DraftRequest(request_key='t', expected_version=1, message_id=uuid4())
    assert HandoffUpdate(request_key='t', expected_version=1, action='request', reason='Fictional handoff')
    assert ReviewUpdate(request_key='t', expected_version=1, action='resolve', reason='Fictional review')


@pytest.mark.parametrize('schema,body', [
    (ConversationOpen, dict(lead_id='not-a-uuid')),
    (MessageAppend, dict(role='assistant', body='Hi')),
    (MessageAppend, dict(role='customer', body='  ')),
    (MessageAppend, dict(role='owner', body='Hi', expected_version=True)),
    (DraftRequest, dict(message_id='nope')),
    (HandoffUpdate, dict(action='escalate', reason='Fictional')),
    (HandoffUpdate, dict(action='request', reason=' ')),
    (ReviewUpdate, dict(action='close', reason='Fictional')),
    (ReviewUpdate, dict(action='resolve', reason='Fictional', actor='model')),
])
def test_conversation_schemas_reject_invalid_requests(schema, body):
    base = dict(request_key='t', expected_version=1, lead_id=uuid4(), message_id=uuid4(),
                role='customer', body='Hello', action='request', reason='Fictional')
    valid = {k: v for k, v in base.items() if k in schema.model_fields}
    with pytest.raises(ValidationError):
        schema(**{**valid, **body})


def test_normalize_strips_lines_and_drops_blanks():
    assert normalize('  Two covered slots. \n\n  Near gate.\n') == 'Two covered slots.\nNear gate.'
    assert normalize('   ') == ''


def test_parse_candidate_accepts_strict_contract():
    payload = json.dumps({'answer': 'Two covered slots.', 'citations': [
        {'faq_id': str(uuid4()), 'version': 1, 'quote': 'Two covered slots.'}], 'confidence': 'high'})
    candidate = parse_candidate(payload)
    assert candidate.answer == 'Two covered slots.' and candidate.confidence == 'high'


@pytest.mark.parametrize('text', ['not json', json.dumps({'answer': 'Hi'}),
    json.dumps({'answer': '   ', 'citations': [], 'confidence': 'low'}),
    json.dumps({'answer': 'Hi', 'citations': [], 'confidence': 'certain'}),
    json.dumps({'answer': 'Hi', 'citations': [], 'confidence': 'low', 'extra': 1}),
    json.dumps({'answer': 'x' * 2001, 'citations': [], 'confidence': 'low'}),
    json.dumps({'answer': 'Hi', 'citations': [{'faq_id': str(uuid4()), 'version': 1, 'quote': 'q'}] * 6, 'confidence': 'low'}),
    json.dumps({'answer': 'Hi', 'citations': [{'faq_id': 'bad', 'version': 1, 'quote': 'q'}], 'confidence': 'low'})])
def test_parse_candidate_rejects_malformed_or_loose_drafts(text):
    with pytest.raises(InvalidDraft):
        parse_candidate(text)


@pytest.mark.parametrize('record,expected', [
    (dict(status='open', handoff_state='none'), None),
    (dict(status='open', handoff_state='resolved'), None),
    (dict(status='open', handoff_state='requested'), 'handoff_requested'),
    (dict(status='pending_owner', handoff_state='none'), 'pending_owner'),
    (dict(status='resolved', handoff_state='none'), 'resolved'),
    (dict(status='resolved', handoff_state='requested'), 'handoff_requested'),
])
def test_blocked_reason_matrix(record, expected):
    assert blocked_reason(record) == expected
    if expected is None:
        gate(record)
    else:
        with pytest.raises(CRMError):
            gate(record)


def test_handoff_and_review_graphs():
    assert HANDOFF == {'none': {'requested'}, 'requested': {'resolved'}, 'resolved': {'requested'}}
    assert REVIEW[('pending_owner', 'acknowledge')] == 'open'
    assert REVIEW[('resolved', 'reopen')] == 'open'
    assert ('open', 'acknowledge') not in REVIEW and ('resolved', 'resolve') not in REVIEW


def test_draft_prompt_is_bounded_and_cites_sources():
    faq_id = uuid4()
    retrieved = [dict(faq_id=faq_id, version=2, question='Q?' * 200, answer='A.' * 3000)]
    prompt = draft_prompt(retrieved, 'Hello?' * 500)
    assert len(prompt) <= 20000 and str(faq_id) in prompt and 'Hello?' in prompt


class StubCursor:
    def __init__(self, stored, locked=None):
        self.stored = stored
        self.locked = locked or []
    def execute(self, *args):
        return self
    def fetchone(self):
        return self.stored
    def fetchall(self):
        return self.locked


def candidate_with(*citations):
    return parse_candidate(json.dumps({'answer': 'Two slots.', 'citations': list(citations), 'confidence': 'medium'}))


def test_validate_citations_empty_is_missing():
    assert validate_citations(StubCursor(None), [], candidate_with()) == 'missing_citations'


def test_validate_citations_unknown_version_is_invalid():
    faq_id = uuid4()
    candidate = candidate_with(dict(faq_id=str(faq_id), version=9, quote='Two slots.'))
    assert validate_citations(StubCursor(None), [], candidate) == 'invalid_citation'


def test_validate_citations_unretrieved_or_archived_is_unauthorized():
    faq_id, other = uuid4(), uuid4()
    retrieved = [dict(faq_id=faq_id, version=1)]
    candidate = candidate_with(dict(faq_id=str(other), version=1, quote='Two slots.'))
    stored = dict(answer='Two slots.')
    locked = [dict(id=other, current_version=1, status='active')]
    assert validate_citations(StubCursor(stored, locked), retrieved, candidate) == 'unauthorized_faq'
    candidate = candidate_with(dict(faq_id=str(faq_id), version=1, quote='Two slots.'))
    locked = [dict(id=faq_id, current_version=1, status='archived')]
    assert validate_citations(StubCursor(stored, locked), retrieved, candidate) == 'unauthorized_faq'


def test_validate_citations_quote_must_match_exactly():
    faq_id = uuid4()
    retrieved = [dict(faq_id=faq_id, version=1)]
    stored = dict(answer='Two covered slots near the gate.')
    locked = [dict(id=faq_id, current_version=1, status='active')]
    good = candidate_with(dict(faq_id=str(faq_id), version=1, quote='  Two covered slots near the gate. '))
    assert validate_citations(StubCursor(stored, locked), retrieved, good) is None
    bad = candidate_with(dict(faq_id=str(faq_id), version=1, quote='Three open slots.'))
    assert validate_citations(StubCursor(stored, locked), retrieved, bad) == 'quote_mismatch'


def test_validate_citations_superseded_version_is_unauthorized():
    faq_id = uuid4()
    retrieved = [dict(faq_id=faq_id, version=1)]
    candidate = candidate_with(dict(faq_id=str(faq_id), version=1, quote='Two slots.'))
    stored = dict(answer='Two slots.')
    locked = [dict(id=faq_id, current_version=2, status='active')]
    assert validate_citations(StubCursor(stored, locked), retrieved, candidate) == 'unauthorized_faq'


@pytest.mark.parametrize('suffix,method', [('conversations', 'POST'), ('conversations', 'GET')])
def test_conversation_routes_require_owner(suffix, method):
    with TestClient(create_app()) as client:
        assert client.request(method, f'/owner/crm/{suffix}', json={} if method == 'POST' else None).status_code == 401


def test_assemble_grounded_pins_verified_quotes_with_fixed_framing():
    first = Citation(faq_id=uuid4(), version=1, quote='  Two covered slots. ')
    second = Citation(faq_id=uuid4(), version=2, quote='Near the gate.')
    assert assemble_grounded([first, second]) == (GROUNDED_PREAMBLE +
        f'\n\n[FAQ {first.faq_id} v1] Two covered slots.\n\n[FAQ {second.faq_id} v2] Near the gate.')


def test_assemble_grounded_rejects_overlong_answers():
    big = Citation(faq_id=uuid4(), version=1, quote='x' * 2000)
    with pytest.raises(ValueError):
        assemble_grounded([big] * 5)
