from datetime import timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID
import re

from pydantic import AwareDatetime, Field, field_validator, model_validator
from app.tools.schemas import Input
from app.qualification import Timeframe


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


class PropertyUpdate(PropertyCreate):
    expected_version: int = Field(gt=0,strict=True)


class SlotCreate(Write):
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @model_validator(mode='after')
    def duration(self):
        if not timedelta(0)<self.ends_at-self.starts_at<=timedelta(hours=8):
            raise ValueError('Visit duration must be positive and at most eight hours')
        return self


class AppointmentRequest(Write):
    lead_id: UUID
    property_id: UUID
    slot_id: UUID
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
