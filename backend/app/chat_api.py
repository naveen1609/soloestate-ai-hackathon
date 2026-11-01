"""Visitor website-chat endpoints. Token-scoped; no owner session accepted."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import AwareDatetime

from app import chat_schemas as s
from app.chat import matches as chat_matches
from app.chat import request_handoff, requirements, resume, scope, send, set_context, slots as chat_slots, start
from app.config import Settings
from app.crm import CRM, CRMError
from app.crm_api import service
from app.tools.schemas import MatchesOutput
from app.visitor_tokens import verify

router = APIRouter(prefix='/chat')
bearer = HTTPBearer(auto_error=False)


def chat_settings():
    return Settings()


def visitor(credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
            config: Settings = Depends(chat_settings)):
    if credentials is None or not credentials.credentials:
        raise CRMError('Chat token is required.', 401)
    return verify(credentials.credentials, config)


@router.post('/start', response_model=s.ChatStartResult)
def start_chat(body: s.ChatStart, crm: CRM = Depends(service), config: Settings = Depends(chat_settings)):
    return start(crm, body, config)


@router.get('/conversations/{identifier}', response_model=s.ChatThread)
def read_chat(identifier: UUID, cid: UUID = Depends(visitor), crm: CRM = Depends(service)):
    scope(cid, identifier)
    return resume(crm, identifier)


@router.post('/conversations/{identifier}/send', response_model=s.ChatSendResult)
async def send_chat(identifier: UUID, body: s.ChatSend, cid: UUID = Depends(visitor), crm: CRM = Depends(service)):
    scope(cid, identifier)
    return await send(crm, identifier, body)


@router.post('/conversations/{identifier}/requirements', response_model=s.ChatLeadResult)
def update_requirements(identifier: UUID, body: s.ChatRequirements, cid: UUID = Depends(visitor),
                        crm: CRM = Depends(service)):
    scope(cid, identifier)
    return requirements(crm, identifier, body)


@router.get('/conversations/{identifier}/matches', response_model=MatchesOutput)
def read_matches(identifier: UUID, limit: int = Query(5, ge=1, le=20), cid: UUID = Depends(visitor),
                 crm: CRM = Depends(service)):
    scope(cid, identifier)
    return chat_matches(crm, identifier, limit)


@router.get('/conversations/{identifier}/slots', response_model=s.ChatSlots)
def read_slots(identifier: UUID, since: AwareDatetime, until: AwareDatetime, cid: UUID = Depends(visitor),
               crm: CRM = Depends(service)):
    scope(cid, identifier)
    return chat_slots(crm, since, until)


@router.post('/conversations/{identifier}/context', response_model=s.ChatConversationResult)
def select_context(identifier: UUID, body: s.ChatContext, cid: UUID = Depends(visitor), crm: CRM = Depends(service)):
    scope(cid, identifier)
    return set_context(crm, identifier, body)


@router.post('/conversations/{identifier}/handoff', response_model=s.ChatConversationResult)
def request_review(identifier: UUID, body: s.ChatHandoff, cid: UUID = Depends(visitor), crm: CRM = Depends(service)):
    scope(cid, identifier)
    return request_handoff(crm, identifier, body)
