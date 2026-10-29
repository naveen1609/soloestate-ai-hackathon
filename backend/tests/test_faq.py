from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.crm_schemas import FaqArchive, FaqCreate, FaqVersion
from app.faq import tokens, retrieve
from app.main import create_app


def test_faq_create_accepts_business_and_property_scopes():
    business = FaqCreate(request_key='test', scope='business', question='Do you charge a fee?', answer='No fee for buyers.')
    assert business.property_id is None
    prop = FaqCreate(request_key='test', scope='property', property_id=uuid4(), question='Parking?', answer='Two covered slots.')
    assert prop.property_id is not None


@pytest.mark.parametrize('change', [{'scope': 'property'}, {'scope': 'business', 'property_id': uuid4()},
    {'question': ' '}, {'answer': '  '}, {'question': 'q' * 501}, {'answer': 'a' * 5001},
    {'scope': 'city'}, {'actor': 'model'}])
def test_faq_create_rejects_scope_mismatch_blank_overlong_and_extra(change):
    base = dict(request_key='test', scope='business', question='Q?', answer='A.')
    with pytest.raises(ValidationError):
        FaqCreate(**{**base, **change})


@pytest.mark.parametrize('schema,body', [(FaqVersion, dict(question='Q?', answer='A.')),
    (FaqArchive, dict(archived=True))])
def test_faq_mutation_schemas_require_strict_versions(schema, body):
    with pytest.raises(ValidationError):
        schema(**{**dict(request_key='test', expected_version=True), **body})
    assert schema(request_key='test', expected_version=2, **body).expected_version == 2


def test_faq_archive_rejects_non_boolean():
    with pytest.raises(ValidationError):
        FaqArchive(request_key='test', expected_version=1, archived='yes')


def test_tokens_normalize_and_filter_short_words():
    assert tokens('  Parking SLOTS, parking! A ab abc 1234 ') == {'parking', 'slots', 'abc', '1234'}


class StubCursor:
    def __init__(self, rows):
        self.rows = rows
    def execute(self, *args):
        return self
    def fetchall(self):
        return self.rows


def test_retrieve_ranks_overlap_then_recency_then_id():
    from datetime import datetime, timezone
    rows = [
        dict(faq_id='b', version=1, updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc), question='pool gym', answer='none'),
        dict(faq_id='a', version=1, updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc), question='pool gym', answer='none'),
        dict(faq_id='c', version=2, updated_at=datetime(2026, 1, 3, tzinfo=timezone.utc), question='parking slots available', answer='two covered'),
    ]
    out = retrieve(StubCursor(rows), {'property_id': None}, 'Where are the parking slots?')
    assert [row['faq_id'] for row in out] == ['c', 'b', 'a']
    assert out[0]['version'] == 2


def test_retrieve_respects_limit():
    rows = [dict(faq_id=str(i), version=1, updated_at='t', question='parking', answer='yes') for i in range(8)]
    assert len(retrieve(StubCursor(rows), {'property_id': None}, 'parking', limit=5)) == 5


@pytest.mark.parametrize('suffix,method', [('faqs', 'POST'), ('faqs', 'GET')])
def test_faq_routes_require_owner(suffix, method):
    with TestClient(create_app()) as client:
        assert client.request(method, f'/owner/crm/{suffix}', json={} if method == 'POST' else None).status_code == 401
