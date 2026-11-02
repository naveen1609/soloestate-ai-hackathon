from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime
from app.owner_api import require_owner
from app.crm import CRM, CRMError
from app import crm_schemas as s
from app.journey import JourneyUpdate

router=APIRouter(prefix='/owner/crm',dependencies=[Depends(require_owner)])


def service(): return CRM()


@router.get('/summary')
def summary(crm:CRM=Depends(service)): return crm.summary()


@router.get('/operations/{key}')
def operation(key:str,crm:CRM=Depends(service)):
    if not 1<=len(key)<=128: raise CRMError('Invalid operation key.',422)
    return crm.operation(key)


@router.get('/leads')
def leads(q:str=Query('',max_length=200),sort:str='created_desc',page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),status:Literal['new','contacted','qualified','closed']|None=None,archived:bool=False,crm:CRM=Depends(service)):
    return crm.listing('leads',q=q,sort=sort,page=page,page_size=page_size,status=status,archived=archived)


@router.post('/leads')
def create_lead(body:s.LeadCreate,crm:CRM=Depends(service)): return crm.save('leads',body)


@router.get('/leads/{identifier}')
def lead(identifier:UUID,crm:CRM=Depends(service)): return crm.detail('leads',identifier)


@router.patch('/leads/{identifier}')
def edit_lead(identifier:UUID,body:s.LeadUpdate,crm:CRM=Depends(service)): return crm.save('leads',body,identifier)


@router.post('/leads/{identifier}/archive')
def archive(identifier:UUID,body:s.Archive,crm:CRM=Depends(service)): return crm.archive(identifier,body)


@router.get('/properties')
def properties(q:str=Query('',max_length=200),sort:str='created_desc',page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),active:bool|None=None,locality:str=Query('',max_length=120),bedrooms:int|None=Query(None,ge=1,le=30),min_price:Decimal|None=Query(None,ge=0,allow_inf_nan=False),max_price:Decimal|None=Query(None,ge=0,allow_inf_nan=False),crm:CRM=Depends(service)):
    if min_price is not None and max_price is not None and min_price>max_price: raise CRMError('Invalid price range.',422)
    return crm.listing('properties',q=q,sort=sort,page=page,page_size=page_size,active=active,locality=locality,bedrooms=bedrooms,min_price=min_price,max_price=max_price)


@router.post('/properties')
def create_property(body:s.PropertyCreate,crm:CRM=Depends(service)): return crm.save('properties',body)


@router.get('/properties/{identifier}')
def property_detail(identifier:UUID,crm:CRM=Depends(service)): return crm.detail('properties',identifier)


@router.patch('/properties/{identifier}')
def edit_property(identifier:UUID,body:s.PropertyUpdate,crm:CRM=Depends(service)): return crm.save('properties',body,identifier)


@router.get('/appointments')
def appointments(q:str=Query('',max_length=200),sort:str='starts_asc',page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),status:Literal['confirmed','cancelled']|None=None,period:Literal['upcoming','past']|None=None,lead_id:UUID|None=None,property_id:UUID|None=None,since:AwareDatetime|None=None,until:AwareDatetime|None=None,outcome:Literal['completed','no_show']|None=None,crm:CRM=Depends(service)):
    if since and until and since>=until: raise CRMError('Invalid date range.',422)
    return crm.listing('appointments',q=q,sort=sort,page=page,page_size=page_size,status=status,period=period,lead_id=lead_id,property_id=property_id,since=since,until=until,outcome=outcome)


@router.get('/appointments/{identifier}')
def appointment(identifier:UUID,crm:CRM=Depends(service)): return crm.detail('appointments',identifier)


@router.post('/appointments/{identifier}/cancel')
def cancel(identifier:UUID,body:s.Cancel,crm:CRM=Depends(service)): return crm.cancel(identifier,body)


@router.post('/appointments/{identifier}/outcome')
def update_outcome(identifier:UUID,body:s.OutcomeUpdate,crm:CRM=Depends(service)):
    from app.visits import record_outcome
    return record_outcome(crm,identifier,body)


@router.get('/visit-slots')
def slots(since:AwareDatetime,until:AwareDatetime,crm:CRM=Depends(service)):
    from datetime import timedelta
    if not timedelta(0)<until-since<=timedelta(days=31): raise CRMError('Availability window must be 1–31 days.',422)
    return crm.slots(since,until)


@router.post('/visit-slots')
def create_slot(body:s.SlotCreate,crm:CRM=Depends(service)): return crm.slot(body)


@router.post('/appointment-requests')
def prepare(body:s.AppointmentRequest,crm:CRM=Depends(service)): return crm.prepare(body)


@router.get('/appointment-requests/{identifier}')
def request_detail(identifier:UUID,crm:CRM=Depends(service)): return crm.request_detail(identifier)


@router.post('/appointment-requests/{identifier}/commit')
def commit(identifier:UUID,body:s.Confirm,crm:CRM=Depends(service)): return crm.commit(identifier,body)


@router.post('/appointment-requests/{identifier}/reject')
def reject(identifier:UUID,body:s.Confirm,crm:CRM=Depends(service)): return crm.reject(identifier,body)


@router.post('/leads/{identifier}/journey')
def update_journey(identifier:UUID,body:JourneyUpdate,crm:CRM=Depends(service)):
    from app.journey import update
    return update(crm,identifier,body)


@router.get('/leads/{identifier}/activities')
def lead_activities(identifier:UUID,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),crm:CRM=Depends(service)):
    from app.journey import activities
    return activities(crm,identifier,page,page_size)


@router.post('/leads/{identifier}/qualify')
def qualify(identifier:UUID,body:s.Qualify,crm:CRM=Depends(service)):
    from app.qualification import confirm
    return confirm(crm,identifier,body)


@router.get('/leads/{identifier}/matches')
def matches(identifier:UUID,limit:int=Query(5,ge=1,le=20),crm:CRM=Depends(service)):
    from psycopg.rows import dict_row
    from app.qualification import property_matches
    with crm.connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            lead=cur.execute('SELECT * FROM soloestate.leads WHERE id=%s AND archived_at IS NULL FOR SHARE',(identifier,)).fetchone()
            if lead is None: raise CRMError('Active lead not found.',404)
            return property_matches(cur,lead,limit).model_dump(mode='json')


@router.post('/faqs')
def create_faq(body:s.FaqCreate,crm:CRM=Depends(service)):
    from app.faq import create
    return create(crm,body)


@router.get('/faqs')
def faqs(scope:Literal['business','property']|None=None,property_id:UUID|None=None,status:Literal['active','archived']|None=None,q:str=Query('',max_length=200),page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),crm:CRM=Depends(service)):
    from app.faq import listing
    return listing(crm,scope=scope,property_id=property_id,status=status,q=q,page=page,page_size=page_size)


@router.get('/faqs/{identifier}')
def faq_detail(identifier:UUID,crm:CRM=Depends(service)):
    from app.faq import detail
    return detail(crm,identifier)


@router.post('/faqs/{identifier}/versions')
def faq_version(identifier:UUID,body:s.FaqVersion,crm:CRM=Depends(service)):
    from app.faq import new_version
    return new_version(crm,identifier,body)


@router.post('/faqs/{identifier}/archive')
def faq_archive(identifier:UUID,body:s.FaqArchive,crm:CRM=Depends(service)):
    from app.faq import archive
    return archive(crm,identifier,body)


@router.post('/conversations')
def create_conversation(body:s.ConversationOpen,crm:CRM=Depends(service)):
    from app.conversation import open_conversation
    return open_conversation(crm,body)


@router.get('/conversations')
def conversations(lead_id:UUID|None=None,status:Literal['open','pending_owner','resolved']|None=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),crm:CRM=Depends(service)):
    from app.conversation import listing
    return listing(crm,lead_id=lead_id,status=status,page=page,page_size=page_size)


@router.get('/conversations/{identifier}')
def conversation_detail(identifier:UUID,crm:CRM=Depends(service)):
    from app.conversation import detail
    return detail(crm,identifier)


@router.post('/conversations/{identifier}/messages')
def append_message(identifier:UUID,body:s.MessageAppend,crm:CRM=Depends(service)):
    from app.conversation import append
    return append(crm,identifier,body)


@router.post('/conversations/{identifier}/draft')
async def draft_response(identifier:UUID,body:s.DraftRequest,crm:CRM=Depends(service)):
    from app.config import Settings
    from app.conversation import draft
    from app.services.meta_model import MetaModelService
    async with MetaModelService(Settings()) as model:
        return await draft(crm,identifier,body,model)


@router.post('/conversations/{identifier}/handoff')
def update_handoff(identifier:UUID,body:s.HandoffUpdate,crm:CRM=Depends(service)):
    from app.conversation import handoff
    return handoff(crm,identifier,body)


@router.post('/conversations/{identifier}/review')
def review_conversation(identifier:UUID,body:s.ReviewUpdate,crm:CRM=Depends(service)):
    from app.conversation import review
    return review(crm,identifier,body)


@router.post('/followup-tasks')
def create_task(body:s.TaskCreate,crm:CRM=Depends(service)):
    from app.followups import create_task
    return create_task(crm,body)


@router.get('/followup-tasks')
def tasks(lead_id:UUID|None=None,status:Literal['open','in_progress','in_review','done','cancelled']|None=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),crm:CRM=Depends(service)):
    from app.followups import listing
    return listing(crm,lead_id=lead_id,status=status,page=page,page_size=page_size)


@router.get('/followup-tasks/{identifier}')
def task_detail(identifier:UUID,crm:CRM=Depends(service)):
    from app.followups import detail
    return detail(crm,identifier)


@router.post('/followup-tasks/{identifier}/update')
def edit_task(identifier:UUID,body:s.TaskUpdate,crm:CRM=Depends(service)):
    from app.followups import update_task
    return update_task(crm,identifier,body)


@router.get('/business-policy')
def business_policy(crm:CRM=Depends(service)):
    from app.policies import get_policy
    return get_policy(crm)


@router.patch('/business-policy')
def edit_policy(body:s.PolicyUpdate,crm:CRM=Depends(service)):
    from app.policies import update_policy
    return update_policy(crm,body)


@router.get('/resources')
def resources(crm:CRM=Depends(service)):
    from app.resources import listing
    return listing(crm)


@router.post('/resources')
def create_resource(body:s.ResourceCreate,crm:CRM=Depends(service)):
    from app.resources import create
    return create(crm,body)


@router.post('/resources/{identifier}/deactivate')
def deactivate_resource(identifier:UUID,body:s.ResourceDeactivate,crm:CRM=Depends(service)):
    from app.resources import deactivate
    return deactivate(crm,identifier,body)


@router.post('/visit-slots/generate')
def generate_visit_slots(body:s.SlotGeneration,crm:CRM=Depends(service)):
    from app.policies import generate_slots
    return generate_slots(crm,body)


@router.get('/booking-requests')
def booking_requests(lead_id:UUID|None=None,status:Literal['proposed','awaiting_owner','approved','denied','expired','booked','cancelled']|None=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),crm:CRM=Depends(service)):
    from app.booking_requests import listing
    return listing(crm,lead_id=lead_id,status=status,page=page,page_size=page_size)


@router.get('/booking-requests/{identifier}')
def booking_request_detail(identifier:UUID,crm:CRM=Depends(service)):
    from app.booking_requests import detail
    return detail(crm,identifier)


@router.post('/booking-requests')
def create_booking_request(body:s.BookingRequestCreate,crm:CRM=Depends(service)):
    from app.booking_requests import propose
    return propose(crm,body)


@router.post('/booking-requests/{identifier}/confirm')
def confirm_booking_request(identifier:UUID,body:s.BookingConfirm,crm:CRM=Depends(service)):
    from app.booking_requests import confirm
    result=confirm(crm,identifier,body)
    if not result.get('confirmed'): raise CRMError(result.get('message') or 'Booking request cannot be confirmed.',409)
    return result


@router.post('/booking-requests/{identifier}/decide')
def decide_booking_request(identifier:UUID,body:s.BookingDecide,crm:CRM=Depends(service)):
    from app.booking_requests import decide
    result=decide(crm,identifier,body)
    if not result.get('decided'): raise CRMError(result.get('message') or 'Booking request cannot be decided.',409)
    return result


@router.post('/booking-requests/{identifier}/commit')
def commit_booking_request(identifier:UUID,body:s.BookingCommit,crm:CRM=Depends(service)):
    from app.booking_requests import commit
    result=commit(crm,identifier,body)
    if not result.get('committed'): raise CRMError(result.get('message') or 'Booking request cannot be committed.',409)
    return result


@router.post('/booking-requests/{identifier}/cancel')
def cancel_booking_request(identifier:UUID,body:s.BookingRequestCancel,crm:CRM=Depends(service)):
    from app.booking_requests import cancel
    return cancel(crm,identifier,body)


@router.get('/identity-links')
def identity_links(lead_id:UUID|None=None,status:Literal['observed','active','revoked','superseded']|None=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),crm:CRM=Depends(service)):
    from app.identity_links import listing
    return listing(crm,lead_id=lead_id,status=status,page=page,page_size=page_size)


@router.post('/identity-links/link')
def link_identity(body:s.IdentityLinkCreate,crm:CRM=Depends(service)):
    from app.identity_links import link
    return link(crm,body)


@router.post('/identity-links/{identifier}/revoke')
def revoke_identity(identifier:UUID,body:s.IdentityRevoke,crm:CRM=Depends(service)):
    from app.identity_links import revoke
    return revoke(crm,identifier,body)


@router.get('/suppression-list')
def suppression_list(crm:CRM=Depends(service)):
    from app.identity_links import suppression_listing
    return suppression_listing(crm)


@router.post('/suppression-list')
def create_suppression(body:s.SuppressionCreate,crm:CRM=Depends(service)):
    from app.identity_links import suppress
    return suppress(crm,body)


@router.get('/automation-controls')
def automation_controls(crm:CRM=Depends(service)):
    from app.policies import get_controls
    return get_controls(crm)


@router.post('/automation-controls/stop')
def set_automation_stop(body:s.ControlsSet,crm:CRM=Depends(service)):
    from app.policies import set_stop
    return set_stop(crm,body)


@router.get('/automation-policies')
def automation_policies(crm:CRM=Depends(service)):
    from app.policies import list_automation_policies
    return list_automation_policies(crm)


@router.post('/automation-policies')
def create_automation_policy(body:s.AutomationPolicyCreate,crm:CRM=Depends(service)):
    from app.policies import create_automation_policy
    return create_automation_policy(crm,body)


@router.patch('/automation-policies/{identifier}')
def edit_automation_policy(identifier:UUID,body:s.AutomationPolicyUpdate,crm:CRM=Depends(service)):
    from app.policies import update_automation_policy
    return update_automation_policy(crm,identifier,body)
