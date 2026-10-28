from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ToolContext(BaseModel):
    # Server-supplied context, never part of a model tool's arguments.
    run_id: UUID


class Window(Input):
    since: AwareDatetime
    until: AwareDatetime
    limit: int = Field(default=50, ge=1, le=100, strict=True)

    @model_validator(mode="after")
    def valid_window(self):
        if not timedelta(0) < self.until - self.since <= timedelta(days=31):
            raise ValueError("Window must be positive and no longer than 31 days")
        return self


class GetLeadsInput(Window):
    pass


class GetMatchesInput(Input):
    lead_id: UUID
    limit: int = Field(default=5, ge=1, le=20, strict=True)


class GetSlotsInput(Window):
    pass


class DraftInput(Input):
    lead_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=128)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)


class ApprovalInput(Input):
    kind: Literal["followup", "booking"]
    idempotency_key: str = Field(min_length=1, max_length=128)
    draft_id: UUID | None = None
    lead_id: UUID | None = None
    property_id: UUID | None = None
    slot_id: UUID | None = None

    @model_validator(mode="after")
    def target(self):
        if self.kind == "followup":
            if self.draft_id is None or any(v is not None for v in (self.lead_id, self.property_id, self.slot_id)):
                raise ValueError("Followup approval requires only draft_id")
        elif self.draft_id is not None or any(v is None for v in (self.lead_id, self.property_id, self.slot_id)):
            raise ValueError("Booking approval requires lead_id, property_id and slot_id")
        return self


class BookInput(Input):
    approval_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=128)


class RunStatusInput(Input):
    pass


class Lead(BaseModel):
    id: UUID
    name: str
    email: str
    preferred_localities: list[str]
    budget_min: Decimal
    budget_max: Decimal
    bedrooms: int
    status: Literal["new", "contacted", "qualified", "closed"]
    notes: str
    created_at: datetime


class LeadsOutput(BaseModel):
    leads: list[Lead]
    truncated: bool


class PropertyMatch(BaseModel):
    id: UUID
    title: str
    locality: str
    price: Decimal
    bedrooms: int
    description: str
    score: int
    reasons: list[str]


class MatchesOutput(BaseModel):
    lead_id: UUID
    matches: list[PropertyMatch]
    truncated: bool
    criteria: str = "Active inventory within budget, preferred locality, and at least requested bedrooms; exact bedrooms first, then lowest price."


class Slot(BaseModel):
    id: UUID
    starts_at: datetime
    ends_at: datetime


class SlotsOutput(BaseModel):
    slots: list[Slot]
    truncated: bool
    checked_at: datetime
    booking_requires_recheck: Literal[True] = True


class DraftRecord(BaseModel):
    id: UUID
    run_id: UUID
    lead_id: UUID
    channel: Literal["email"]
    subject: str
    body: str
    version: int
    status: Literal["draft", "approved", "rejected", "sent"]


class DraftOutput(BaseModel):
    draft: DraftRecord
    created: bool
    customer_message_sent: Literal[False] = False


class ApprovalRecord(BaseModel):
    id: UUID
    run_id: UUID
    kind: Literal["followup", "booking"]
    lead_id: UUID
    draft_id: UUID | None
    property_id: UUID | None
    slot_id: UUID | None
    payload: dict[str, Any]
    status: Literal["pending", "approved", "rejected", "consumed"]
    decided_at: datetime | None


class ApprovalOutput(BaseModel):
    approval: ApprovalRecord
    created: bool


class BookingRecord(BaseModel):
    id: UUID
    run_id: UUID
    approval_id: UUID
    lead_id: UUID
    property_id: UUID
    slot_id: UUID
    status: Literal["confirmed", "cancelled"]


class BookingOutput(BaseModel):
    booking: BookingRecord
    created: bool


class RunRecord(BaseModel):
    id: UUID
    goal: str
    status: Literal["pending", "running", "awaiting_approval", "completed", "failed", "cancelled"]
    plan: dict[str, Any] | None
    outcome: dict[str, Any] | None


class StepRecord(BaseModel):
    id: UUID
    sequence: int
    tool_name: str
    status: str
    result: dict[str, Any] | None
    error: dict[str, Any] | None


class RunStatusOutput(BaseModel):
    run: RunRecord
    steps: list[StepRecord]
    steps_truncated: bool
    draft_counts: dict[str, int]
    approval_counts: dict[str, int]
    booking_counts: dict[str, int]


class ToolResult(BaseModel):
    tool: str
    status: Literal["succeeded", "approval_required", "error"]
    data: dict[str, Any] | None = None
    code: str | None = None
    message: str | None = None
