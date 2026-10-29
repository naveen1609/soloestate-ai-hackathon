"""B2 real PostgreSQL checks; fictional records and all writes roll back."""
from contextlib import contextmanager
from decimal import Decimal
from uuid import UUID,uuid4
import psycopg
import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.database import connect
from app.crm import CRM,CRMError
from app.crm_schemas import LeadCreate,LeadUpdate,Qualify,Archive
from app.journey import JourneyUpdate,update,activities
from app.qualification import confirm,property_matches
from app.tools.service import BusinessTools
from app.tools.schemas import ToolContext
from app.main import create_app
from app.crm_api import service
from app.owner_api import require_owner

pytestmark=pytest.mark.integrity_database

@pytest.fixture
def b2(request):
    if not request.config.getoption('--run-integrity-db-tests'): pytest.skip('Use --run-integrity-db-tests')
    with connect(Settings()) as conn:
        with conn.transaction(force_rollback=True):
            @contextmanager
            def factory():
                with conn.transaction(): yield conn
            crm=CRM(factory);prefix='B2-fictional-'+str(uuid4())
            lead=crm.save('leads',LeadCreate(request_key=str(uuid4()),name=prefix,email=prefix+'@example.invalid'))['record']
            yield dict(crm=crm,conn=conn,lead=lead,locality=prefix,key=lambda:str(uuid4()))

def save(b,lead=None,**answers):
    lead=lead or b['lead']
    return b['crm'].save('leads',LeadUpdate(request_key=b['key'](),expected_version=lead['version'],name=lead['name'],email=lead['email'],**answers),lead['id'])['record']

def complete(b,lead=None,**changes):
    return save(b,lead,**{**dict(budget_min=100,budget_max=200,preferred_localities=[b['locality']],bedrooms=2,purchase_timeframe='within_3_months'),**changes})

def client(b):
    app=create_app();app.dependency_overrides[require_owner]=lambda: 'owner';app.dependency_overrides[service]=lambda: b['crm']
    return TestClient(app)

def test_unknown_persistence_and_incomplete_matches(b2):
    b=b2;lead=b['lead'];tools=BusinessTools(b['crm'].connect)
    assert lead['bedrooms'] is None and lead['budget_min'] is None
    with client(b) as api:
        detail=api.get('/owner/crm/leads/'+lead['id']).json()
        assert detail['qualification']['matching_missing_fields']==['budget_min','budget_max','preferred_localities','bedrooms']
        result=api.get('/owner/crm/leads/'+lead['id']+'/matches').json()
        assert not result['matching_ready'] and result['matches']==[]
        assert api.get('/owner/crm/leads/'+lead['id']+'/matches?limit=21').status_code==422
        assert api.get(f'/owner/crm/leads/{uuid4()}/matches').status_code==404
    result=tools.execute('get_property_matches',{'lead_id':lead['id']},ToolContext(run_id=uuid4()))
    assert result.status=='succeeded' and not result.data['matching_ready'] and result.data['matches']==[]

def test_confirm_review_staleness_history_and_generic_transition_guard(b2):
    b=b2;crm=b['crm'];lead=update(crm,b['lead']['id'],JourneyUpdate(request_key=b['key'](),expected_version=1,journey_stage='qualifying',reason='Fictional owner qualification'))['record']
    with pytest.raises(CRMError,match='incomplete'): confirm(crm,lead['id'],Qualify(request_key=b['key'](),expected_version=lead['version'],reason='Not complete'))
    with pytest.raises(CRMError,match='incomplete'): update(crm,lead['id'],JourneyUpdate(request_key=b['key'](),expected_version=lead['version'],reason='Cannot bypass',journey_stage='qualified'))
    lead=complete(b,lead)
    req=Qualify(request_key=b['key'](),expected_version=lead['version'],reason='Fictional requirements reviewed')
    reviewed=confirm(crm,lead['id'],req)['record']
    assert confirm(crm,lead['id'],req)['record']==reviewed
    assert crm.detail('leads',lead['id'])['qualification']['qualification_current']
    assert reviewed['status']=='new' and reviewed['contact_permission']=='unknown'
    with pytest.raises(CRMError,match='changed'): confirm(crm,lead['id'],req.model_copy(update={'request_key':b['key']()}))
    with pytest.raises(CRMError,match='different'): confirm(crm,lead['id'],req.model_copy(update={'reason':'Conflicting retry'}))
    changed=save(b,reviewed,budget_max=250)
    assert changed['journey_stage']=='qualified'
    assert crm.detail('leads',lead['id'])['qualification']['needs_review']
    assert activities(crm,lead['id'])['items'][0]['after_state']['qualification']['budget_max']=='250.00'
    current=confirm(crm,lead['id'],Qualify(request_key=b['key'](),expected_version=changed['version'],reason='Reviewed changed ceiling'))['record']
    assert current['qualification_fingerprint']!=reviewed['qualification_fingerprint']
    assert crm.detail('leads',lead['id'])['qualification']['qualification_current']

def test_owner_http_confirmation_and_archived_safety(b2):
    b=b2;lead=complete(b)
    with client(b) as api:
        path='/owner/crm/leads/'+lead['id']
        body=dict(request_key=b['key'](),expected_version=lead['version'],reason='Fictional review')
        assert api.post(path+'/qualify',json=body).status_code==409  # Must explicitly enter qualifying.
        lead=update(b['crm'],lead['id'],JourneyUpdate(request_key=b['key'](),expected_version=lead['version'],journey_stage='qualifying',reason='Begin review'))['record']
        body.update(request_key=b['key'](),expected_version=lead['version'])
        assert api.post(path+'/qualify',json={**body,'actor':'model'}).status_code==422
        assert api.post(path+'/qualify',json=body).status_code==200
        assert api.get(path).json()['qualification']['qualification_current']
        assert api.post(f'/owner/crm/leads/{uuid4()}/qualify',json={**body,'request_key':b['key']()}).status_code==404
    lead=b['crm'].detail('leads',lead['id'])['record']
    archived=b['crm'].archive(lead['id'],Archive(request_key=b['key'](),expected_version=lead['version'],archived=True))['record']
    with pytest.raises(CRMError,match='Restore'): confirm(b['crm'],lead['id'],Qualify(request_key=b['key'](),expected_version=archived['version'],reason='Archived'))
    with pytest.raises(CRMError,match='Restore'): save(b,archived,bedrooms=3)
    assert b['crm'].detail('leads',lead['id'])['record']['bedrooms']==2

def test_matching_boundaries_explanations_and_tool_api_equivalence(b2):
    b=b2;lead=complete(b,purchase_timeframe=None)
    ids=[uuid4() for _ in range(7)]
    for identifier,price,beds,active,locality in zip(ids,[100,200,150,99,201,150,150],[2,2,3,2,2,2,1],[True]*5+[False,True],[b['locality']]*7):
        b['conn'].execute('INSERT INTO soloestate.properties(id,title,locality,price,bedrooms,active) VALUES (%s,%s,%s,%s,%s,%s)',(identifier,'Fictional B2 property',' '+locality.upper()+' ',price,beds,active))
    tools=BusinessTools(b['crm'].connect)
    tool=tools.execute('get_property_matches',dict(lead_id=lead['id'],limit=20),ToolContext(run_id=uuid4()))
    with client(b) as api:
        response=api.get('/owner/crm/leads/'+lead['id']+'/matches?limit=20')
        assert response.status_code==200
        result=response.json()
        assert {k:v for k,v in result.items() if k!='checked_at'}=={k:v for k,v in tool.data.items() if k!='checked_at'}
        short=api.get('/owner/crm/leads/'+lead['id']+'/matches?limit=1').json()
    assert [row['id'] for row in result['matches']]==list(map(str,ids[:3]))
    assert [row['score'] for row in result['matches']]==[100,100,90]
    assert all(check['passed'] for row in result['matches'] for check in row['checks'])
    assert short['truncated'] and len(short['matches'])==1
    assert result['matching_ready'] and not b['crm'].detail('leads',lead['id'])['qualification']['qualification_ready']
    lead=save(b,lead,budget_max=110)
    result=tools.execute('get_property_matches',dict(lead_id=lead['id']),ToolContext(run_id=uuid4()))
    assert len(result.data['matches'])==1
    lead=save(b,lead,budget_min=101,budget_max=110)
    result=tools.execute('get_property_matches',dict(lead_id=lead['id']),ToolContext(run_id=uuid4()))
    assert result.data['matching_ready'] and result.data['matches']==[]

def test_qualification_rollback_on_activity_failure(b2):
    b=b2;lead=complete(b)
    lead=update(b['crm'],lead['id'],JourneyUpdate(request_key=b['key'](),expected_version=lead['version'],journey_stage='qualifying',reason='Fictional review'))['record']
    req=Qualify(request_key=b['key'](),expected_version=lead['version'],reason='Fictional confirmation')
    b['conn'].execute("CREATE FUNCTION pg_temp.b2_fail() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fictional activity outage'; END $$")
    b['conn'].execute('CREATE TRIGGER b2_fail BEFORE INSERT ON soloestate.lead_activities FOR EACH ROW EXECUTE FUNCTION pg_temp.b2_fail()')
    with pytest.raises(psycopg.Error): confirm(b['crm'],lead['id'],req)
    current=b['crm'].detail('leads',lead['id'])['record']
    assert current['journey_stage']=='qualifying' and current['qualification_fingerprint'] is None
    assert b['conn'].execute('SELECT count(*) FROM soloestate.crm_operations WHERE request_key=%s',(req.request_key,)).fetchone()[0]==0

def test_optout_and_handoff_are_not_changed_by_internal_review(b2):
    b=b2;lead=complete(b)
    lead=update(b['crm'],lead['id'],JourneyUpdate(request_key=b['key'](),expected_version=lead['version'],journey_stage='qualifying',contact_permission='opted_out',handoff_state='requested',reason='Fictional internal handoff'))['record']
    reviewed=confirm(b['crm'],lead['id'],Qualify(request_key=b['key'](),expected_version=lead['version'],reason='Owner reviewed without contacting customer'))['record']
    assert reviewed['contact_permission']=='opted_out' and reviewed['handoff_state']=='requested'
    b['conn'].execute("INSERT INTO soloestate.runs(id,request_key,goal,status) VALUES (%s,%s,'Fictional B2 policy regression','pending')",(run:=uuid4(),b['key']()))
    result=BusinessTools(b['crm'].connect).execute('create_followup_draft',dict(lead_id=lead['id'],subject='Fictional',body='Never sent',idempotency_key='blocked'),ToolContext(run_id=run))
    assert result.status=='error' and result.code=='contact_blocked'


def test_partial_edits_validate_effective_bounds_and_preserve_unrelated_fields(b2):
    b=b2;lead=complete(b)
    lead=save(b,lead,status='contacted',notes='Fictional retained note')
    with pytest.raises(CRMError,match='Maximum budget'): save(b,lead,budget_max=50)
    assert b['crm'].detail('leads',lead['id'])['record']['version']==lead['version']
    changed=save(b,lead,bedrooms=None)
    assert changed['budget_max']=='200.00' and changed['status']=='contacted' and changed['notes']=='Fictional retained note'
    assert changed['bedrooms'] is None
    assert not b['crm'].detail('leads',lead['id'])['qualification']['matching_ready']


def test_omitted_and_explicit_null_have_different_operation_identities(b2):
    b=b2;lead=complete(b)
    req=LeadUpdate(request_key=b['key'](),expected_version=lead['version'],name=lead['name'],email=lead['email'])
    saved=b['crm'].save('leads',req,lead['id'])
    assert b['crm'].save('leads',req,lead['id'])==saved
    changed=LeadUpdate(**{**req.model_dump(exclude_unset=True),'budget_max':None})
    with pytest.raises(CRMError,match='different'): b['crm'].save('leads',changed,lead['id'])
    assert b['crm'].detail('leads',lead['id'])['record']['budget_max']=='200.00'


def test_pre_b2_operation_keys_keep_their_original_replay(b2):
    from app.owner import payload_hash
    from psycopg.types.json import Jsonb
    b=b2;lead=complete(b,purchase_timeframe=None)
    fields=('name','email','preferred_localities','budget_min','budget_max','bedrooms','status','notes')
    req=LeadUpdate(request_key=b['key'](),expected_version=lead['version'],**{key:lead[key] for key in fields})
    old_args=req.model_dump(mode='json');old_args.pop('purchase_timeframe')
    digest=payload_hash(dict(action='leads:'+lead['id'],arguments=old_args))
    old_record={key:value for key,value in lead.items() if key not in ('purchase_timeframe','qualification_fingerprint')}
    response=dict(record=old_record)
    b['conn'].execute("INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) VALUES (%s,%s,%s,'owner',%s,%s)",(req.request_key,'leads:'+lead['id'],digest,lead['id'],Jsonb(response)))
    assert b['crm'].save('leads',req,lead['id'])==response
    assert b['crm'].detail('leads',lead['id'])['record']['version']==lead['version']


def test_generic_journey_confirmation_uses_same_readiness_guard_and_evidence(b2):
    b=b2;lead=complete(b)
    lead=update(b['crm'],lead['id'],JourneyUpdate(request_key=b['key'](),expected_version=lead['version'],journey_stage='qualifying',reason='Fictional qualification'))['record']
    lead=update(b['crm'],lead['id'],JourneyUpdate(request_key=b['key'](),expected_version=lead['version'],journey_stage='qualified',reason='Fictional owner review'))['record']
    assert b['crm'].detail('leads',lead['id'])['qualification']['qualification_current']
    lead=save(b,lead,budget_max=250)
    assert lead['qualification_fingerprint'] is None
    lead=save(b,lead,budget_max=200)
    assert b['crm'].detail('leads',lead['id'])['qualification']['needs_review']  # Returning to old facts cannot revive a review.


def test_equal_rank_matches_have_stable_id_order(b2):
    b=b2;lead=complete(b)
    ids=sorted([uuid4(),uuid4(),uuid4()])
    for identifier in reversed(ids):
        b['conn'].execute('INSERT INTO soloestate.properties(id,title,locality,price,bedrooms) VALUES (%s,%s,%s,150,2)',(identifier,'Fictional tie',b['locality']))
    with client(b) as api:
        result=api.get('/owner/crm/leads/'+lead['id']+'/matches?limit=2').json()
    assert [row['id'] for row in result['matches']]==list(map(str,ids[:2])) and result['truncated']


def test_competing_review_and_requirement_edit_are_version_fenced(request):
    if not request.config.getoption('--run-integrity-db-tests'): pytest.skip('Use --run-integrity-db-tests')
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    crm=CRM();prefix='B2-race-'+str(uuid4())
    lead=crm.save('leads',LeadCreate(request_key=prefix+':create',name=prefix,email=prefix+'@example.invalid',budget_min=100,budget_max=200,preferred_localities=[prefix],bedrooms=2,purchase_timeframe='later'))['record']
    try:
        lead=update(crm,lead['id'],JourneyUpdate(request_key=prefix+':begin',expected_version=lead['version'],journey_stage='qualifying',reason='Fictional race'))['record']
        gate=Barrier(2)
        def attempt(action):
            gate.wait(timeout=10)
            try:
                if action=='review': return confirm(CRM(),lead['id'],Qualify(request_key=prefix+':review',expected_version=lead['version'],reason='Fictional review'))
                return CRM().save('leads',LeadUpdate(request_key=prefix+':edit',expected_version=lead['version'],name=prefix,email=lead['email'],budget_max=250),lead['id'])
            except CRMError: return None
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(attempt,['review','edit']))
        assert sum(result is not None for result in results)==1
        current=crm.detail('leads',lead['id'])
        assert current['record']['version']==lead['version']+1
        if current['record']['budget_max']=='250.00': assert not current['qualification']['qualification_current']
        else: assert current['qualification']['qualification_current']
    finally:
        with connect(Settings()) as conn:
            conn.execute('DELETE FROM soloestate.lead_activities WHERE lead_id=%s',(lead['id'],))
            conn.execute('DELETE FROM soloestate.crm_operations WHERE entity_id=%s',(lead['id'],))
            conn.execute('DELETE FROM soloestate.leads WHERE id=%s',(lead['id'],))
