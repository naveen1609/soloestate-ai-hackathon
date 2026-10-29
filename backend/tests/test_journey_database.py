"""Isolated fictional B1 records; every write rolls back."""
from contextlib import contextmanager
from uuid import uuid4
import pytest
from app.config import Settings
from app.database import connect
from app.crm import CRM, CRMError
from app.crm_schemas import LeadCreate, Archive
from app.journey import JourneyUpdate, update, activities

pytestmark=pytest.mark.integrity_database

@pytest.fixture
def data(request):
    if not request.config.getoption('--run-integrity-db-tests'): pytest.skip('Use --run-integrity-db-tests')
    with connect(Settings()) as conn:
        with conn.transaction(force_rollback=True):
            @contextmanager
            def factory():
                with conn.transaction(): yield conn
            crm=CRM(factory)
            lead=crm.save('leads',LeadCreate(request_key=str(uuid4()),name='Fictional B1',email=str(uuid4())+'@example.invalid',preferred_localities=['Demo'],budget_min=1,budget_max=2,bedrooms=1))['record']
            yield crm,conn,lead

def intent(lead,**values):
    return JourneyUpdate(request_key=str(uuid4()),expected_version=lead['version'],reason='Fictional owner evidence',**values)

def test_persistence_replay_stale_and_history(data):
    crm,conn,lead=data
    assert lead['contact_permission']=='unknown' and activities(crm,lead['id'])['total']==0
    request=intent(lead,journey_stage='qualifying',contact_permission='opted_out',handoff_state='requested')
    saved=update(crm,lead['id'],request)
    assert update(crm,lead['id'],request)==saved
    assert crm.detail('leads',lead['id'])['record']['status']=='new'
    assert saved['record']['version']==lead['version']+1
    assert activities(crm,lead['id'],page_size=1)['items'][0]['after_state']['contact_permission']=='opted_out'
    assert activities(crm,lead['id'],page=2,page_size=1)['items']==[]
    with pytest.raises(CRMError,match='changed'): update(crm,lead['id'],intent(lead,journey_stage='qualifying'))
    with pytest.raises(CRMError): update(crm,lead['id'],request.model_copy(update={'reason':'Different'}))
    assert activities(crm,lead['id'])['total']==1

def test_invalid_missing_archived(data):
    crm,conn,lead=data
    with pytest.raises(CRMError,match='Invalid'): update(crm,lead['id'],intent(lead,journey_stage='qualified'))
    with pytest.raises(CRMError,match='not found'): update(crm,uuid4(),intent(lead,journey_stage='qualifying'))
    assert activities(crm,lead['id'])['total']==0
    archived=crm.archive(lead['id'],Archive(request_key=str(uuid4()),expected_version=lead['version'],archived=True))['record']
    with pytest.raises(CRMError,match='Restore'): update(crm,lead['id'],intent(archived,journey_stage='qualifying'))

def test_activity_failure_rolls_back_state_and_operation(data):
    crm,conn,lead=data
    request=intent(lead,journey_stage='qualifying')
    conn.execute("CREATE FUNCTION pg_temp.b1_fail() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fictional failure'; END $$")
    conn.execute('CREATE TRIGGER b1_fail BEFORE INSERT ON soloestate.lead_activities FOR EACH ROW EXECUTE FUNCTION pg_temp.b1_fail()')
    import psycopg
    with pytest.raises(psycopg.Error): update(crm,lead['id'],request)
    assert crm.detail('leads',lead['id'])['record']['journey_stage']=='new'
    assert conn.execute('SELECT count(*) FROM soloestate.crm_operations WHERE request_key=%s',(request.request_key,)).fetchone()[0]==0

def test_authenticated_http_validation_persistence_and_missing(data):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.crm_api import service
    from app.owner_api import require_owner
    crm,conn,lead=data
    app=create_app()
    app.dependency_overrides[require_owner]=lambda: None
    app.dependency_overrides[service]=lambda: crm
    path=f"/owner/crm/leads/{lead['id']}"
    with TestClient(app) as client:
        body=intent(lead,journey_stage='qualifying').model_dump(mode='json')
        assert client.post(path+'/journey',json={**body,'actor':'model'}).status_code==422
        assert client.post(path+'/journey',json=body).status_code==200
        assert client.get(path).json()['record']['journey_stage']=='qualifying'
        assert client.get(path+'/activities?page_size=1').json()['total']==1
        assert client.get(path+'/activities?page_size=101').status_code==422
        assert client.post(f'/owner/crm/leads/{uuid4()}/journey',json={**body,'request_key':str(uuid4())}).status_code==404
