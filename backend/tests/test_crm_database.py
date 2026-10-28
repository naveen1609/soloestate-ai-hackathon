"""Real CRM SQL and transactions. Rollback by default; exact cleanup for races."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import timedelta
from uuid import UUID,uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.database import connect
from app.crm import CRM,CRMError
from app import crm_schemas as s
from app.agent.store import RunStore,RunConflict
from app.tools.service import BusinessTools
from app.tools.schemas import ToolContext
from app.main import create_app
from app.crm_api import service
from app.owner_api import require_owner
from app.owner import OwnerService,DecisionRequest

pytestmark=pytest.mark.integrity_database


def records(crm,conn):
    prefix='crm-test-'+str(uuid4())
    def key(): return prefix+':'+str(uuid4())
    lead=crm.save('leads',s.LeadCreate(request_key=key(),name=prefix,email=prefix+'@example.invalid',preferred_localities=[prefix],budget_min=100,budget_max=200,bedrooms=2))['record']
    prop=crm.save('properties',s.PropertyCreate(request_key=key(),title=prefix,locality=prefix,price=150,bedrooms=2))['record']
    start=conn.execute("SELECT greatest(coalesce(max(ends_at),clock_timestamp()),clock_timestamp())+interval '20 years' FROM soloestate.visit_slots").fetchone()[0]
    slots=[crm.slot(s.SlotCreate(request_key=key(),starts_at=start+timedelta(hours=i),ends_at=start+timedelta(hours=i,minutes=30)))['record'] for i in (0,2)]
    return dict(crm=crm,conn=conn,key=key,prefix=prefix,lead=lead,prop=prop,slots=slots)


@pytest.fixture
def crm_db(request):
    if not request.config.getoption('--run-integrity-db-tests'): pytest.skip('Use --run-integrity-db-tests')
    with connect(Settings()) as conn:
        with conn.transaction(force_rollback=True):
            @contextmanager
            def factory():
                with conn.transaction(): yield conn
            yield records(CRM(factory),conn)


def prepare(b,slot=0,old=None):
    return b['crm'].prepare(s.AppointmentRequest(request_key=b['key'](),lead_id=b['lead']['id'],property_id=b['prop']['id'],slot_id=b['slots'][slot]['id'],
        replaces_booking_id=old['id'] if old else None,expected_version=old['version'] if old else None))['record']


def book(b,slot=0,old=None):
    review=prepare(b,slot,old)
    return b['crm'].commit(UUID(review['id']),s.Confirm(request_key=b['key'](),expected_payload_hash=review['payload_hash']))['record']


@pytest.mark.parametrize('entity',['leads','properties'])
def test_create_detail_update_and_replay(crm_db,entity):
    b=crm_db; original=b['lead' if entity=='leads' else 'prop']
    assert b['crm'].detail(entity,original['id'])['record']==original
    values={k:original[k] for k in (('name','email','preferred_localities','budget_min','budget_max','bedrooms','status','notes') if entity=='leads' else ('title','locality','price','bedrooms','description','active'))}
    schema=s.LeadUpdate if entity=='leads' else s.PropertyUpdate
    values['notes' if entity=='leads' else 'description']='Updated fictional record'
    request=schema(**values,expected_version=original['version'],request_key=b['key']())
    result=b['crm'].save(entity,request,original['id'])
    assert result['record']['version']==2
    assert b['crm'].save(entity,request,original['id'])==result
    with pytest.raises(CRMError,match='changed'):
        b['crm'].save(entity,request.model_copy(update={'request_key':b['key']()}),original['id'])


def test_search_filters_pagination_and_allowlisted_sort(crm_db):
    b=crm_db; crm=b['crm']
    listing=crm.listing('leads',q=b['prefix'],status='new',sort='name_asc',page_size=1)
    assert listing['total']==1 and listing['items'][0]['id']==b['lead']['id']
    assert crm.listing('leads',q=b['prefix'],page=2,page_size=1)['total']==1
    assert crm.listing('leads',q=b['prefix'],page=2,page_size=1)['items']==[]
    assert crm.listing('leads',q=b['prefix'],status='closed')['items']==[]
    assert crm.listing('leads',q='%'+b['prefix'])['items']==[]
    assert crm.listing('properties',q=b['prefix'],active=True,bedrooms=2,min_price=100,max_price=200)['total']==1
    assert crm.listing('properties',q=b['prefix'],active=False)['total']==0
    assert crm.listing('properties',q=b['prefix'],locality='other')['items']==[]
    with pytest.raises(CRMError): crm.listing('leads',sort='name; DROP TABLE leads')


def test_duplicate_email_and_request_identity_conflicts(crm_db):
    b=crm_db
    with pytest.raises(CRMError,match='email'):
        b['crm'].save('leads',s.LeadCreate(request_key=b['key'](),name='Other',email=b['lead']['email'].upper(),preferred_localities=['Demo'],budget_min=1,budget_max=2,bedrooms=1))
    request=s.Archive(request_key=b['key'](),expected_version=1,archived=True)
    b['crm'].archive(b['lead']['id'],request)
    with pytest.raises(CRMError,match='different operation'):
        b['crm'].archive(b['lead']['id'],request.model_copy(update={'archived':False}))


def test_archive_and_deactivate_preserve_relations_and_tool_safety(crm_db):
    b=crm_db; appointment=book(b)
    result=b['crm'].archive(b['lead']['id'],s.Archive(request_key=b['key'](),expected_version=1,archived=True))
    assert result['record']['archived_at']
    assert b['crm'].listing('leads',q=b['prefix'])['total']==0
    assert b['crm'].listing('leads',q=b['prefix'],archived=True)['total']==1
    tools=BusinessTools(b['crm'].connect)
    assert tools.execute('get_property_matches',{'lead_id':b['lead']['id']},ToolContext(run_id=uuid4())).code=='not_found'
    assert b['crm'].detail('appointments',appointment['id'])['record']['lead_id']==b['lead']['id']
    with pytest.raises(CRMError): prepare(b,1)
    b['crm'].archive(b['lead']['id'],s.Archive(request_key=b['key'](),expected_version=2,archived=False))
    p=b['prop']
    b['crm'].save('properties',s.PropertyUpdate(request_key=b['key'](),expected_version=1,title=p['title'],locality=p['locality'],price=p['price'],bedrooms=2,active=False),p['id'])
    with pytest.raises(CRMError): prepare(b,1)


@pytest.mark.parametrize('entity',['leads','properties','appointments'])
def test_missing_detail_is_not_success(crm_db,entity):
    with pytest.raises(CRMError) as exc: crm_db['crm'].detail(entity,uuid4())
    assert exc.value.status==404


def test_controlled_slots_overlap_past_and_invalid_duration(crm_db):
    b=crm_db; slot=b['slots'][0]
    with pytest.raises(CRMError):
        b['crm'].slot(s.SlotCreate(request_key=b['key'](),starts_at=slot['starts_at'],ends_at=slot['ends_at']))
    with pytest.raises(CRMError):
        b['crm'].slot(s.SlotCreate(request_key=b['key'](),starts_at='2020-01-01T00:00:00Z',ends_at='2020-01-01T00:30:00Z'))


def test_booking_explicit_consent_and_no_fabricated_ai_history(crm_db):
    b=crm_db; review=prepare(b)
    assert review['approval_status']=='pending'
    assert b['conn'].execute('SELECT count(*) FROM soloestate.bookings WHERE lead_id=%s',(b['lead']['id'],)).fetchone()[0]==0
    with pytest.raises(CRMError): b['crm'].commit(UUID(review['id']),s.Confirm(request_key=b['key'](),expected_payload_hash='0'*64))
    request=s.Confirm(request_key=b['key'](),expected_payload_hash=review['payload_hash'])
    result=b['crm'].commit(UUID(review['id']),request)
    assert result['record']['status']=='confirmed' and result['record']['origin']=='owner'
    assert b['crm'].commit(UUID(review['id']),request)==result
    assert b['conn'].execute('SELECT count(*) FROM soloestate.run_steps WHERE run_id=%s',(review['id'],)).fetchone()[0]==0
    with pytest.raises(RunConflict,match='Owner CRM'): RunStore(b['crm'].connect).claim(UUID(review['id']))
    assert b['crm'].listing('appointments',q=b['prefix'],status='confirmed',period='upcoming')['total']==1
    assert b['crm'].summary()['metrics']['upcoming_visits']>=1


def test_rejection_blocks_booking(crm_db):
    b=crm_db; review=prepare(b)
    request=s.Confirm(request_key=b['key'](),expected_payload_hash=review['payload_hash'])
    assert b['crm'].reject(UUID(review['id']),request)['record']['status']=='cancelled'
    with pytest.raises(CRMError): b['crm'].commit(UUID(review['id']),request.model_copy(update={'request_key':b['key']()}))


def test_cancellation_is_versioned_idempotent_and_frees_slot(crm_db):
    b=crm_db; appointment=book(b)
    request=s.Cancel(request_key=b['key'](),expected_version=appointment['version'],reason='Owner cancelled fictional visit')
    cancelled=b['crm'].cancel(appointment['id'],request)
    assert cancelled['record']['status']=='cancelled' and cancelled['record']['version']==2
    assert b['crm'].cancel(appointment['id'],request)==cancelled
    with pytest.raises(CRMError): b['crm'].cancel(appointment['id'],request.model_copy(update={'request_key':b['key']()}))
    assert prepare(b)['approval_status']=='pending'


def test_reschedule_creates_approved_linked_replacement(crm_db):
    b=crm_db; old=book(b); new=book(b,1,old)
    assert new['id']!=old['id'] and new['replaces_booking_id']==old['id']
    assert b['crm'].detail('appointments',old['id'])['record']['status']=='cancelled'
    assert new['status']=='confirmed'
    assert b['conn'].execute("SELECT status FROM soloestate.approvals WHERE id=%s",(new['approval_id'],)).fetchone()[0]=='consumed'


def test_failed_reschedule_rolls_back_cancellation_and_consent(crm_db):
    b=crm_db; old=book(b); review=prepare(b,1,old)
    b['conn'].execute('UPDATE soloestate.visit_slots SET enabled=false WHERE id=%s',(b['slots'][1]['id'],))
    with pytest.raises((CRMError,RunConflict)):
        b['crm'].commit(UUID(review['id']),s.Confirm(request_key=b['key'](),expected_payload_hash=review['payload_hash']))
    assert b['crm'].detail('appointments',old['id'])['record']['status']=='confirmed'
    assert b['crm'].request_detail(review['id'])['record']['approval_status']=='pending'


def test_database_error_after_replacement_rolls_back_everything(crm_db,monkeypatch):
    b=crm_db; old=book(b); review=prepare(b,1,old)
    original=RunStore.finish
    def fail(self,*args,**kwargs):
        if args[1]=='completed': raise psycopg.OperationalError('Simulated final audit outage')
        return original(self,*args,**kwargs)
    monkeypatch.setattr(RunStore,'finish',fail)
    with pytest.raises(psycopg.OperationalError):
        b['crm'].commit(UUID(review['id']),s.Confirm(request_key=b['key'](),expected_payload_hash=review['payload_hash']))
    assert b['crm'].detail('appointments',old['id'])['record']['status']=='confirmed'
    assert b['conn'].execute("SELECT count(*) FROM soloestate.bookings WHERE lead_id=%s",(b['lead']['id'],)).fetchone()[0]==1


def test_archive_prevents_approving_previously_drafted_followup(crm_db):
    b=crm_db;run=uuid4()
    b['conn'].execute("INSERT INTO soloestate.runs(id,request_key,goal,status) VALUES (%s,%s,'Fictional archive consent regression','pending')",(run,b['key']()))
    tools=BusinessTools(b['crm'].connect);ctx=ToolContext(run_id=run)
    draft=tools.execute('create_followup_draft',dict(lead_id=b['lead']['id'],idempotency_key='draft',subject='Fictional',body='Never deliver'),ctx)
    approval=tools.execute('request_approval',dict(kind='followup',draft_id=draft.data['draft']['id'],idempotency_key='review'),ctx).data['approval']['id']
    b['crm'].archive(b['lead']['id'],s.Archive(request_key=b['key'](),expected_version=1,archived=True))
    owner=OwnerService(b['crm'].connect);review=owner.review(approval)
    with pytest.raises(RunConflict,match='archived'):
        owner.decide(approval,DecisionRequest(decision='approved',expected_payload_hash=review['payload_hash'],idempotency_key=b['key']()))
    assert owner.review(approval)['status']=='pending'


@pytest.mark.parametrize('entity',['leads','properties'])
def test_missing_update_returns_not_found_without_writes(crm_db,entity):
    b=crm_db;row=b['lead' if entity=='leads' else 'prop'];schema=s.LeadUpdate if entity=='leads' else s.PropertyUpdate
    fields=('name','email','preferred_localities','budget_min','budget_max','bedrooms','status','notes') if entity=='leads' else ('title','locality','price','bedrooms','description','active')
    request=schema(**{k:row[k] for k in fields},request_key=b['key'](),expected_version=1)
    with pytest.raises(CRMError) as exc: b['crm'].save(entity,request,uuid4())
    assert exc.value.status==404


def test_crm_api_validation_and_real_persistence(crm_db):
    b=crm_db; app=create_app();app.dependency_overrides[require_owner]=lambda:'owner';app.dependency_overrides[service]=lambda:b['crm']
    with TestClient(app) as client:
        assert client.get('/owner/crm/leads?page=0').status_code==422
        assert client.get('/owner/crm/properties?sort=arbitrary').status_code==422
        assert client.patch('/owner/crm/leads/'+b['lead']['id'],json={}).status_code==422
        assert client.get('/owner/crm/leads/'+str(uuid4())).status_code==404
        assert client.get('/owner/crm/leads',params={'q':b['prefix']}).json()['total']==1


def test_independent_owner_requests_cannot_double_book(request):
    if not request.config.getoption('--run-integrity-db-tests'): pytest.skip('Use --run-integrity-db-tests')
    crm=CRM()
    with connect(Settings()) as conn: b=records(crm,conn)
    try:
        reviews=[prepare(b),prepare(b)]; gate=Barrier(2)
        def attempt(review):
            gate.wait(timeout=10)
            try: return CRM().commit(UUID(review['id']),s.Confirm(request_key=b['key'](),expected_payload_hash=review['payload_hash']))
            except (CRMError,RunConflict): return None
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(attempt,reviews))
        assert sum(result is not None for result in results)==1
        with connect(Settings()) as conn:
            assert conn.execute("SELECT count(*) FROM soloestate.bookings WHERE lead_id=%s AND status='confirmed'",(b['lead']['id'],)).fetchone()[0]==1
    finally:
        with connect(Settings()) as conn:
            runs=[row[0] for row in conn.execute('SELECT run_id FROM soloestate.approvals WHERE lead_id=%s',(b['lead']['id'],)).fetchall()]
            conn.execute('DELETE FROM soloestate.bookings WHERE lead_id=%s',(b['lead']['id'],))
            conn.execute('DELETE FROM soloestate.approval_decisions WHERE approval_id IN (SELECT id FROM soloestate.approvals WHERE lead_id=%s)',(b['lead']['id'],))
            conn.execute('DELETE FROM soloestate.approvals WHERE lead_id=%s',(b['lead']['id'],))
            conn.execute('DELETE FROM soloestate.runs WHERE id=ANY(%s::uuid[])',(runs,))
            conn.execute('DELETE FROM soloestate.crm_operations WHERE request_key LIKE %s',(b['prefix']+':%',))
            conn.execute('DELETE FROM soloestate.visit_slots WHERE id=ANY(%s::uuid[])',([item['id'] for item in b['slots']],))
            conn.execute('DELETE FROM soloestate.properties WHERE id=%s',(b['prop']['id'],))
            conn.execute('DELETE FROM soloestate.leads WHERE id=%s',(b['lead']['id'],))
