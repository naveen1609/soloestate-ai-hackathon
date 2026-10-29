from decimal import Decimal
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.crm_schemas import LeadCreate, Qualify
from app.qualification import facts, fingerprint, readiness, reviewed_fingerprint
from app.crm import CRMError
from app.main import create_app

BASE = dict(budget_min=Decimal('100'),budget_max=Decimal('200'),preferred_localities=['Demo'],bedrooms=2,
            purchase_timeframe='within_3_months',journey_stage='qualifying',qualification_fingerprint=None)

def test_partial_lead_never_invents_answers():
    lead=LeadCreate(request_key='fictional',name='Fictional buyer',email='buyer@example.invalid')
    assert all(getattr(lead,key) is None for key in ('budget_min','budget_max','preferred_localities','bedrooms','purchase_timeframe'))
    result=readiness({**BASE,**lead.model_dump()})
    assert not result['matching_ready'] and len(result['missing_fields'])==5

@pytest.mark.parametrize('change',[{'budget_min':'NaN'},{'budget_max':'Infinity'},{'budget_min':300,'budget_max':200},
    {'budget_max':-1},{'bedrooms':True},{'bedrooms':0},{'bedrooms':31},{'preferred_localities':[]},
    {'preferred_localities':[' ']},{'purchase_timeframe':'tomorrow'},{'qualification_fingerprint':'owner-said-so'}])
def test_invalid_answers_and_review_metadata_rejected(change):
    with pytest.raises(ValidationError):
        LeadCreate(request_key='test',name='Fictional',email='test@example.invalid',**change)

def test_matching_readiness_is_distinct_from_qualification_and_consent():
    state=readiness({**BASE,'purchase_timeframe':None,'contact_permission':'opted_out'})
    assert state['matching_ready'] and not state['qualification_ready']
    assert state['missing_fields']==['purchase_timeframe']
    assert not readiness({**BASE,'budget_max':0})['matching_ready']
    with pytest.raises(CRMError): reviewed_fingerprint({**BASE,'bedrooms':None})

def test_review_fingerprint_normalizes_facts_and_ignores_unrelated_controls():
    reviewed={**BASE,'journey_stage':'qualified','qualification_fingerprint':fingerprint(BASE)}
    assert readiness(reviewed)['qualification_current']
    assert readiness({**reviewed,'contact_permission':'opted_out','handoff_state':'requested','notes':'Edited'})['qualification_current']
    assert fingerprint(BASE)==fingerprint({**BASE,'budget_min':Decimal('100.00'),'preferred_localities':[' demo ','DEMO']})
    assert readiness({**reviewed,'budget_max':250})['needs_review']
    assert readiness({**reviewed,'purchase_timeframe':'later'})['needs_review']
    assert facts({**BASE,'budget_min':None})['budget_min'] is None

@pytest.mark.parametrize('body',[dict(reason=' '),dict(reason='Fictional',expected_version=True),dict(reason='Fictional',actor='model')])
def test_confirmation_validation(body):
    with pytest.raises(ValidationError): Qualify(**{**dict(request_key='test',expected_version=1),**body})

@pytest.mark.parametrize('suffix,method',[('qualify','POST'),('matches','GET')])
def test_qualification_routes_require_owner(suffix,method):
    with TestClient(create_app()) as client:
        assert client.request(method,f'/owner/crm/leads/{uuid4()}/{suffix}',json={} if method=='POST' else None).status_code==401
