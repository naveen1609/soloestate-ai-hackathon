"""Pydantic schemas for SoloEstate AI Growth Engine-lite."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, Field, field_validator


# --- Grounding Schemas ---

class PropertyGrounding(BaseModel):
    property_id: UUID
    title: str = Field(min_length=1)
    locality: str = Field(min_length=1)
    price_inr: Decimal = Field(gt=0)
    bedrooms: int = Field(ge=1, le=30)
    description: str = Field(default="")
    active: bool = True
    grounding_version: int = Field(ge=1, default=1)
    grounding_hash: str = Field(min_length=64, max_length=64)


class GroundingValidationResult(BaseModel):
    is_valid: bool
    discrepancies: list[str] = Field(default_factory=list)
    verified_claims: dict[str, Any] = Field(default_factory=dict)


# --- Voice & Marketing Brief Schemas ---

class VoiceTranscribeRequest(BaseModel):
    property_id: UUID
    audio_base64: str | None = Field(default=None, max_length=15_000_000)  # ~10MB bound
    audio_format: str = "webm"
    sample_rate: int = 16000
    raw_text: str | None = Field(default=None, max_length=10000)

    @field_validator("audio_format")
    @classmethod
    def validate_format(cls, v: str) -> str:
        clean = v.strip().lower()
        allowed = {"webm", "wav", "mp3", "m4a", "ogg", "flac"}
        if clean not in allowed:
            raise ValueError(f"Unsupported audio format '{v}'. Allowed: {', '.join(allowed)}")
        return clean


class VoiceTranscribeResponse(BaseModel):
    transcript: str
    model_id: str
    confidence: float
    detected_language: str = "en"
    source: str = "muse_voice_transcribe"


class MarketingBriefCreate(BaseModel):
    property_id: UUID
    input_mode: Literal["text", "voice"] = "text"
    raw_input: str = Field(min_length=1, max_length=10000)
    target_audience: str = Field(default="", max_length=500)
    key_selling_points: list[str] = Field(default_factory=list)
    tone: Literal["professional", "luxury", "warm", "urgent", "festive"] = "professional"
    campaign_objective: Literal["lead_generation", "brand_awareness", "open_house", "festive_offer", "price_drop"] = "lead_generation"
    special_offers: str = Field(default="", max_length=1000)


class MarketingBriefResponse(BaseModel):
    id: UUID
    property_id: UUID
    input_mode: str
    raw_input: str
    target_audience: str
    key_selling_points: list[str]
    tone: str
    campaign_objective: str
    special_offers: str
    status: str
    created_at: datetime
    updated_at: datetime


# --- Property Visual Intelligence (Meta SAM 3.1) ---

class PropertyImageItem(BaseModel):
    id: UUID
    property_id: UUID
    image_url: str
    caption: str = ""
    is_authentic: bool = True
    features: list[str] = Field(default_factory=list)
    created_at: datetime


class SegmentedFeature(BaseModel):
    feature_id: str
    label: str
    confidence: float = Field(ge=0.0, le=1.0)
    bounding_box: list[float] | None = None  # [ymin, xmin, ymax, xmax] normalized
    segmentation_mask_svg: str | None = None
    description: str = ""


class SAMSegmentRequest(BaseModel):
    property_id: UUID
    image_id: UUID
    labels_to_detect: list[str] = Field(default_factory=lambda: ["balcony"], min_length=1, max_length=6)
    demo_mode: bool = False


class SAMSegmentResponse(BaseModel):
    image_id: UUID
    property_id: UUID
    model_id: str = "meta-models/sam-3.1"
    features: list[SegmentedFeature]
    architectural_summary: str
    is_authentic: bool = True
    source: str = "sam_3_1"


# --- Creative Studio (Meta Muse Image) ---

class CreativeGenerateRequest(BaseModel):
    kit_id: UUID
    property_id: UUID
    source_image_id: UUID | None = None
    asset_type: Literal["segmented_feature", "concept_graphic", "ad_banner", "story_visual"] = "concept_graphic"
    creative_prompt: str = Field(min_length=1, max_length=2000)
    aspect_ratio: Literal["1:1", "4:5", "9:16", "16:9"] = "1:1"
    style: str = "modern-minimalist"


class CreativeAssetResponse(BaseModel):
    id: UUID
    kit_id: UUID
    property_id: UUID
    source_image_id: UUID | None = None
    asset_type: str
    is_authentic: bool = False
    concept_watermark: str = "AI Creative Concept - Not Actual Property Photography"
    image_url: str
    segmentation_data: dict[str, Any] = Field(default_factory=dict)
    prompt_text: str
    prompt_hash: str
    model_id: str
    status: Literal["draft", "approved", "rejected", "superseded"] = "draft"
    version: int = 1
    source: str = "muse_image_creative"
    rejection_reason: str = ""
    created_at: datetime


# --- Marketing Intelligence (Meta Muse Spark) ---

class ContentItemGenerateRequest(BaseModel):
    kit_id: UUID
    channels: list[Literal["instagram_feed", "instagram_story", "facebook_feed", "festival_offer"]] = Field(
        default_factory=lambda: ["instagram_feed", "facebook_feed"]
    )
    custom_tone: str | None = None


class FactReference(BaseModel):
    fact_key: str
    crm_version: int
    rendered_value: str
    source_field: str | None = None


CopyTemplate = Literal["standard_residence", "compact_story", "detailed_showcase", "festive_spotlight"]


class StructuredCopy(BaseModel):
    template_id: CopyTemplate = "standard_residence"
    channel: str = "instagram_feed"
    presentation_style: str = "professional"
    fact_references: list[FactReference] = Field(default_factory=list)
    prose_slots: dict[str, str] = Field(default_factory=dict)
    rendered_at_crm_version: int = 1


class ContentItemResponse(BaseModel):
    id: UUID
    kit_id: UUID
    channel: Literal["instagram_feed", "instagram_story", "facebook_feed", "festival_offer"]
    headline: str
    body: str
    call_to_action: str
    hashtags: list[str]
    creative_asset_id: UUID | None = None
    grounding_verified: bool
    verification_notes: str
    fact_references: list[FactReference] = Field(default_factory=list)
    structured_copy: StructuredCopy | None = None
    status: Literal["draft", "approved", "rejected", "superseded"] = "draft"
    approval_notes: str = ""
    approved_at: datetime | None = None
    version: int = 1
    source: str = "muse_spark"
    claim_checks: dict[str, Any] = Field(default_factory=dict)
    rejection_reason: str = ""
    created_at: datetime
    updated_at: datetime


class CampaignPlanRequest(BaseModel):
    kit_id: UUID
    suggested_daily_budget: Decimal | None = None
    suggested_duration_days: int | None = None
    target_audience: str | None = None


class CampaignPlanResponse(BaseModel):
    id: UUID
    kit_id: UUID
    property_id: UUID
    objective: str
    target_audience_spec: dict[str, Any]
    recommended_placements: list[str]
    suggested_daily_budget: Decimal
    suggested_duration_days: int
    rationale: str
    staged_launch_payload: dict[str, Any]
    status: Literal["draft", "approved", "rejected", "superseded"] = "draft"
    approved_at: datetime | None = None
    version: int = 1
    source: str = "muse_spark_planner"
    rejection_reason: str = ""
    created_at: datetime
    updated_at: datetime


# --- Kit & Workflow Orchestration (LocalPipelineOrchestrator) ---

class KitSummaryResponse(BaseModel):
    id: UUID
    property_id: UUID
    brief_id: UUID | None
    grounding: PropertyGrounding
    objective: str
    status: Literal["draft", "partially_approved", "approved", "archived"]
    content_items: list[ContentItemResponse] = Field(default_factory=list)
    creative_assets: list[CreativeAssetResponse] = Field(default_factory=list)
    campaign_plan: CampaignPlanResponse | None = None
    version: int = 1
    created_at: datetime
    updated_at: datetime


class OrchestrateKitRequest(BaseModel):
    property_id: UUID
    brief: MarketingBriefCreate
    selected_image_ids: list[UUID] = Field(default_factory=list)
    channels: list[Literal["instagram_feed", "instagram_story", "facebook_feed", "festival_offer"]] = Field(
        default_factory=lambda: ["instagram_feed", "instagram_story", "facebook_feed"]
    )
    generate_creative: bool = True
    generate_plan: bool = True


class OrchestrationStepTrace(BaseModel):
    step_name: str
    model_id: str
    status: Literal["completed", "skipped", "error"]
    details: str
    duration_ms: int = 0
    source: str = "live_provider"


class OrchestrateKitResponse(BaseModel):
    kit: KitSummaryResponse
    steps_executed: list[OrchestrationStepTrace]


# --- Approval & Export Schemas ---

class ContentItemEditDecision(BaseModel):
    status: Literal["approved", "rejected", "draft"]
    expected_version: int = Field(ge=1, description="Expected version for optimistic concurrency control")
    headline: str | None = None
    body: str | None = None
    call_to_action: str | None = None
    hashtags: list[str] | None = None
    template_id: CopyTemplate | None = None
    approval_notes: str = ""


class CreativeAssetDecision(BaseModel):
    status: Literal["approved", "rejected", "draft"]
    expected_version: int = Field(ge=1, description="Expected version for optimistic concurrency control")
    notes: str = ""


class CampaignPlanDecision(BaseModel):
    plan_id: UUID
    status: Literal["approved", "rejected", "draft"]
    expected_version: int = Field(ge=1, description="Expected version for optimistic concurrency control")
    notes: str = ""


class KitExportPackage(BaseModel):
    kit_id: UUID
    property_id: UUID
    property_title: str
    locality: str
    price_formatted: str
    approved_content: list[dict[str, Any]]
    approved_creatives: list[dict[str, Any]]
    campaign_plan: dict[str, Any] | None
    export_timestamp: datetime
    safety_disclaimer: str = (
        "Content generated via SoloEstate AI Growth Engine-lite. Grounded in verified CRM property records. "
        "Creative assets are concepts and watermarked to preserve property authenticity. "
        "No autonomous external publication or ad spend is authorized without explicit owner action."
    )
