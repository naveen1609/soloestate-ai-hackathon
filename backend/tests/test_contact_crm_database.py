"""Opt-out preserves existing appointments and permits rejection/cancellation."""
from uuid import UUID
import pytest
from test_crm_database import crm_db, book, prepare
from app.journey import JourneyUpdate, update
from app.crm_schemas import Confirm, Cancel
from app.agent.store import RunConflict

pytestmark=pytest.mark.integrity_database

def test_optout_during_reschedule_keeps_original_and_allows_cancellation(crm_db):
    b=crm_db
    original=book(b)
    review=prepare(b,1,original)
    update(b['crm'],b['lead']['id'],JourneyUpdate(request_key=b['key'](),expected_version=b['lead']['version'],reason='Fictional opt-out before replacement',contact_permission='opted_out'))
    with pytest.raises(RunConflict):
        b['crm'].commit(UUID(review['id']),Confirm(request_key=b['key'](),expected_payload_hash=review['payload_hash']))
    assert b['crm'].detail('appointments',original['id'])['record']['status']=='confirmed'
    assert b['crm'].request_detail(UUID(review['id']))['record']['approval_status']=='pending'
    b['crm'].reject(UUID(review['id']),Confirm(request_key=b['key'](),expected_payload_hash=review['payload_hash']))
    cancelled=b['crm'].cancel(original['id'],Cancel(request_key=b['key'](),expected_version=original['version'],reason='Fictional owner cancellation'))
    assert cancelled['record']['status']=='cancelled'
