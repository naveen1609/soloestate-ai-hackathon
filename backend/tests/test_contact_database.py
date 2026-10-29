"""Exercise the actual registered tools and owner approval service, with rollback fixtures."""
from uuid import UUID, uuid4
import pytest
from test_tools_database import business, booking_approval
from app.owner import OwnerService, DecisionRequest
from app.agent.store import RunConflict

pytestmark=pytest.mark.tools_database

@pytest.mark.parametrize('field,value',[('contact_permission','opted_out'),('contact_preference','none'),('handoff_state','requested'),('handoff_state','in_progress')])
def test_blocked_contact_cannot_draft_or_request_booking(business,field,value):
    b=business
    # Column is a test-owned allowlist, never user or model input.
    from psycopg import sql
    b['conn'].execute(sql.SQL('UPDATE soloestate.leads SET {}=%s WHERE id=%s').format(sql.Identifier(field)),(value,b['lead']))
    draft=b['call']('create_followup_draft',dict(lead_id=str(b['lead']),subject='Fictional',body='Fictional only',idempotency_key='blocked'))
    assert draft.status=='error'
    result=b['call']('request_approval',dict(kind='booking',lead_id=str(b['lead']),property_id=str(b['property']),slot_id=str(b['slot']),idempotency_key='blocked-booking'))
    assert result.status=='error'
    assert b['conn'].execute('SELECT count(*) FROM soloestate.bookings WHERE run_id=%s',(b['run'],)).fetchone()[0]==0

def test_optout_after_approval_blocks_booking(business):
    b=business
    approval=booking_approval(b)
    b['approve'](approval)
    b['conn'].execute("UPDATE soloestate.leads SET contact_permission='opted_out' WHERE id=%s",(b['lead'],))
    result=b['call']('book_site_visit',dict(approval_id=approval,idempotency_key='cannot-book'))
    assert result.status=='error'
    assert b['conn'].execute('SELECT count(*) FROM soloestate.bookings WHERE run_id=%s',(b['run'],)).fetchone()[0]==0

def test_optout_blocks_old_followup_review_but_allows_rejection(business):
    b=business
    draft=b['call']('create_followup_draft',dict(lead_id=str(b['lead']),subject='Fictional',body='Not sent',idempotency_key='draft'))
    approval=b['call']('request_approval',dict(kind='followup',draft_id=draft.data['draft']['id'],idempotency_key='review'))
    identifier=UUID(approval.data['approval']['id'])
    b['conn'].execute("UPDATE soloestate.leads SET contact_permission='opted_out' WHERE id=%s",(b['lead'],))
    service=OwnerService(b['tools']._connect)
    digest=service.review(identifier)['payload_hash']
    with pytest.raises(RunConflict): service.decide(identifier,DecisionRequest(decision='approved',expected_payload_hash=digest,idempotency_key=str(uuid4())))
    service.decide(identifier,DecisionRequest(decision='rejected',expected_payload_hash=digest,idempotency_key=str(uuid4())))
