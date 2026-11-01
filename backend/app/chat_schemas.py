"""Visitor chat input and public response schemas. No actor, role, lead or model inputs."""
import re
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.crm_schemas import Versioned, Write
from app.qualification import Timeframe


class ChatStart(Write):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=254)

    @field_validator('email')
    @classmethod
    def email_address(cls, value):
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            raise ValueError('Enter a valid email address')
        return value.lower()


class ChatSend(Versioned):
    body: str = Field(min_length=1, max_length=5000, pattern=r"\S")


class ChatRequirements(Versioned):
    preferred_localities: list[str] | None = Field(default=None, min_length=1, max_length=20)
    budget_min: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    budget_max: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    bedrooms: int | None = Field(default=None, ge=1, le=30, strict=True)
    purchase_timeframe: Timeframe | None = None

    @field_validator('preferred_localities')
    @classmethod
    def localities(cls, value):
        if value is None:
            return None
        cleaned = []
        seen = set()
        for item in value:
            normalized = item.strip()
            if normalized.lower() not in seen:
                cleaned.append(normalized)
                seen.add(normalized.lower())
        if any(not item or len(item) > 120 for item in cleaned):
            raise ValueError('Localities must be nonempty and at most 120 characters')
        return cleaned

    @model_validator(mode='after')
    def budgets(self):
        if self.budget_max is not None and self.budget_min is not None and self.budget_max < self.budget_min:
            raise ValueError('Maximum budget must cover minimum budget')
        return self


class ChatHandoff(Versioned):
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")


class ChatContext(Versioned):
    property_id: UUID


class PublicMessage(BaseModel):
    id: UUID
    role: str
    body: str
    grounding: str
    unsupported_reason: str | None
    citations: list
    citations_superseded: bool
    confidence: str | None
    supersedes_message_id: UUID | None
    created_at: datetime


class PublicLead(BaseModel):
    id: UUID
    version: int
    name: str
    preferred_localities: list[str] | None
    budget_min: Decimal | None
    budget_max: Decimal | None
    bedrooms: int | None
    purchase_timeframe: str | None
    readiness: dict


class PublicConversation(BaseModel):
    id: UUID
    lead_id: UUID
    status: str
    handoff_state: str
    property_id: UUID | None
    version: int
    created_at: datetime


class ChatStartResult(BaseModel):
    lead: PublicLead
    conversation: PublicConversation
    messages: list[PublicMessage]
    token: str


class ChatThread(BaseModel):
    lead: PublicLead
    conversation: PublicConversation
    messages: list[PublicMessage]
    truncated: bool
    drafting_blocked: str | None


class ChatSendResult(BaseModel):
    message: PublicMessage
    reply: PublicMessage
    conversation: PublicConversation


class ChatLeadResult(BaseModel):
    lead: PublicLead


class ChatConversationResult(BaseModel):
    conversation: PublicConversation


class ChatSlots(BaseModel):
    items: list
    truncated: bool
    provisional: bool
