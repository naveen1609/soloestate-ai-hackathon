import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

ToolName = Literal["get_leads", "get_property_matches", "get_available_slots", "create_followup_draft", "request_approval", "book_site_visit", "get_run_status"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunRequest(Contract):
    goal: str = Field(min_length=1, max_length=20000)
    request_key: str = Field(min_length=1, max_length=128)
    mode: Literal["read_only", "draft_first"] = "draft_first"


class PlanStep(Contract):
    tool: ToolName
    purpose: str = Field(min_length=1, max_length=500)
    required: bool = Field(strict=True)


class Plan(Contract):
    summary: str = Field(min_length=1, max_length=1000)
    steps: list[PlanStep] = Field(min_length=1, max_length=12)
    owner_approval_required: Literal[True]


class Finish(Contract):
    action: Literal["finish"]
    disposition: Literal["complete", "blocked"]
    step_sequences: list[Annotated[int, Field(strict=True, gt=0)]] = Field(max_length=50)


def json_object(text: str) -> dict:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("Duplicate JSON field")
            value[key] = item
        return value

    def invalid_constant(value):
        raise ValueError("Non-finite JSON number")

    value = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value
