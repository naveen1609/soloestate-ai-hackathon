"""FastAPI API Router for SoloEstate AI Growth Engine-lite.
Protected by require_owner session authentication.
Provides dedicated marketing endpoints without modifying core CRM or D3 routers.
Enforces durable persistence and strict fail-closed CRM grounding at all boundaries.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.crm import CRM, CRMError
from app.owner_api import require_owner
from app.growth_engine.config import GrowthEngineSettings
from app.growth_engine.fixtures import DEMO_PROPERTY_IMAGES, DEMO_VOICE_BRIEFS
from app.growth_engine.schemas import (
    CampaignPlanDecision,
    CampaignPlanRequest,
    CampaignPlanResponse,
    ContentItemEditDecision,
    ContentItemGenerateRequest,
    ContentItemResponse,
    CreativeAssetDecision,
    CreativeAssetResponse,
    CreativeGenerateRequest,
    KitExportPackage,
    KitSummaryResponse,
    MarketingBriefCreate,
    MarketingBriefResponse,
    OrchestrateKitRequest,
    OrchestrateKitResponse,
    PropertyImageItem,
    SAMSegmentRequest,
    SAMSegmentResponse,
    VoiceTranscribeRequest,
    VoiceTranscribeResponse,
)
from app.growth_engine.services.approval_service import (
    ApprovalServiceError,
    GrowthApprovalService,
)
from app.growth_engine.services.creative_service import (
    CreativeServiceError,
    MetaMuseImageService,
)
from app.growth_engine.services.grounding_service import (
    GroundingService,
    GroundingValidationError,
    PropertyInactiveError,
    PropertyNotFoundError,
    StaleGroundingError,
)
from app.growth_engine.services.marketing_intelligence_service import (
    MarketingIntelligenceError,
    MetaMuseSparkService,
)
from app.growth_engine.services.orchestrator_service import (
    LocalPipelineOrchestrator,
    MetaMuseWonderOrchestrator,
    OrchestratorServiceError,
)
from app.growth_engine.services.visual_intelligence_service import (
    MetaSAMService,
    VisualIntelligenceError,
)
from app.growth_engine.services.voice_service import (
    MetaMuseVoiceService,
    VoiceServiceError,
)
from app.growth_engine.store import GrowthEngineStore, GrowthStoreError

router = APIRouter(prefix="/owner/growth-engine", dependencies=[Depends(require_owner)])


def get_crm() -> CRM:
    return CRM()


def get_settings() -> GrowthEngineSettings:
    return GrowthEngineSettings()


def get_store() -> GrowthEngineStore:
    return GrowthEngineStore()


def get_grounding_service() -> GroundingService:
    return GroundingService()


def get_orchestrator() -> LocalPipelineOrchestrator:
    return LocalPipelineOrchestrator()


def get_approval_service() -> GrowthApprovalService:
    return GrowthApprovalService()


# --- Properties & Visual Assets ---

@router.get("/properties")
def list_growth_properties(
    crm: CRM = Depends(get_crm),
    store: GrowthEngineStore = Depends(get_store),
):
    """List available properties with authentic photography count and grounding status.
    Strictly reads from CRM and does NOT invent fictional properties if unavailable.
    """
    try:
        properties_list = crm.listing("properties", page=1, page_size=50)
        items = properties_list.get("items", [])
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Unable to retrieve CRM properties: {exc}",
        )

    results = []
    for prop in items:
        pid = UUID(str(prop["id"]))
        images = store.get_property_images(pid)
        results.append({
            "id": str(pid),
            "title": prop.get("title", ""),
            "locality": prop.get("locality", ""),
            "price": str(prop.get("price", "0")),
            "bedrooms": int(prop.get("bedrooms", 1)),
            "description": prop.get("description", ""),
            "active": prop.get("active", True),
            "authentic_images_count": len(images),
            "images": [img.model_dump(mode="json") for img in images],
        })

    return {"status": "ok", "properties": results, "demo_voice_briefs": DEMO_VOICE_BRIEFS}


@router.get("/properties/{property_id}/images")
def get_property_images(
    property_id: UUID,
    store: GrowthEngineStore = Depends(get_store),
    grounding_service: GroundingService = Depends(get_grounding_service),
):
    """Fetch authentic property photos for a selected property.
    Validates that property exists and is active in CRM.
    """
    try:
        grounding_service.validate_crm_property(property_id)
    except PropertyNotFoundError:
        raise HTTPException(status_code=404, detail=f"Property {property_id} not found in CRM.")
    except PropertyInactiveError:
        raise HTTPException(status_code=422, detail=f"Property {property_id} is inactive in CRM.")

    images = store.get_property_images(property_id)
    return {"status": "ok", "property_id": str(property_id), "images": [img.model_dump(mode="json") for img in images]}


# --- Model Operations ---

@router.post("/transcribe-voice", response_model=VoiceTranscribeResponse)
def transcribe_voice(
    request: VoiceTranscribeRequest,
    settings: GrowthEngineSettings = Depends(get_settings),
):
    """Transcribe spoken audio via Meta Muse Voice Transcribe."""
    service = MetaMuseVoiceService(settings)
    try:
        return service.transcribe_audio(request)
    except VoiceServiceError as exc:
        if exc.code == "audio_payload_too_large":
            raise HTTPException(status_code=413, detail=str(exc))
        if exc.code == "unsupported_audio_format":
            raise HTTPException(status_code=415, detail=str(exc))
        if exc.code == "missing_credentials":
            raise HTTPException(status_code=503, detail=str(exc))
        if exc.code == "provider_timeout":
            raise HTTPException(status_code=504, detail=str(exc))
        if exc.code in {"provider_unreachable", "provider_request_failed"} or exc.code.startswith("provider_http_"):
            raise HTTPException(status_code=502, detail=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Voice transcription service error: {exc}")


@router.post("/visual-segment", response_model=SAMSegmentResponse)
def segment_visual_features(
    request: SAMSegmentRequest,
    store: GrowthEngineStore = Depends(get_store),
    grounding_service: GroundingService = Depends(get_grounding_service),
    settings: GrowthEngineSettings = Depends(get_settings),
):
    """Segment architectural features from authentic property photography using Meta SAM 3.1."""
    try:
        prop = grounding_service.validate_crm_property(request.property_id)
    except PropertyNotFoundError:
        raise HTTPException(status_code=404, detail=f"Property {request.property_id} not found in CRM.")
    except PropertyInactiveError:
        raise HTTPException(status_code=422, detail=f"Property {request.property_id} is inactive in CRM.")

    images = store.get_property_images(request.property_id)
    target_img = next((img for img in images if img.id == request.image_id), None)
    if not target_img:
        raise HTTPException(status_code=404, detail=f"Source image {request.image_id} not found for property.")

    service = MetaSAMService(settings)
    try:
        return service.segment_property_image(
            request,
            image_url=target_img.image_url,
            property_context={"locality": prop.get("locality"), "bedrooms": prop.get("bedrooms")},
        )
    except VisualIntelligenceError as exc:
        if exc.code == "missing_credentials":
            raise HTTPException(status_code=503, detail=str(exc))
        if exc.code == "provider_timeout":
            raise HTTPException(status_code=504, detail=str(exc))
        if exc.code in {"provider_unreachable", "provider_request_failed", "malformed_response"} or exc.code.startswith("provider_http_"):
            raise HTTPException(status_code=502, detail=str(exc))
        if exc.code in {"invalid_bounding_box", "invalid_mask"}:
            raise HTTPException(status_code=502, detail=f"Provider returned invalid geometry: {exc}")
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/generate-creative", response_model=CreativeAssetResponse)
def generate_creative_asset(
    request: CreativeGenerateRequest,
    store: GrowthEngineStore = Depends(get_store),
    grounding_service: GroundingService = Depends(get_grounding_service),
    settings: GrowthEngineSettings = Depends(get_settings),
):
    """Generate marketing creative concept using Meta Muse Image while preserving authentic photos."""
    kit = store.get_kit(request.kit_id)
    if not kit:
        raise HTTPException(
            status_code=404,
            detail="Kit not found. A valid existing kit is required for creative generation.",
        )

    # Derive property_id from persisted kit on server; never trust caller-supplied property_id to override
    if request.property_id and request.property_id != kit.property_id:
        raise HTTPException(
            status_code=422,
            detail="Caller property_id mismatch: supplied property_id does not match kit property_id.",
        )

    # Revalidate live CRM facts
    try:
        grounding_service.validate_crm_property(kit.property_id, expected_hash=kit.grounding.grounding_hash)
    except PropertyNotFoundError:
        raise HTTPException(status_code=404, detail=f"Property {kit.property_id} not found in CRM.")
    except PropertyInactiveError:
        raise HTTPException(status_code=422, detail=f"Property {kit.property_id} is inactive in CRM.")
    except StaleGroundingError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # Verify source_image_id belongs to kit.property_id
    if request.source_image_id:
        images = store.get_property_images(kit.property_id)
        valid_img = next((img for img in images if img.id == request.source_image_id), None)
        if not valid_img:
            raise HTTPException(
                status_code=422,
                detail=f"Source image {request.source_image_id} does not belong to property {kit.property_id} or does not exist.",
            )

    service = MetaMuseImageService(settings)
    try:
        asset = service.generate_creative_asset(request, kit.grounding)
    except CreativeServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Bind asset to kit property_id server-side
    asset.property_id = kit.property_id
    asset.kit_id = kit.id

    try:
        persisted_asset = store.save_creative_asset(kit.id, asset)
    except GrowthStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    return persisted_asset


@router.post("/generate-content", response_model=list[ContentItemResponse])
def generate_content_items(
    request: ContentItemGenerateRequest,
    store: GrowthEngineStore = Depends(get_store),
    grounding_service: GroundingService = Depends(get_grounding_service),
    settings: GrowthEngineSettings = Depends(get_settings),
):
    """Generate platform-specific copy variants (IG Feed, Stories, FB, Festivals) using Meta Muse Spark."""
    kit = store.get_kit(request.kit_id)
    if not kit:
        raise HTTPException(status_code=404, detail="Kit not found")

    # Revalidate live CRM facts
    try:
        grounding_service.validate_crm_property(kit.property_id, expected_hash=kit.grounding.grounding_hash)
    except PropertyNotFoundError:
        raise HTTPException(status_code=404, detail=f"Property {kit.property_id} not found in CRM.")
    except PropertyInactiveError:
        raise HTTPException(status_code=422, detail=f"Property {kit.property_id} is inactive in CRM.")
    except StaleGroundingError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    service = MetaMuseSparkService(settings)
    try:
        items = service.generate_content_items(
            kit_id=kit.id,
            grounding=kit.grounding,
            channels=request.channels,
        )
    except MarketingIntelligenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Atomically supersede old channel items and insert replacements in ONE transaction
    try:
        persisted_items = store.replace_content_items_for_channels(kit.id, [c for c in request.channels], items)
    except GrowthStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    return persisted_items


@router.post("/generate-plan", response_model=CampaignPlanResponse)
def generate_campaign_plan(
    request: CampaignPlanRequest,
    store: GrowthEngineStore = Depends(get_store),
    grounding_service: GroundingService = Depends(get_grounding_service),
    settings: GrowthEngineSettings = Depends(get_settings),
):
    """Generate campaign audience, placements, and budget recommendation using Meta Muse Spark."""
    kit = store.get_kit(request.kit_id)
    if not kit:
        raise HTTPException(status_code=404, detail="Kit not found")

    try:
        grounding_service.validate_crm_property(kit.property_id, expected_hash=kit.grounding.grounding_hash)
    except PropertyNotFoundError:
        raise HTTPException(status_code=404, detail=f"Property {kit.property_id} not found in CRM.")
    except PropertyInactiveError:
        raise HTTPException(status_code=422, detail=f"Property {kit.property_id} is inactive in CRM.")
    except StaleGroundingError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    service = MetaMuseSparkService(settings)
    try:
        plan = service.generate_campaign_plan(
            kit_id=kit.id,
            grounding=kit.grounding,
            request=request,
        )
    except MarketingIntelligenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        persisted_plan = store.replace_campaign_plan(kit.id, plan)
    except GrowthStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    return persisted_plan


# --- Multi-Model Pipeline (Local Deterministic Orchestration) ---

@router.post("/orchestrate-kit", response_model=OrchestrateKitResponse)
def orchestrate_marketing_kit(
    request: OrchestrateKitRequest,
    store: GrowthEngineStore = Depends(get_store),
    orchestrator: LocalPipelineOrchestrator = Depends(get_orchestrator),
    grounding_service: GroundingService = Depends(get_grounding_service),
):
    """Run the complete end-to-end marketing workflow orchestrated locally.
    Validates CRM property strictly and persists durably before returning success.
    """
    try:
        prop_record = grounding_service.validate_crm_property(request.property_id)
    except PropertyNotFoundError:
        raise HTTPException(status_code=404, detail=f"Property {request.property_id} not found in CRM.")
    except PropertyInactiveError:
        raise HTTPException(status_code=422, detail=f"Property {request.property_id} is inactive in CRM.")

    raw_images = [img.model_dump(mode="json") for img in store.get_property_images(request.property_id)]
    try:
        response = orchestrator.orchestrate_marketing_kit(
            request=request,
            property_record=prop_record,
            property_images=raw_images,
        )
    except OrchestratorServiceError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        store.save_kit(response.kit, brief_data=request.brief)
    except GrowthStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    return response


# --- Owner Approval Workspace ---

@router.get("/kits")
def list_kits(
    property_id: UUID | None = None,
    store: GrowthEngineStore = Depends(get_store),
):
    """List generated marketing kits from durable storage."""
    try:
        kits = store.list_kits(property_id)
        return {"status": "ok", "kits": [k.model_dump(mode="json") for k in kits]}
    except GrowthStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/kits/{kit_id}", response_model=KitSummaryResponse)
def get_kit_detail(kit_id: UUID, store: GrowthEngineStore = Depends(get_store)):
    """Retrieve full details of a marketing kit from durable storage."""
    try:
        kit = store.get_kit(kit_id)
    except GrowthStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    if not kit:
        raise HTTPException(status_code=404, detail="Kit not found")
    return kit


@router.patch("/kits/{kit_id}/content-items/{item_id}/decision", response_model=ContentItemResponse)
def decide_content_item(
    kit_id: UUID,
    item_id: UUID,
    decision: ContentItemEditDecision,
    store: GrowthEngineStore = Depends(get_store),
    approval_service: GrowthApprovalService = Depends(get_approval_service),
    grounding_service: GroundingService = Depends(get_grounding_service),
):
    """Owner review, in-place edit, and approve/reject a content item draft.
    Requires expected_version check, revalidates live CRM facts, and commits atomically without aggregate upsert.
    """
    try:
        kit = store.get_kit(kit_id)
    except GrowthStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    if not kit:
        raise HTTPException(status_code=404, detail="Kit not found")

    target_item = next((item for item in kit.content_items if item.id == item_id), None)
    if target_item is None:
        raise HTTPException(status_code=404, detail="Content item not found in kit")

    try:
        crm_property = grounding_service.validate_crm_property(kit.property_id)
    except PropertyNotFoundError:
        raise HTTPException(status_code=404, detail=f"Property {kit.property_id} no longer exists in CRM")
    except PropertyInactiveError:
        raise HTTPException(status_code=422, detail=f"Property {kit.property_id} is inactive in CRM")
    except StaleGroundingError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    try:
        updated = approval_service.decide_content_item(
            target_item,
            decision,
            kit.grounding,
            current_property_record=crm_property,
            expected_hash=kit.grounding.grounding_hash,
        )
    except ApprovalServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    # ATOMIC COMPARE-AND-UPDATE in database
    try:
        persisted = store.update_content_item_decision(
            kit_id=kit_id,
            item_id=item_id,
            expected_version=decision.expected_version,
            status=decision.status,
            headline=updated.headline,
            body=updated.body,
            call_to_action=updated.call_to_action,
            hashtags=updated.hashtags,
            approval_notes=updated.approval_notes,
            rejection_reason=decision.approval_notes if decision.status == "rejected" else "",
            source=updated.source,
            grounding_verified=updated.grounding_verified,
            verification_notes=updated.verification_notes,
            claim_checks=updated.claim_checks,
        )
        return persisted
    except GrowthStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.patch("/kits/{kit_id}/creative-assets/{asset_id}/decision", response_model=CreativeAssetResponse)
def decide_creative_asset(
    kit_id: UUID,
    asset_id: UUID,
    decision: CreativeAssetDecision,
    store: GrowthEngineStore = Depends(get_store),
    approval_service: GrowthApprovalService = Depends(get_approval_service),
    grounding_service: GroundingService = Depends(get_grounding_service),
):
    """Owner review and approve/reject a creative concept graphic atomically."""
    try:
        kit = store.get_kit(kit_id)
    except GrowthStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    if not kit:
        raise HTTPException(status_code=404, detail="Kit not found")

    target_asset = next((cr for cr in kit.creative_assets if cr.id == asset_id), None)
    if target_asset is None:
        raise HTTPException(status_code=404, detail="Creative asset not found in kit")

    try:
        crm_property = grounding_service.validate_crm_property(kit.property_id)
    except PropertyNotFoundError:
        raise HTTPException(status_code=404, detail=f"Property {kit.property_id} no longer exists in CRM")
    except PropertyInactiveError:
        raise HTTPException(status_code=422, detail=f"Property {kit.property_id} is inactive in CRM")

    try:
        approval_service.decide_creative_asset(
            target_asset,
            decision,
            current_property_record=crm_property,
            expected_hash=kit.grounding.grounding_hash,
        )
    except ApprovalServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    # ATOMIC COMPARE-AND-UPDATE in database
    try:
        persisted = store.update_creative_asset_decision(
            kit_id=kit_id,
            asset_id=asset_id,
            expected_version=decision.expected_version,
            status=decision.status,
            rejection_reason=decision.notes if decision.status == "rejected" else "",
        )
        return persisted
    except GrowthStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.patch("/kits/{kit_id}/campaign-plan/decision", response_model=CampaignPlanResponse)
def decide_campaign_plan(
    kit_id: UUID,
    decision: CampaignPlanDecision,
    store: GrowthEngineStore = Depends(get_store),
    approval_service: GrowthApprovalService = Depends(get_approval_service),
    grounding_service: GroundingService = Depends(get_grounding_service),
):
    """Owner review and approve/reject campaign plan atomically."""
    try:
        kit = store.get_kit(kit_id)
    except GrowthStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    if not kit or not kit.campaign_plan:
        raise HTTPException(status_code=404, detail="Kit or campaign plan not found")

    try:
        crm_property = grounding_service.validate_crm_property(kit.property_id)
    except PropertyNotFoundError:
        raise HTTPException(status_code=404, detail=f"Property {kit.property_id} no longer exists in CRM")
    except PropertyInactiveError:
        raise HTTPException(status_code=422, detail=f"Property {kit.property_id} is inactive in CRM")

    try:
        updated = approval_service.decide_campaign_plan(
            kit.campaign_plan,
            decision,
            current_property_record=crm_property,
            expected_hash=kit.grounding.grounding_hash,
        )
    except ApprovalServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    # ATOMIC COMPARE-AND-UPDATE in database
    try:
        persisted = store.update_campaign_plan_decision(
            kit_id=kit_id,
            plan_id=decision.plan_id,
            expected_version=decision.expected_version,
            status=decision.status,
            rejection_reason=decision.notes if decision.status == "rejected" else "",
            staged_launch_payload=updated.staged_launch_payload,
        )
        return persisted
    except GrowthStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/kits/{kit_id}/export", response_model=KitExportPackage)
def export_kit(
    kit_id: UUID,
    store: GrowthEngineStore = Depends(get_store),
    approval_service: GrowthApprovalService = Depends(get_approval_service),
    grounding_service: GroundingService = Depends(get_grounding_service),
):
    """Export approved marketing assets, copy, and plans.
    Independently verifies live CRM facts and claim validity at the export boundary.
    """
    try:
        kit = store.get_kit(kit_id)
    except GrowthStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    if not kit:
        raise HTTPException(status_code=404, detail="Kit not found")

    try:
        crm_property = grounding_service.validate_crm_property(kit.property_id)
    except PropertyNotFoundError:
        raise HTTPException(status_code=404, detail=f"Property {kit.property_id} no longer exists in CRM")
    except PropertyInactiveError:
        raise HTTPException(status_code=422, detail=f"Property {kit.property_id} is inactive in CRM")
    except StaleGroundingError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    try:
        return approval_service.export_kit_package(kit, current_property_record=crm_property, validate_live_crm=True)
    except ApprovalServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
