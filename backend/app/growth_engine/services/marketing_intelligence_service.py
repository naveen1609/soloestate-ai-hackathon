"""Marketing Intelligence service using Meta Muse Spark.
Generates platform-specific copy variants (Instagram Feed, Instagram Story, Facebook Feed, Festival Offers),
hashtags, calls-to-action, and campaign plans strictly grounded in verified CRM property data.
"""

from datetime import datetime, timezone
from decimal import Decimal
import json
from typing import Any
from uuid import UUID, uuid4

import httpx
from openai import OpenAI, OpenAIError

from app.growth_engine.config import GrowthEngineSettings
from app.growth_engine.schemas import (
    CampaignPlanRequest,
    CampaignPlanResponse,
    ContentItemResponse,
    MarketingBriefResponse,
    PropertyGrounding,
)
from app.growth_engine.services.grounding_service import COPY_TEMPLATES, make_structured_copy, render_structured_copy


class MarketingIntelligenceError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class MetaMuseSparkService:
    def __init__(
        self,
        settings: GrowthEngineSettings | None = None,
        *,
        http_client: httpx.Client | None = None,
    ):
        self.settings = settings or GrowthEngineSettings()
        secret_key = self.settings.model_api_key.get_secret_value().strip() if self.settings.model_api_key else ""
        self._secret_key = secret_key
        self._client = OpenAI(
            api_key=secret_key or "no-key",
            base_url=self.settings.meta_base_url,
            timeout=self.settings.model_timeout_seconds,
            max_retries=0,
            http_client=http_client,
        )

    def generate_content_items(
        self,
        kit_id: UUID,
        grounding: PropertyGrounding,
        brief: dict[str, Any] | None = None,
        channels: list[str] | None = None,
        creative_asset_id: UUID | None = None,
    ) -> list[ContentItemResponse]:
        """Generate platform-specific copy variants grounded in CRM property facts."""
        target_channels = channels or ["instagram_feed", "instagram_story", "facebook_feed"]
        items: list[ContentItemResponse] = []

        choices = {}
        if self._secret_key:
            try:
                response = self._client.chat.completions.create(
                    model=self.settings.muse_spark_model,
                    messages=[{
                        "role": "user",
                        "content": (
                            f"Choose a template per channel for this owner brief: {json.dumps(brief or {}, default=str)}. "
                            f"Channels: {target_channels}. Template IDs: {list(COPY_TEMPLATES)}. "
                            "Return JSON mapping each channel to one template ID."
                        ),
                    }],
                )
                output = json.loads(response.choices[0].message.content or "{}")
                if isinstance(output, dict):
                    choices = output
            except (ValueError, AttributeError, IndexError, OpenAIError):
                pass

        defaults = {"instagram_story": "compact_story", "facebook_feed": "detailed_showcase", "festival_offer": "festive_spotlight"}
        for channel in dict.fromkeys(target_channels):
            choice = choices.get(channel)
            model_choice = isinstance(choice, str) and choice in COPY_TEMPLATES
            template = choice if model_choice else defaults.get(channel, "standard_residence")
            structured = make_structured_copy(channel, template, grounding)
            headline, body, cta, hashtags = render_structured_copy(structured, grounding)
            now = datetime.now(timezone.utc)
            items.append(ContentItemResponse(
                id=uuid4(), kit_id=kit_id, channel=channel, headline=headline, body=body,
                call_to_action=cta, hashtags=hashtags, creative_asset_id=creative_asset_id,
                grounding_verified=True, verification_notes="Rendered from current CRM references.",
                fact_references=structured.fact_references, structured_copy=structured,
                source="muse_spark" if model_choice else "deterministic_template",
                claim_checks={"structured_copy": structured.model_dump(mode="json")},
                created_at=now, updated_at=now,
            ))
        return items

    def generate_campaign_plan(
        self,
        kit_id: UUID,
        grounding: PropertyGrounding,
        request: CampaignPlanRequest,
        brief: dict[str, Any] | None = None,
    ) -> CampaignPlanResponse:
        """Generate audience targeting, placement recommendations, and budget guidance.
        Strictly sets launch_authorized to False in all staged payloads.
        """
        daily_budget = request.suggested_daily_budget or Decimal("1500.00")
        daily_budget = max(Decimal(str(self.settings.min_daily_budget_inr)), min(daily_budget, Decimal(str(self.settings.max_daily_budget_inr))))

        duration = request.suggested_duration_days or 14
        duration = max(self.settings.min_campaign_duration_days, min(duration, self.settings.max_campaign_duration_days))

        return CampaignPlanResponse(
            id=uuid4(), kit_id=kit_id, property_id=grounding.property_id,
            suggested_daily_budget=daily_budget, suggested_duration_days=duration,
            source="deterministic_template",
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
            **campaign_fields(grounding, daily_budget, duration),
        )


def campaign_fields(grounding, daily_budget, duration):
    audience_spec = {
        "geographic_targeting": {
            "city": "Bengaluru",
            "center_locality": grounding.locality,
            "radius_km": 10,
        },
        "demographics": {
            "age_range": [28, 55],
            "income_band": "Upper-middle to High Net Worth (INR 25L+ p.a.)",
        },
        "interests": [
            "Real estate investing",
            "Residential apartments",
            "Home improvement",
            "Tech professionals in Bengaluru",
        ],
        "behavioral_signals": [
            "Recently moved to Bengaluru",
            "Actively researching residential properties in Bengaluru",
        ],
    }

    placements = [
        "Instagram Feed",
        "Instagram Stories & Reels",
        "Facebook Feed",
        "Meta Audience Network (Mobile Apps)",
    ]

    rationale = (
        f"For a {grounding.bedrooms} BHK property in {grounding.locality} priced at INR {grounding.price_inr.normalize():,f}, "
        f"a {duration}-day sprint with INR {float(daily_budget):,.0f}/day balances reach and cost-per-qualified-lead. "
        f"Visual story placements highlight architectural segmentation features, while feed placements drive high-intent visit bookings."
    )

    staged_payload = {
        "status": "staged_draft",
        "launch_authorized": False,
        "notice": "Draft only. Live ad spend and publication requires future authorized connection and owner launch approval.",
        "property_id": str(grounding.property_id),
        "estimated_impressions": int(float(daily_budget) * duration * 18),
        "estimated_clicks": int(float(daily_budget) * duration * 0.45),
        "suggested_daily_budget_inr": float(daily_budget),
        "suggested_duration_days": duration,
    }

    return {
        "objective": "Lead Generation & Private Visit Bookings",
        "target_audience_spec": audience_spec,
        "recommended_placements": placements,
        "rationale": rationale,
        "staged_launch_payload": staged_payload,
    }
