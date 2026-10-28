from contextlib import contextmanager
from uuid import uuid4
import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.main import create_app
from app import crm_schemas as s
from app.crm import CRM

LEAD=dict(request_key='test',name='Fictional',email='demo@example.invalid',preferred_localities=['Demo'],budget_min='100',budget_max='200',bedrooms=2)


@pytest.mark.parametrize('change',[
 {'email':'invalid'}, {'budget_min':'300'}, {'bedrooms':True}, {'preferred_localities':[' ']},
 {'budget_max':'NaN'}, {'name':''}, {'status':'owner'}, {'actor':'model'},
])
def test_lead_input_rejects_invalid_or_extra_fields(change):
    with pytest.raises(ValidationError): s.LeadCreate(**{**LEAD,**change})


def test_input_normalizes_email_and_deduplicates_localities():
    value=s.LeadCreate(**{**LEAD,'email':'DEMO@example.invalid','preferred_localities':[' Demo ','Demo']})
    assert value.email=='demo@example.invalid' and value.preferred_localities==['Demo']


@pytest.mark.parametrize('path,method',[
 ('summary','get'),('leads','get'),('properties','get'),('appointments','get'),
 ('leads','post'),('properties','post'),('visit-slots','post'),('appointment-requests','post'),
 (f'leads/{uuid4()}','patch'),(f'leads/{uuid4()}/archive','post'),
 (f'appointments/{uuid4()}/cancel','post'),(f'appointment-requests/{uuid4()}/commit','post'),
 (f'appointment-requests/{uuid4()}/reject','post'),
])
def test_every_crm_route_requires_owner(path,method):
    with TestClient(create_app()) as client:
        assert client.request(method,'/owner/crm/'+path,json={} if method!='get' else None).status_code==401


def test_commit_failure_cannot_acknowledge_crm_mutation():
    class Conn:
        def execute(self,*args): return self
        def fetchone(self): return None
    @contextmanager
    def failing():
        yield Conn()
        raise psycopg.OperationalError('Simulated commit loss')
    crm=CRM(failing)
    with pytest.raises(psycopg.OperationalError):
        crm.mutate('test',s.Write(request_key='key'),lambda _:dict(record={'id':str(uuid4())}))
