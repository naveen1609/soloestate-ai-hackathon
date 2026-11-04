import base64
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4
import httpx
import pytest
from fastapi import HTTPException

from app.growth_engine.config import GrowthEngineSettings
from app.growth_engine.fixtures import CEDAR_APARTMENT_ID
from app.growth_engine.router import (
    decide_campaign_plan,
    decide_content_item,
    decide_creative_asset,
    export_kit,
    generate_content_items,
    generate_creative_asset,
    get_kit_detail,
    get_property_images,
    list_growth_properties,
    orchestrate_marketing_kit,
    segment_visual_features,
    transcribe_voice,
)
from app.growth_engine.schemas import (
    CampaignPlanDecision,
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
    OrchestrateKitRequest,
    PropertyGrounding,
    PropertyImageItem,
    SAMSegmentRequest,
    VoiceTranscribeRequest,
)
from app.growth_engine.services.approval_service import GrowthApprovalService
from app.growth_engine.services.voice_service import MetaMuseVoiceService
from app.growth_engine.services.grounding_service import (
    GroundingService,
    PropertyInactiveError,
    PropertyNotFoundError,
    StaleGroundingError,
)
from app.growth_engine.services.orchestrator_service import MetaMuseWonderOrchestrator
from app.growth_engine.store import GrowthEngineStore, GrowthStoreError


class MockCRM:
    def __init__(self):
        self.properties = {
            str(CEDAR_APARTMENT_ID): {
                "id": str(CEDAR_APARTMENT_ID),
                "title": "Demo Cedar Apartment",
                "locality": "Indiranagar",
                "price": "10500000",
                "bedrooms": 2,
                "description": "2 BHK apartment with balcony in Indiranagar.",
                "active": True,
                "version": 1,
            }
        }

    def listing(self, entity: str, page: int = 1, page_size: int = 50):
        return {"items": list(self.properties.values()), "total": len(self.properties)}

    def detail(self, entity: str, item_id: UUID | str):
        sid = str(item_id)
        if sid not in self.properties:
            from app.crm import CRMError
            raise CRMError(f"Property {item_id} not found", 404)
        return self.properties[sid]


class MockStore(GrowthEngineStore):
    def __init__(self):
        self.kits: dict[UUID, KitSummaryResponse] = {}
        self.images: dict[UUID, list[PropertyImageItem]] = {
            CEDAR_APARTMENT_ID: [
                PropertyImageItem(
                    id=uuid4(),
                    property_id=CEDAR_APARTMENT_ID,
                    image_url="https://example.com/facade.jpg",
                    caption="Balcony view",
                    is_authentic=True,
                    features=["Eastern Balcony", "Modular Kitchen"],
                    created_at=datetime.now(timezone.utc),
                ),
                PropertyImageItem(
                    id=uuid4(),
                    property_id=CEDAR_APARTMENT_ID,
                    image_url="https://example.com/interior.jpg",
                    caption="Living room",
                    is_authentic=True,
                    features=["Living Flooring"],
                    created_at=datetime.now(timezone.utc),
                ),
            ]
        }

    def get_property_images(self, property_id: UUID, include_demo_fixtures: bool = False) -> list[PropertyImageItem]:
        return self.images.get(property_id, [])

    def save_kit(self, kit: KitSummaryResponse, brief_data: MarketingBriefCreate | None = None) -> KitSummaryResponse:
        self.kits[kit.id] = kit
        return kit

    def get_kit(self, kit_id: UUID) -> KitSummaryResponse | None:
        return self.kits.get(kit_id)

    def list_kits(self, property_id: UUID | None = None) -> list[KitSummaryResponse]:
        if property_id:
            return [k for k in self.kits.values() if k.property_id == property_id]
        return list(self.kits.values())

    def update_content_item_decision(
        self,
        kit_id: UUID,
        item_id: UUID,
        expected_version: int,
        status: str,
        headline: str | None = None,
        body: str | None = None,
        call_to_action: str | None = None,
        hashtags: list[str] | None = None,
        approval_notes: str = "",
        rejection_reason: str = "",
        source: str = "owner_edit",
        grounding_verified: bool = True,
        verification_notes: str = "",
        claim_checks: dict[str, Any] | None = None,
    ) -> ContentItemResponse:
        kit = self.kits.get(kit_id)
        if not kit:
            raise GrowthStoreError("kit_not_found", "Kit not found", 404)
        for item in kit.content_items:
            if item.id == item_id:
                if item.version != expected_version:
                    raise GrowthStoreError("version_conflict", f"Version conflict: current {item.version} != expected {expected_version}", 409)
                item.status = status
                if headline is not None:
                    item.headline = headline
                if body is not None:
                    item.body = body
                if call_to_action is not None:
                    item.call_to_action = call_to_action
                if hashtags is not None:
                    item.hashtags = hashtags
                item.approval_notes = approval_notes
                item.rejection_reason = rejection_reason
                item.source = source
                item.grounding_verified = grounding_verified
                item.verification_notes = verification_notes
                if claim_checks is not None:
                    from app.growth_engine.store import copy_metadata
                    item.claim_checks = claim_checks
                    metadata = copy_metadata(claim_checks)
                    item.structured_copy = metadata.get("structured_copy")
                    item.fact_references = metadata.get("fact_references", [])
                item.version += 1
                item.updated_at = datetime.now(timezone.utc)
                if status == "approved":
                    item.approved_at = datetime.now(timezone.utc)
                return item
        raise GrowthStoreError("content_item_not_found", "Item not found", 404)

    def update_creative_asset_decision(
        self,
        kit_id: UUID,
        asset_id: UUID,
        expected_version: int,
        status: str,
        rejection_reason: str = "",
    ) -> CreativeAssetResponse:
        kit = self.kits.get(kit_id)
        if not kit:
            raise GrowthStoreError("kit_not_found", "Kit not found", 404)
        for cr in kit.creative_assets:
            if cr.id == asset_id:
                if cr.version != expected_version:
                    raise GrowthStoreError("version_conflict", f"Version conflict: current {cr.version} != expected {expected_version}", 409)
                cr.status = status
                cr.rejection_reason = rejection_reason
                cr.version += 1
                return cr
        raise GrowthStoreError("creative_asset_not_found", "Asset not found", 404)

    def update_campaign_plan_decision(
        self,
        kit_id: UUID,
        plan_id: UUID,
        expected_version: int,
        status: str,
        rejection_reason: str = "",
        staged_launch_payload: dict[str, Any] | None = None,
    ) -> CampaignPlanResponse:
        kit = self.kits.get(kit_id)
        if not kit or not kit.campaign_plan:
            raise GrowthStoreError("campaign_plan_not_found", "Plan not found", 404)
        plan = kit.campaign_plan
        if plan.id != plan_id:
            raise GrowthStoreError("plan_id_mismatch", "Plan ID mismatch", 409)
        if plan.version != expected_version:
            raise GrowthStoreError("version_conflict", f"Version conflict: current {plan.version} != expected {expected_version}", 409)
        plan.status = status
        plan.rejection_reason = rejection_reason
        if staged_launch_payload:
            plan.staged_launch_payload = staged_launch_payload
        plan.version += 1
        plan.updated_at = datetime.now(timezone.utc)
        if status == "approved":
            plan.approved_at = datetime.now(timezone.utc)
        return plan

    def save_creative_asset(self, kit_id: UUID, asset: CreativeAssetResponse) -> CreativeAssetResponse:
        kit = self.kits.get(kit_id)
        if not kit:
            raise GrowthStoreError("kit_not_found", "Kit not found", 404)
        kit.creative_assets.append(asset)
        return asset

    def replace_content_items_for_channels(
        self,
        kit_id: UUID,
        channels: list[str],
        items: list[ContentItemResponse],
    ) -> list[ContentItemResponse]:
        kit = self.kits.get(kit_id)
        if not kit:
            raise GrowthStoreError("kit_not_found", "Kit not found", 404)
        for item in kit.content_items:
            if item.channel in channels and item.status != "superseded":
                item.status = "superseded"
        kit.content_items.extend(items)
        return items

    def replace_campaign_plan(self, kit_id: UUID, plan: CampaignPlanResponse) -> CampaignPlanResponse:
        kit = self.kits.get(kit_id)
        if not kit:
            raise GrowthStoreError("kit_not_found", "Kit not found", 404)
        if kit.campaign_plan:
            kit.campaign_plan.status = "superseded"
        kit.campaign_plan = plan
        return plan

    def supersede_content_for_channels(self, kit_id: UUID, channels: list[str]) -> None:
        kit = self.kits.get(kit_id)
        if kit:
            for item in kit.content_items:
                if item.channel in channels and item.status != "superseded":
                    item.status = "superseded"


@pytest.fixture
def mock_crm():
    return MockCRM()


@pytest.fixture
def mock_store():
    return MockStore()


@pytest.fixture
def grounding_service(mock_crm):
    return GroundingService(crm=mock_crm)


@pytest.fixture
def settings():
    return GrowthEngineSettings()


def test_list_growth_properties_endpoint(mock_crm, mock_store):
    res = list_growth_properties(crm=mock_crm, store=mock_store)
    assert res["status"] == "ok"
    assert len(res["properties"]) == 1
    assert res["properties"][0]["title"] == "Demo Cedar Apartment"


def test_missing_property_images_returns_404(mock_store, grounding_service):
    missing_id = uuid4()
    with pytest.raises(HTTPException) as exc_info:
        get_property_images(property_id=missing_id, store=mock_store, grounding_service=grounding_service)
    assert exc_info.value.status_code == 404


def test_valid_property_images_returns_images(mock_store, grounding_service):
    res = get_property_images(property_id=CEDAR_APARTMENT_ID, store=mock_store, grounding_service=grounding_service)
    assert res["status"] == "ok"
    assert len(res["images"]) == 2
    assert res["images"][0]["is_authentic"] is True


def test_voice_transcribe_endpoint(settings):
    # Case A: raw_text without audio is rejected from voice transcription
    req_text = VoiceTranscribeRequest(
        property_id=CEDAR_APARTMENT_ID,
        raw_text="Spacious 2 BHK with sunlit eastern balcony in Indiranagar.",
    )
    with pytest.raises(HTTPException) as exc_info:
        transcribe_voice(request=req_text, settings=settings)
    assert exc_info.value.status_code == 400
    assert "Raw text cannot be transcribed as voice" in str(exc_info.value.detail)

    # Case B: valid WAV audio transcribes via Meta Voice
    valid_wav_header = b"RIFF,\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x08\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b64_wav = base64.b64encode(valid_wav_header).decode("utf-8")

    def mock_transcribe_handler(request: httpx.Request):
        assert "/asr/transcribe" in str(request.url)
        return httpx.Response(200, json={"transcript": "Spacious 2 BHK with sunlit eastern balcony in Indiranagar.", "confidence": 0.95})

    client = httpx.Client(transport=httpx.MockTransport(mock_transcribe_handler))
    service = MetaMuseVoiceService(GrowthEngineSettings(model_api_key="mock-key"), http_client=client)
    req_audio = VoiceTranscribeRequest(
        property_id=CEDAR_APARTMENT_ID,
        audio_base64=b64_wav,
        audio_format="wav",
    )
    res = service.transcribe_audio(req_audio)
    assert "balcony" in res.transcript.lower()
    assert res.model_id == "muse-voice-transcribe-1.0"


def test_orchestrate_and_approval_workflow(mock_store, grounding_service, settings):
    approval_service = GrowthApprovalService()
    orchestrator = MetaMuseWonderOrchestrator(settings=settings)

    # 1. Orchestrate kit
    orch_req = OrchestrateKitRequest(
        property_id=CEDAR_APARTMENT_ID,
        brief=MarketingBriefCreate(
            property_id=CEDAR_APARTMENT_ID,
            input_mode="text",
            raw_input="2 BHK home in Indiranagar with modular kitchen and spacious balcony.",
            target_audience="Tech professionals in Bengaluru",
            key_selling_points=["Prime Indiranagar location", "Spacious layout"],
            tone="professional",
            campaign_objective="lead_generation",
            special_offers="",
        ),
        channels=["instagram_feed", "facebook_feed"],
        generate_creative=True,
        generate_plan=True,
    )
    orch_res = orchestrate_marketing_kit(
        request=orch_req,
        store=mock_store,
        orchestrator=orchestrator,
        grounding_service=grounding_service,
    )
    kit = orch_res.kit
    kit_id = kit.id
    assert kit_id is not None
    assert len(kit.content_items) == 2
    assert len(kit.creative_assets) == 1
    assert kit.campaign_plan is not None

    # 2. Retrieve kit from durable storage
    fetched_kit = get_kit_detail(kit_id=kit_id, store=mock_store)
    assert fetched_kit.id == kit_id

    # 3. Content item decision - Approve with valid content
    content_item = kit.content_items[0]
    item_id = content_item.id

    approved_item = decide_content_item(
        kit_id=kit_id,
        item_id=item_id,
        decision=ContentItemEditDecision(
            status="approved",
            expected_version=content_item.version,
            approval_notes="Approved valid headline",
        ),
        store=mock_store,
        approval_service=approval_service,
        grounding_service=grounding_service,
    )
    assert approved_item.status == "approved"
    assert approved_item.version == 2

    # 4. Version Conflict - submitting stale version returns 409
    with pytest.raises(HTTPException) as exc_409:
        decide_content_item(
            kit_id=kit_id,
            item_id=item_id,
            decision=ContentItemEditDecision(
                status="approved",
                expected_version=1,  # stale version!
            ),
            store=mock_store,
            approval_service=approval_service,
            grounding_service=grounding_service,
        )
    assert exc_409.value.status_code == 409

    # 5. Grounding Discrepancy - editing to claim 4 BHK and helipad returns 422
    with pytest.raises(HTTPException) as exc_422:
        decide_content_item(
            kit_id=kit_id,
            item_id=item_id,
            decision=ContentItemEditDecision(
                status="approved",
                expected_version=2,
                headline="Massive 4 BHK Villa with Private Helipad",
            ),
            store=mock_store,
            approval_service=approval_service,
            grounding_service=grounding_service,
        )
    assert exc_422.value.status_code == 422

    # 6. Approve creative concept
    creative = kit.creative_assets[0]
    approved_cr = decide_creative_asset(
        kit_id=kit_id,
        asset_id=creative.id,
        decision=CreativeAssetDecision(status="approved", expected_version=creative.version),
        store=mock_store,
        approval_service=approval_service,
        grounding_service=grounding_service,
    )
    assert approved_cr.status == "approved"
    assert approved_cr.is_authentic is False

    # 7. Approve campaign plan
    approved_plan = decide_campaign_plan(
        kit_id=kit_id,
        decision=CampaignPlanDecision(
            plan_id=kit.campaign_plan.id,
            status="approved",
            expected_version=kit.campaign_plan.version,
        ),
        store=mock_store,
        approval_service=approval_service,
        grounding_service=grounding_service,
    )
    assert approved_plan.status == "approved"
    assert approved_plan.staged_launch_payload["launch_authorized"] is False

    # 8. Export package
    exported_pkg = export_kit(
        kit_id=kit_id,
        store=mock_store,
        approval_service=approval_service,
        grounding_service=grounding_service,
    )
    assert exported_pkg.property_title == "Demo Cedar Apartment"
    assert len(exported_pkg.approved_content) >= 1
    assert len(exported_pkg.approved_creatives) >= 1
    assert exported_pkg.campaign_plan["staged_launch_payload"]["launch_authorized"] is False


def test_creative_generation_missing_kit_returns_404(mock_store, grounding_service, settings):
    """Codex 2: Reject creative generation for a missing or invalid kit."""
    fake_kit_id = uuid4()
    req = CreativeGenerateRequest(
        kit_id=fake_kit_id,
        property_id=CEDAR_APARTMENT_ID,
        creative_prompt="Showcase modern living room",
    )
    with pytest.raises(HTTPException) as exc_info:
        generate_creative_asset(
            request=req,
            store=mock_store,
            grounding_service=grounding_service,
            settings=settings,
        )
    assert exc_info.value.status_code == 404
    assert "Kit not found" in exc_info.value.detail


def test_creative_generation_cross_property_mismatch_returns_422(mock_store, grounding_service, settings):
    """Codex 2: Never trust caller-supplied property_id to override kit ownership."""
    # First create a valid kit for CEDAR_APARTMENT_ID
    orch_req = OrchestrateKitRequest(
        property_id=CEDAR_APARTMENT_ID,
        brief=MarketingBriefCreate(
            property_id=CEDAR_APARTMENT_ID,
            raw_input="2 BHK apartment",
        ),
        channels=["instagram_feed"],
        generate_creative=False,
        generate_plan=False,
    )
    res = orchestrate_marketing_kit(
        request=orch_req,
        store=mock_store,
        orchestrator=MetaMuseWonderOrchestrator(settings=settings),
        grounding_service=grounding_service,
    )
    kit_id = res.kit.id

    # Now attempt creative generation passing a DIFFERENT caller property_id
    foreign_prop_id = uuid4()
    req = CreativeGenerateRequest(
        kit_id=kit_id,
        property_id=foreign_prop_id,  # mismatch!
        creative_prompt="Modern layout",
    )
    with pytest.raises(HTTPException) as exc_info:
        generate_creative_asset(
            request=req,
            store=mock_store,
            grounding_service=grounding_service,
            settings=settings,
        )
    assert exc_info.value.status_code == 422
    assert "property_id mismatch" in exc_info.value.detail.lower()


def test_creative_generation_cross_property_photo_returns_422(mock_store, grounding_service, settings):
    """Codex 2: Reject source_image_id that belongs to another property or is nonexistent."""
    orch_req = OrchestrateKitRequest(
        property_id=CEDAR_APARTMENT_ID,
        brief=MarketingBriefCreate(
            property_id=CEDAR_APARTMENT_ID,
            raw_input="2 BHK apartment",
        ),
        channels=["instagram_feed"],
        generate_creative=False,
        generate_plan=False,
    )
    res = orchestrate_marketing_kit(
        request=orch_req,
        store=mock_store,
        orchestrator=MetaMuseWonderOrchestrator(settings=settings),
        grounding_service=grounding_service,
    )
    kit_id = res.kit.id

    foreign_image_id = uuid4()
    req = CreativeGenerateRequest(
        kit_id=kit_id,
        property_id=CEDAR_APARTMENT_ID,
        source_image_id=foreign_image_id,  # image does not exist on property
        creative_prompt="Modern layout",
    )
    with pytest.raises(HTTPException) as exc_info:
        generate_creative_asset(
            request=req,
            store=mock_store,
            grounding_service=grounding_service,
            settings=settings,
        )
    assert exc_info.value.status_code == 422
    assert "does not belong to property" in exc_info.value.detail


def test_content_regeneration_supersedes_prior_revisions(mock_store, grounding_service, settings):
    """Codex 2: When regenerating content for channels, prior revisions must be marked superseded and excluded from export."""
    approval_service = GrowthApprovalService()

    # 1. Orchestrate initial kit
    orch_req = OrchestrateKitRequest(
        property_id=CEDAR_APARTMENT_ID,
        brief=MarketingBriefCreate(
            property_id=CEDAR_APARTMENT_ID,
            raw_input="2 BHK apartment in Indiranagar",
        ),
        channels=["instagram_feed"],
        generate_creative=False,
        generate_plan=False,
    )
    res = orchestrate_marketing_kit(
        request=orch_req,
        store=mock_store,
        orchestrator=MetaMuseWonderOrchestrator(settings=settings),
        grounding_service=grounding_service,
    )
    kit_id = res.kit.id
    first_item = res.kit.content_items[0]

    # Approve the first item
    decide_content_item(
        kit_id=kit_id,
        item_id=first_item.id,
        decision=ContentItemEditDecision(status="approved", expected_version=first_item.version),
        store=mock_store,
        approval_service=approval_service,
        grounding_service=grounding_service,
    )

    # 2. Regenerate content for instagram_feed
    new_items = generate_content_items(
        request=ContentItemGenerateRequest(kit_id=kit_id, channels=["instagram_feed"]),
        store=mock_store,
        grounding_service=grounding_service,
        settings=settings,
    )
    assert len(new_items) == 1
    assert new_items[0].status == "draft"

    # Verify first item was superseded
    kit_after_regen = mock_store.get_kit(kit_id)
    old_item = next(i for i in kit_after_regen.content_items if i.id == first_item.id)
    assert old_item.status == "superseded"

    # Export kit: verify superseded items are excluded
    exported = export_kit(
        kit_id=kit_id,
        store=mock_store,
        approval_service=approval_service,
        grounding_service=grounding_service,
    )
    # The new item is still draft, and old item is superseded, so neither should be exported as approved!
    assert len(exported.approved_content) == 0


def test_voice_transcribe_audio_magic_bytes_validation(settings):
    """Codex 2: Uploaded audio must be validated against actual container magic bytes."""
    import base64

    # Case A: Valid RIFF WAVE container (RIFF header + WAVE tag)
    valid_wav_header = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
    b64_wav = base64.b64encode(valid_wav_header).decode("utf-8")
    req_wav = VoiceTranscribeRequest(
        property_id=CEDAR_APARTMENT_ID,
        audio_base64=b64_wav,
        audio_format="wav",
    )
    # When credentials are not configured, valid magic bytes pass to credential check
    settings_no_key = GrowthEngineSettings(model_api_key=None)
    with pytest.raises(HTTPException) as exc_wav:
        transcribe_voice(request=req_wav, settings=settings_no_key)
    assert exc_wav.value.status_code in {400, 503}
    assert "missing_credentials" in str(exc_wav.value.detail) or "MODEL_API_KEY" in str(exc_wav.value.detail)

    # Case B: Spoofed format: declared wav but bytes are ASCII text ("hello world 123456789")
    fake_bytes = b"hello world 123456789 not audio"
    b64_fake = base64.b64encode(fake_bytes).decode("utf-8")
    req_fake = VoiceTranscribeRequest(
        property_id=CEDAR_APARTMENT_ID,
        audio_base64=b64_fake,
        audio_format="wav",
    )
    with pytest.raises(HTTPException) as exc_fake:
        transcribe_voice(request=req_fake, settings=settings)
    assert exc_fake.value.status_code == 400
    assert "container header signatures" in str(exc_fake.value.detail) or "invalid_audio_magic_bytes" in str(exc_fake.value.detail)
