from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.main import create_app
from app.journey import JourneyUpdate, validate_transition, STAGES, HANDOFF
from app.crm import CRMError
from app.tools.service import require_contact_action, ToolFailure

BASE=dict(journey_stage='new',handoff_state='none',contact_permission='unknown',contact_preference='unspecified')

@pytest.mark.parametrize('field,graph',[('journey_stage',STAGES),('handoff_state',HANDOFF)])
def test_every_transition(field,graph):
    for before in graph:
        for target in graph:
            state={**BASE,field:before}
            if target==before or target in graph[before]: validate_transition(state,{field:target})
            else:
                with pytest.raises(CRMError): validate_transition(state,{field:target})

@pytest.mark.parametrize('change',[{}, {'reason':' '}, {'journey_stage':'booked'}, {'handoff_state':'bot'}, {'contact_permission':'approved'}, {'contact_permission':'opted_in','actor':'model'}, {'expected_version':True}])
def test_input_validation(change):
    data=dict(request_key='fictional',expected_version=1,reason='Fictional owner evidence')
    with pytest.raises(ValidationError): JourneyUpdate(**{**data,**change})

@pytest.mark.parametrize('field,value',[('contact_permission','opted_out'),('contact_preference','none'),('handoff_state','requested'),('handoff_state','in_progress')])
def test_customer_action_blocks(field,value):
    with pytest.raises(ToolFailure): require_contact_action({**BASE,field:value})

def test_optout_cannot_be_reset_to_unknown():
    require_contact_action(BASE)
    with pytest.raises(CRMError): validate_transition({**BASE,'contact_permission':'opted_out'},{'contact_permission':'unknown'})
    validate_transition({**BASE,'contact_permission':'opted_out'},{'contact_permission':'opted_in'})

@pytest.mark.parametrize('suffix,method',[('journey','post'),('activities','get')])
def test_owner_required(suffix,method):
    with TestClient(create_app()) as client:
        assert client.request(method,f'/owner/crm/leads/{uuid4()}/{suffix}',json={} if method=='post' else None).status_code==401
