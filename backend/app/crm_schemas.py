from datetime import date, time, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID
import re

from pydantic import AwareDatetime, Field, field_validator, model_validator
from app.tools.schemas import Input
from app.qualification import Timeframe

PHONE_PATTERN = r'^\+[1-9][0-9]{7,14}$'


class Write(Input):
    request_key: str = Field(min_length=1,max_length=128)


class Versioned(Write):
    expected_version: int = Field(gt=0,strict=True)


class LeadCreate(Write):
    name: str = Field(min_length=1,max_length=200)
    email: str = Field(min_length=3,max_length=254)
    preferred_localities: list[str] | None = Field(default=None,min_length=1,max_length=20)
    budget_min: Decimal | None = Field(default=None,ge=0,max_digits=14,decimal_places=2,allow_inf_nan=False)
    budget_max: Decimal | None = Field(default=None,ge=0,max_digits=14,decimal_places=2,allow_inf_nan=False)
    bedrooms: int | None = Field(default=None,ge=1,le=30,strict=True)
    purchase_timeframe: Timeframe | None = None
    status: Literal['new','contacted','qualified','closed'] = 'new'
    notes: str = Field(default='',max_length=5000)

    @field_validator('email')
    @classmethod
    def email_address(cls,value):
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',value):
            raise ValueError('Enter a valid email address')
        return value.lower()

    @field_validator('preferred_localities')
    @classmethod
    def localities(cls,value):
        if value is None: return None
        cleaned=[]
        seen=set()
        for item in value:
            normalized=item.strip()
            if normalized.lower() not in seen:
                cleaned.append(normalized)
                seen.add(normalized.lower())
        if any(not item or len(item)>120 for item in cleaned):
            raise ValueError('Localities must be nonempty and at most 120 characters')
        return cleaned

    @model_validator(mode='after')
    def budgets(self):
        if self.budget_max is not None and self.budget_min is not None and self.budget_max<self.budget_min: raise ValueError('Maximum budget must cover minimum budget')
        return self


class LeadUpdate(LeadCreate):
    expected_version: int = Field(gt=0,strict=True)


class Archive(Versioned):
    archived: bool = Field(strict=True)


class PropertyCreate(Write):
    title: str = Field(min_length=1,max_length=200)
    locality: str = Field(min_length=1,max_length=120)
    price: Decimal = Field(gt=0,max_digits=14,decimal_places=2,allow_inf_nan=False)
    bedrooms: int = Field(ge=1,le=30,strict=True)
    description: str = Field(default='',max_length=5000)
    active: bool = Field(default=True,strict=True)


    concurrent_visit_capacity: int | None = Field(default=None, gt=0)


class PropertyUpdate(PropertyCreate):
    expected_version: int = Field(gt=0,strict=True)


class SlotCreate(Write):
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    resource_id: UUID | None = None

    @model_validator(mode='after')
    def duration(self):
        if not timedelta(0)<self.ends_at-self.starts_at<=timedelta(hours=8):
            raise ValueError('Visit duration must be positive and at most eight hours')
        return self


class AppointmentRequest(Write):
    lead_id: UUID
    property_id: UUID
    slot_id: UUID
    resource_id: UUID | None = None
    replaces_booking_id: UUID | None = None
    expected_version: int | None = Field(default=None,gt=0,strict=True)

    @model_validator(mode='after')
    def replacement_version(self):
        if (self.replaces_booking_id is None)!=(self.expected_version is None):
            raise ValueError('A replacement requires the original booking version')
        return self


class Confirm(Write):
    expected_payload_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


class Cancel(Versioned):
    reason: str = Field(min_length=1,max_length=1000)


class Qualify(Versioned):
    reason: str = Field(min_length=1,max_length=1000,pattern=r"\S")


class FaqCreate(Write):
    scope: Literal['business','property']
    property_id: UUID | None = None
    question: str = Field(min_length=1,max_length=500,pattern=r"\S")
    answer: str = Field(min_length=1,max_length=5000,pattern=r"\S")

    @model_validator(mode='after')
    def scope_property(self):
        if (self.scope=='property')==(self.property_id is None):
            raise ValueError('Property FAQs require a property; business FAQs forbid one')
        return self


class FaqVersion(Versioned):
    question: str = Field(min_length=1,max_length=500,pattern=r"\S")
    answer: str = Field(min_length=1,max_length=5000,pattern=r"\S")


class FaqArchive(Versioned):
    archived: bool = Field(strict=True)


class ConversationOpen(Write):
    lead_id: UUID
    property_id: UUID | None = None


class MessageAppend(Versioned):
    role: Literal['customer','owner']
    body: str = Field(min_length=1,max_length=5000,pattern=r"\S")
    supersedes_message_id: UUID | None = None


class DraftRequest(Versioned):
    message_id: UUID


class HandoffUpdate(Versioned):
    action: Literal['request','resolve']
    reason: str = Field(min_length=1,max_length=1000,pattern=r"\S")


class ReviewUpdate(Versioned):
    action: Literal['acknowledge','resolve','reopen']
    reason: str = Field(min_length=1,max_length=1000,pattern=r"\S")


class TaskCreate(Write):
    lead_id: UUID
    property_id: UUID | None = None
    title: str = Field(min_length=1,max_length=200,pattern=r"\S")
    notes: str = Field(default='',max_length=5000)
    due_at: AwareDatetime | None = None
    assignee: str = Field(default='owner',min_length=1,max_length=120,pattern=r"\S")


class TaskUpdate(Versioned):
    title: str | None = Field(default=None,min_length=1,max_length=200,pattern=r"\S")
    notes: str | None = Field(default=None,max_length=5000)
    due_at: AwareDatetime | None = None
    assignee: str | None = Field(default=None,min_length=1,max_length=120,pattern=r"\S")
    property_id: UUID | None = None
    status: Literal['open','in_progress','in_review','done','cancelled'] | None = None
    outcome: str | None = Field(default=None,max_length=2000)
    reason: str = Field(min_length=1,max_length=1000,pattern=r"\S")


class OutcomeUpdate(Versioned):
    visit_outcome: Literal['completed','no_show'] | None = None
    outcome_note: str | None = Field(default=None,max_length=2000)
    followup_state: Literal['none','pending','done'] | None = None
    followup_note: str | None = Field(default=None,max_length=2000)
    reason: str = Field(min_length=1,max_length=1000,pattern=r"\S")


class PolicyUpdate(Versioned):
    reason: str = Field(min_length=1,max_length=1000,pattern=r"\S")
    timezone: str | None = Field(default=None,min_length=1,max_length=64)
    working_days: list[int] | None = None
    weekly_holiday: int | None = Field(default=None,ge=0,le=6,strict=True)
    hours_start: time | None = None
    hours_end: time | None = None
    holiday_dates: list[date] | None = None
    notice_hours: int | None = Field(default=None,gt=0,strict=True)
    default_duration_min: int | None = Field(default=None,gt=0,strict=True)
    min_duration_min: int | None = Field(default=None,gt=0,strict=True)
    max_duration_min: int | None = Field(default=None,gt=0,strict=True)
    buffer_minutes: int | None = Field(default=None,ge=0,strict=True)


class SlotGeneration(Write):
    resource_id: UUID | None = None
    start_date: date
    end_date: date
    duration_min: int | None = Field(default=None,gt=0,strict=True)


class ResourceCreate(Write):
    name: str = Field(min_length=1,max_length=200)


class ResourceDeactivate(Versioned):
    reason: str = Field(min_length=1,max_length=1000,pattern=r"\S")


class BookingRequestCreate(Write):
    lead_id: UUID
    property_id: UUID | None = None
    conversation_id: UUID | None = None
    channel: Literal['owner','synthetic','website','whatsapp'] = 'owner'
    requested_start: AwareDatetime
    requested_end: AwareDatetime
    requested_resource_id: UUID | None = None
    requested_duration_min: int | None = Field(default=None,gt=0,strict=True)

    @model_validator(mode='after')
    def window(self):
        if self.requested_end<=self.requested_start:
            raise ValueError('Requested window must be positive')
        return self


class BookingConfirm(Versioned):
    pass


class BookingDecide(Versioned):
    decision: Literal['approved','denied']
    expected_request_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    approved_start: AwareDatetime | None = None
    approved_end: AwareDatetime | None = None
    approved_resource_id: UUID | None = None
    reason: str = Field(min_length=1,max_length=1000,pattern=r"\S")

    @model_validator(mode='after')
    def window(self):
        if (self.approved_start is None)!=(self.approved_end is None):
            raise ValueError('Approved window needs both start and end')
        if self.approved_start is not None and self.approved_end<=self.approved_start:
            raise ValueError('Approved window must be positive')
        return self


class BookingCommit(Write):
    expected_request_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


class BookingRequestCancel(Versioned):
    reason: str = Field(min_length=1,max_length=1000,pattern=r"\S")


class IdentityLinkCreate(Write):
    link_id: UUID
    lead_id: UUID
    verified_destination: str = Field(min_length=1,max_length=254)
    reason: str = Field(min_length=1,max_length=1000,pattern=r"\S")


class IdentityRevoke(Versioned):
    reason: str = Field(min_length=1,max_length=1000,pattern=r"\S")


class SuppressionCreate(Write):
    channel: Literal['website','whatsapp']
    destination: str = Field(min_length=1,max_length=254)
    evidence_ref: str | None = Field(default=None,max_length=1000)


class ControlsSet(Write):
    stopped: bool = Field(strict=True)
    reason: str = Field(min_length=1,max_length=1000,pattern=r"\S")


class AutomationPolicyCreate(Write):
    kind: Literal['booking_reminder','followup_nudge']
    hours_before: int | None = Field(default=None,gt=0,strict=True)
    days_after: int | None = Field(default=None,ge=0,strict=True)
    after_event: str | None = Field(default=None,min_length=1,max_length=120)
    frequency: Literal['once','daily','weekly'] = 'once'
    categories: list[str] = Field(default_factory=list)
    lead_statuses: list[str] = Field(default_factory=list)
    max_attempts: int = Field(default=1,gt=0,strict=True)
    per_lead_cooldown_hours: int = Field(default=24,ge=0,strict=True)
    wording: Literal['template','assisted'] = 'template'


class AutomationPolicyUpdate(Versioned):
    hours_before: int | None = Field(default=None,gt=0,strict=True)
    days_after: int | None = Field(default=None,ge=0,strict=True)
    after_event: str | None = Field(default=None,min_length=1,max_length=120)
    frequency: Literal['once','daily','weekly'] | None = None
    categories: list[str] | None = None
    lead_statuses: list[str] | None = None
    max_attempts: int | None = Field(default=None,gt=0,strict=True)
    per_lead_cooldown_hours: int | None = Field(default=None,ge=0,strict=True)
    wording: Literal['template','assisted'] | None = None
    enabled: bool | None = Field(default=None,strict=True)
