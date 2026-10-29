"""B3 real PostgreSQL FAQ checks; fictional records and all writes roll back."""
from contextlib import contextmanager
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.database import connect
from app.crm import CRM, CRMError
from app.crm_schemas import FaqArchive, FaqCreate, FaqVersion, PropertyCreate, PropertyUpdate
from app.faq import archive, create, detail, listing, new_version
from app.main import create_app
from app.crm_api import service
from app.owner_api import require_owner

pytestmark = pytest.mark.integrity_database

@pytest.fixture
def b3(request):
    if not request.config.getoption('--run-integrity-db-tests'): pytest.skip('Use --run-integrity-db-tests')
    with connect(Settings()) as conn:
        with conn.transaction(force_rollback=True):
            @contextmanager
            def factory():
                with conn.transaction(): yield conn
            crm = CRM(factory); prefix = 'B3-fictional-' + str(uuid4())
            prop = crm.save('properties', PropertyCreate(request_key=str(uuid4()), title=prefix, locality=prefix, price=150, bedrooms=2))['record']
            yield dict(crm=crm, conn=conn, prop=prop, prefix=prefix, key=lambda: str(uuid4()))

def make_faq(b, **kw):
    base = dict(request_key=b['key'](), scope='business', question='Do you charge a fee?', answer='No fee for buyers in Bengaluru.')
    return create(b['crm'], FaqCreate(**{**base, **kw}))['record']

def client(b):
    app = create_app(); app.dependency_overrides[require_owner] = lambda: 'owner'; app.dependency_overrides[service] = lambda: b['crm']
    return TestClient(app)

def test_create_detail_and_current_version(b3):
    b = b3
    business = make_faq(b)
    assert business['current_version'] == 1 and business['version'] == 1 and business['status'] == 'active'
    prop = make_faq(b, scope='property', property_id=b['prop']['id'], question='Parking?', answer='Two covered slots.')
    assert prop['property_id'] == b['prop']['id']
    found = detail(b['crm'], business['id'])
    assert found['record']['answer'] == 'No fee for buyers in Bengaluru.'
    assert [(v['version'], v['question']) for v in found['versions']] == [(1, 'Do you charge a fee?')]
    with pytest.raises(CRMError) as exc:
        detail(b['crm'], uuid4())
    assert exc.value.status == 404

def test_create_validates_property_scope(b3):
    b = b3
    with pytest.raises(CRMError) as exc:
        make_faq(b, scope='property', property_id=uuid4())
    assert exc.value.status == 404
    prop = b['crm'].save('properties', PropertyUpdate(request_key=b['key'](), expected_version=1, title=b['prefix'],
        locality=b['prefix'], price=150, bedrooms=2, active=False), b['prop']['id'])['record']
    assert not prop['active']
    with pytest.raises(CRMError) as exc:
        make_faq(b, scope='property', property_id=b['prop']['id'])
    assert exc.value.status == 422

def test_new_version_preserves_history_and_bumps_pointer(b3):
    b = b3; faq = make_faq(b)
    updated = new_version(b['crm'], faq['id'], FaqVersion(request_key=b['key'](), expected_version=1,
        question='Do you charge a buyer fee?', answer='No buyer fee applies in Bengaluru.'))['record']
    assert updated['current_version'] == 2 and updated['version'] == 2
    assert updated['answer'] == 'No buyer fee applies in Bengaluru.'
    with b['crm'].connect() as conn:
        first = conn.execute('SELECT question,answer FROM soloestate.faq_versions WHERE faq_id=%s AND version=1', (faq['id'],)).fetchone()
    assert first[0] == 'Do you charge a fee?' and first[1] == 'No fee for buyers in Bengaluru.'
    assert len(detail(b['crm'], faq['id'])['versions']) == 2

def test_stale_replay_and_conflicting_writes(b3):
    b = b3; faq = make_faq(b)
    req = FaqVersion(request_key=b['key'](), expected_version=faq['version'], question='Q2?', answer='A2.')
    updated = new_version(b['crm'], faq['id'], req)['record']
    assert new_version(b['crm'], faq['id'], req)['record'] == updated
    with pytest.raises(CRMError, match='changed'):
        new_version(b['crm'], faq['id'], req.model_copy(update={'request_key': b['key']()}))
    with pytest.raises(CRMError, match='different'):
        new_version(b['crm'], faq['id'], req.model_copy(update={'answer': 'Conflicting.'}))

def test_archive_excludes_and_blocks_versions_until_restored(b3):
    b = b3; faq = make_faq(b)
    assert listing(b['crm'], status='active')['total'] >= 1
    archived = archive(b['crm'], faq['id'], FaqArchive(request_key=b['key'](), expected_version=1, archived=True))['record']
    assert archived['status'] == 'archived'
    assert all(item['id'] != faq['id'] for item in listing(b['crm'], status='active')['items'])
    with pytest.raises(CRMError, match='Restore'):
        new_version(b['crm'], faq['id'], FaqVersion(request_key=b['key'](), expected_version=archived['version'], question='Q?', answer='A.'))
    restored = archive(b['crm'], faq['id'], FaqArchive(request_key=b['key'](), expected_version=archived['version'], archived=False))['record']
    assert restored['status'] == 'active'
    assert new_version(b['crm'], faq['id'], FaqVersion(request_key=b['key'](), expected_version=restored['version'], question='Q2?', answer='A2.'))['record']['current_version'] == 2

def test_listing_filters_search_and_pagination(b3):
    b = b3
    make_faq(b, question='Buyer fee in Bengaluru?', answer='No fee.')
    make_faq(b, scope='property', property_id=b['prop']['id'], question='Parking slots?', answer='Two covered.')
    assert listing(b['crm'], scope='business', q='Bengaluru')['total'] >= 1
    assert listing(b['crm'], scope='property', property_id=b['prop']['id'])['total'] == 1
    assert listing(b['crm'], q='Parking slots')['items'][0]['question'] == 'Parking slots?'
    page = listing(b['crm'], page=1, page_size=1)
    assert page['total'] >= 2 and len(page['items']) == 1
    with pytest.raises(CRMError) as exc:
        listing(b['crm'], scope='city')
    assert exc.value.status == 422

def test_owner_http_faq_flow(b3):
    b = b3
    with client(b) as api:
        created = api.post('/owner/crm/faqs', json=dict(request_key=b['key'](), scope='business', question='Fee?', answer='None.')).json()['record']
        assert api.get('/owner/crm/faqs/' + created['id']).json()['record']['current_version'] == 1
        assert api.get('/owner/crm/faqs?scope=business').json()['total'] >= 1
        assert api.get('/owner/crm/faqs?scope=city').status_code == 422
        assert api.get(f'/owner/crm/faqs/{uuid4()}').status_code == 404
        assert api.post('/owner/crm/faqs', json=dict(request_key=b['key'](), scope='property', question='Q?', answer='A.')).status_code == 422
