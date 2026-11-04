from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
import pytest

from app.growth_engine.schemas import (
    CampaignPlanDecision,
    CampaignPlanResponse,
    ContentItemEditDecision,
    ContentItemResponse,
    CreativeAssetDecision,
    CreativeAssetResponse,
    KitSummaryResponse,
    PropertyGrounding,
)
from app.growth_engine.services.approval_service import GrowthApprovalService


def sample_grounding() -> PropertyGrounding:
    return PropertyGrounding(
        property_id=uuid4(),
        title="Demo Cedar Apartment",
        locality="Indiranagar",
        price_inr=Decimal("10500000"),
        bedrooms=2,
        description="2 BHK apartment with balcony in Indiranagar.",
        active=True,
        grounding_version=1,
        grounding_hash="c" * 64,
    )


def test_approve_content_item_with_edit():
    service = GrowthApprovalService()
    grounding = sample_grounding()
    kit_id = uuid4()

    original = ContentItemResponse(
        id=uuid4(),
        kit_id=kit_id,
        channel="instagram_feed",
        headline="Old Headline",
        body="Original draft text for 2 BHK in Indiranagar.",
        call_to_action="DM for visit",
        hashtags=["#Indiranagar"],
        grounding_verified=True,
        verification_notes="",
        status="draft",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    preview = service.decide_content_item(
        original, ContentItemEditDecision(status="draft", expected_version=1, template_id="compact_story"), grounding
    )
    updated = service.decide_content_item(
        preview, ContentItemEditDecision(status="approved", expected_version=preview.version), grounding
    )
    assert updated.status == "approved"
    assert updated.headline == "Now listed: 2 BHK in Indiranagar"
    assert updated.approved_at is not None
    assert updated.grounding_verified is True


def test_approve_content_item_detects_edited_hallucination():
    service = GrowthApprovalService()
    grounding = sample_grounding()
    kit_id = uuid4()

    original = ContentItemResponse(
        id=uuid4(),
        kit_id=kit_id,
        channel="instagram_feed",
        headline="Valid Headline",
        body="Valid 2 BHK in Indiranagar.",
        call_to_action="DM for visit",
        hashtags=["#Indiranagar"],
        grounding_verified=True,
        verification_notes="",
        status="draft",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    # Owner inadvertently edits to claim 4 BHK and private helipad -> must fail closed with 422!
    bad_edit = ContentItemEditDecision(
        status="approved",
        expected_version=original.version,
        headline="Massive 4 BHK Villa with Private Helipad",
        body="Includes an Olympic swimming pool in Indiranagar.",
    )

    from app.growth_engine.services.approval_service import ApprovalServiceError
    with pytest.raises(ApprovalServiceError) as exc_info:
        service.decide_content_item(original, bad_edit, grounding)
    assert exc_info.value.code == "grounding_violation"
    assert exc_info.value.status_code == 422
    assert "server-rendered template" in str(exc_info.value)


def test_campaign_plan_approval_boundary():
    service = GrowthApprovalService()
    from app.growth_engine.schemas import CampaignPlanRequest
    from app.growth_engine.services.grounding_service import GroundingService
    from app.growth_engine.services.marketing_intelligence_service import MetaMuseSparkService

    record = dict(id=uuid4(), title="Cedar", locality="Indiranagar", price=10500000, bedrooms=2, active=True)
    grounding = GroundingService().snapshot_from_dict(record)
    kit_id = uuid4()
    plan = MetaMuseSparkService().generate_campaign_plan(kit_id, grounding, CampaignPlanRequest(kit_id=kit_id))
    approved_plan = service.decide_campaign_plan(
        plan, CampaignPlanDecision(plan_id=plan.id, status="approved", expected_version=plan.version),
        current_property_record=record, expected_hash=grounding.grounding_hash,
    )
    assert approved_plan.status == "approved"
    # Verify strict architectural boundary: content approval NEVER authorizes live spend
    assert approved_plan.staged_launch_payload["launch_authorized"] is False
    assert "No ad account spend" in approved_plan.staged_launch_payload["boundary_notice"]

    from app.growth_engine.services.approval_service import ApprovalServiceError

    plan.target_audience_spec["bedrooms"] = 5
    plan.target_audience_spec["price_inr"] = 9999
    with pytest.raises(ApprovalServiceError, match="Regenerate the campaign"):
        service.decide_campaign_plan(
            plan, CampaignPlanDecision(plan_id=plan.id, status="approved", expected_version=plan.version),
            current_property_record=record, expected_hash=grounding.grounding_hash,
        )



def test_export_package():
    service = GrowthApprovalService()
    grounding = sample_grounding()
    kit_id = uuid4()

    from app.growth_engine.services.marketing_intelligence_service import MetaMuseSparkService
    from app.growth_engine.config import GrowthEngineSettings

    approved_item = MetaMuseSparkService(GrowthEngineSettings(model_api_key=None)).generate_content_items(
        kit_id, grounding, channels=["instagram_feed"]
    )[0]
    approved_item = service.decide_content_item(
        approved_item, ContentItemEditDecision(status="approved", expected_version=1), grounding
    )

    approved_cr = CreativeAssetResponse(
        id=uuid4(),
        kit_id=kit_id,
        property_id=grounding.property_id,
        asset_type="concept_graphic",
        is_authentic=False,
        concept_watermark="AI Creative Concept",
        image_url="data:image/svg+xml;utf8,<svg></svg>",
        segmentation_data={"export_format": "labelled_concept_v1"},
        prompt_text="Concept",
        prompt_hash="1" * 64,
        model_id="muse-image",
        status="approved",
        created_at=datetime.now(timezone.utc),
    )

    kit = KitSummaryResponse(
        id=kit_id,
        property_id=grounding.property_id,
        brief_id=uuid4(),
        grounding=grounding,
        objective="lead_generation",
        status="approved",
        content_items=[approved_item],
        creative_assets=[approved_cr],
        campaign_plan=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    package = service.export_kit_package(kit)
    assert package.kit_id == kit_id
    assert package.property_title == "Demo Cedar Apartment"
    assert len(package.approved_content) == 1
    assert len(package.approved_creatives) == 1
    assert "Grounded in verified CRM" in package.safety_disclaimer


def test_provider_prose_and_legacy_copy_cannot_cross_approval():
    import json
    import httpx
    from app.growth_engine.config import GrowthEngineSettings
    from app.growth_engine.services.approval_service import ApprovalServiceError
    from app.growth_engine.services.marketing_intelligence_service import MetaMuseSparkService

    prose = "The residence has a wine cellar"
    payload = {"instagram_feed": {"headline": prose, "body": prose}}
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json={"choices": [{"message": {"content": json.dumps(payload)}}]},
    )))
    settings = GrowthEngineSettings(_env_file=None, model_api_key="fixture")
    grounding = sample_grounding()
    item = MetaMuseSparkService(settings, http_client=client).generate_content_items(
        uuid4(), grounding, channels=["instagram_feed"]
    )[0]
    assert prose not in item.body
    assert item.source == "deterministic_template"
    service = GrowthApprovalService()
    with pytest.raises(ApprovalServiceError):
        service.decide_content_item(
            item, ContentItemEditDecision(status="approved", expected_version=1, body=prose), grounding
        )
    legacy = item.model_copy(update={"structured_copy": None})
    with pytest.raises(ApprovalServiceError):
        service.decide_content_item(legacy, ContentItemEditDecision(status="approved", expected_version=1), grounding)
