from decimal import Decimal
from uuid import UUID, uuid4
import httpx
import pytest

from app.growth_engine.config import GrowthEngineSettings
from app.growth_engine.schemas import (
    CampaignPlanRequest,
    MarketingBriefCreate,
    OrchestrateKitRequest,
    PropertyGrounding,
)
from app.growth_engine.services.grounding_service import GroundingService
from app.growth_engine.services.marketing_intelligence_service import MetaMuseSparkService
from app.growth_engine.services.orchestrator_service import MetaMuseWonderOrchestrator


def sample_property() -> dict:
    return {
        "id": str(uuid4()),
        "title": "Demo Cedar Apartment",
        "locality": "Indiranagar",
        "price": "10500000",
        "bedrooms": 2,
        "description": "2 BHK modern apartment in prime Indiranagar with modular kitchen and spacious balcony.",
        "active": True,
        "version": 1,
    }


def test_muse_spark_content_generation():
    service = MetaMuseSparkService(GrowthEngineSettings())
    prop = sample_property()
    grounding = GroundingService().snapshot_from_dict(prop)
    kit_id = uuid4()

    brief = {
        "tone": "luxury",
        "target_audience": "Tech founders in Bangalore",
        "key_selling_points": ["Balcony view", "Granite kitchen"],
        "special_offers": "Complimentary modular woodwork",
    }

    items = service.generate_content_items(
        kit_id=kit_id,
        grounding=grounding,
        brief=brief,
        channels=["instagram_feed", "instagram_story", "facebook_feed", "festival_offer"],
    )

    assert len(items) == 4
    for item in items:
        assert item.kit_id == kit_id
        assert item.status == "draft"
        assert item.grounding_verified is True
        # Ensure bedroom count and locality are present and correct in copy
        assert "2 BHK" in f"{item.headline} {item.body}" or "2" in f"{item.headline} {item.body}"
        assert "Indiranagar" in f"{item.headline} {item.body}"
        assert len(item.hashtags) > 0
        assert len(item.call_to_action) > 0


def test_campaign_planner():
    service = MetaMuseSparkService(GrowthEngineSettings())
    prop = sample_property()
    grounding = GroundingService().snapshot_from_dict(prop)
    kit_id = uuid4()

    req = CampaignPlanRequest(
        kit_id=kit_id,
        suggested_daily_budget=Decimal("2000.00"),
        suggested_duration_days=14,
    )
    plan = service.generate_campaign_plan(kit_id=kit_id, grounding=grounding, request=req)

    assert plan.kit_id == kit_id
    assert plan.status == "draft"
    assert plan.suggested_daily_budget == Decimal("2000.00")
    assert plan.suggested_duration_days == 14
    assert len(plan.recommended_placements) >= 3
    assert "geographic_targeting" in plan.target_audience_spec
    # Staged payload must clearly be labeled as draft and not live
    assert plan.staged_launch_payload.get("status") == "staged_draft"


def test_muse_wonder_orchestrator():
    orchestrator = MetaMuseWonderOrchestrator(GrowthEngineSettings())
    prop = sample_property()
    prop_id = UUID(prop["id"])

    brief = MarketingBriefCreate(
        property_id=prop_id,
        input_mode="voice",
        raw_input="Showcase the morning sunlight on the balcony and modern kitchen. Target working couples.",
        tone="warm",
        campaign_objective="lead_generation",
    )

    req = OrchestrateKitRequest(
        property_id=prop_id,
        brief=brief,
        channels=["instagram_feed", "facebook_feed"],
        generate_creative=True,
        generate_plan=True,
    )

    res = orchestrator.orchestrate_marketing_kit(request=req, property_record=prop)

    assert res.kit.property_id == prop_id
    assert res.kit.status == "draft"
    assert len(res.kit.content_items) == 2
    assert len(res.kit.creative_assets) == 1
    assert res.kit.campaign_plan is not None

    # Check execution trace
    step_names = [s.step_name for s in res.steps_executed]
    assert "Grounding Freeze" in step_names
    assert "Visual Intelligence Segmentation" in step_names
    assert "Creative Studio Generation" in step_names
    assert "Marketing Copy Studio" in step_names
    assert "Claim Grounding Verification Gate" in step_names
    assert "Campaign Strategy & Budget Planning" in step_names
