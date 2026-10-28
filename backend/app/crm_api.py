from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime
from app.owner_api import require_owner
from app.crm import CRM, CRMError
from app import crm_schemas as s

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
def appointments(q:str=Query('',max_length=200),sort:str='starts_asc',page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),status:Literal['confirmed','cancelled']|None=None,period:Literal['upcoming','past']|None=None,lead_id:UUID|None=None,property_id:UUID|None=None,since:AwareDatetime|None=None,until:AwareDatetime|None=None,crm:CRM=Depends(service)):
    if since and until and since>=until: raise CRMError('Invalid date range.',422)
    return crm.listing('appointments',q=q,sort=sort,page=page,page_size=page_size,status=status,period=period,lead_id=lead_id,property_id=property_id,since=since,until=until)


@router.get('/appointments/{identifier}')
def appointment(identifier:UUID,crm:CRM=Depends(service)): return crm.detail('appointments',identifier)


@router.post('/appointments/{identifier}/cancel')
def cancel(identifier:UUID,body:s.Cancel,crm:CRM=Depends(service)): return crm.cancel(identifier,body)


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
