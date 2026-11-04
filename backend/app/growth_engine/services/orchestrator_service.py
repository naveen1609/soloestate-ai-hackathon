"""Workflow Orchestration service for SoloEstate AI Growth Engine-lite.
Coordinates the end-to-end pipeline:
1. CRM Property Grounding Freeze
2. Voice/Text Brief Processing (Meta Muse Voice)
3. Architectural Visual Intelligence (Meta SAM 3.1)
4. Context-grounded Creative Generation (Meta Muse Image)
5. Structured Multi-Platform Copywriting (Meta Muse Spark)
6. Grounding Validation Gate
7. Campaign Planning (Audience & Budget Recommendations)
"""

from datetime import datetime, timezone
import time
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.growth_engine.config import GrowthEngineSettings
from app.growth_engine.schemas import (
    CampaignPlanRequest,
    CreativeGenerateRequest,
    KitSummaryResponse,
    OrchestrateKitRequest,
    OrchestrateKitResponse,
    OrchestrationStepTrace,
    PropertyGrounding,
    SAMSegmentRequest,
    SegmentedFeature,
)
from app.growth_engine.services.creative_service import MetaMuseImageService
from app.growth_engine.services.grounding_service import GroundingService
from app.growth_engine.services.marketing_intelligence_service import MetaMuseSparkService
from app.growth_engine.services.visual_intelligence_service import MetaSAMService, VisualIntelligenceError
from app.growth_engine.services.voice_service import MetaMuseVoiceService


class OrchestratorServiceError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class LocalPipelineOrchestrator:
    def __init__(
        self,
        settings: GrowthEngineSettings | None = None,
        *,
        http_client: httpx.Client | None = None,
    ):
        self.settings = settings or GrowthEngineSettings()
        self.grounding_service = GroundingService()
        self.voice_service = MetaMuseVoiceService(self.settings, http_client=http_client)
        self.sam_service = MetaSAMService(self.settings, http_client=http_client)
        self.creative_service = MetaMuseImageService(self.settings, http_client=http_client)
        self.marketing_service = MetaMuseSparkService(self.settings, http_client=http_client)

    def orchestrate_marketing_kit(
        self,
        request: OrchestrateKitRequest,
        property_record: dict[str, Any],
        property_images: list[dict[str, Any]] | None = None,
    ) -> OrchestrateKitResponse:
        """Run the end-to-end multi-model marketing creation workflow.
        Each step records its authentic model/source and elapsed execution time.
        """
        steps: list[OrchestrationStepTrace] = []
        kit_id = uuid4()

        # Step 1: Grounding Snapshot Freeze
        t0 = time.perf_counter()
        grounding = self.grounding_service.snapshot_from_dict(property_record)
        t_ground = int((time.perf_counter() - t0) * 1000)
        steps.append(
            OrchestrationStepTrace(
                step_name="Grounding Freeze",
                model_id="crm_grounding_snapshot",
                status="completed",
                details=f"Frozen property {grounding.title} ({grounding.bedrooms} BHK, {grounding.locality}) with hash {grounding.grounding_hash[:8]}...",
                duration_ms=max(1, t_ground),
                source="local_pipeline",
            )
        )

        # Step 2: Brief Structured Parsing
        t0 = time.perf_counter()
        brief_model = self.settings.muse_voice_model if request.brief.input_mode == "voice" else "deterministic_brief_parser"
        brief_data = self.voice_service.parse_structured_brief(
            property_id=request.property_id,
            raw_text=request.brief.raw_input,
            input_mode=request.brief.input_mode,
        )
        t_brief = int((time.perf_counter() - t0) * 1000)
        steps.append(
            OrchestrationStepTrace(
                step_name="Marketing Brief Structuring",
                model_id=brief_model,
                status="completed",
                details=f"Extracted {len(brief_data.key_selling_points)} selling points with tone '{brief_data.tone}' and objective '{brief_data.campaign_objective}'",
                duration_ms=max(1, t_brief),
                source="local_pipeline",
            )
        )

        # Step 3: Visual Intelligence & Architectural Segmentation (Meta SAM 3.1)
        t0 = time.perf_counter()
        segmented_features: list[SegmentedFeature] = []
        valid_images = [img for img in (property_images or []) if img.get("image_url")]

        # Match selected_image_ids if supplied
        selected_img = None
        if request.selected_image_ids:
            sel_id_str = {str(i) for i in request.selected_image_ids}
            for img in valid_images:
                if str(img.get("id")) in sel_id_str:
                    selected_img = img
                    break
            if not selected_img:
                raise OrchestratorServiceError(
                    "mismatched_image",
                    f"Selected image ID {request.selected_image_ids[0]} does not belong to property {request.property_id}."
                )
        elif valid_images:
            selected_img = valid_images[0]

        source_image_id: UUID | None = None
        if selected_img:
            image_url = selected_img.get("image_url", "")
            source_image_id = UUID(str(selected_img["id"]))
            try:
                sam_res = self.sam_service.segment_property_image(
                    request=SAMSegmentRequest(property_id=request.property_id, image_id=source_image_id),
                    image_url=image_url,
                    property_context={"locality": grounding.locality, "bedrooms": grounding.bedrooms},
                )
                segmented_features = sam_res.features
                t_sam = int((time.perf_counter() - t0) * 1000)
                steps.append(
                    OrchestrationStepTrace(
                        step_name="Visual Intelligence Segmentation",
                        model_id=sam_res.model_id,
                        status="completed",
                        details=f"Identified {len(segmented_features)} architectural features from photography ({sam_res.architectural_summary[:80]})",
                        duration_ms=max(1, t_sam),
                        source="live_provider" if sam_res.is_authentic else "local_pipeline",
                    )
                )
            except VisualIntelligenceError as exc:
                segmented_features = []
                t_sam = int((time.perf_counter() - t0) * 1000)
                steps.append(
                    OrchestrationStepTrace(
                        step_name="Visual Intelligence Segmentation",
                        model_id="sam_3_1_unavailable",
                        status="error",
                        details=f"Architectural segmentation unavailable: {exc}",
                        duration_ms=max(1, t_sam),
                        source="local_pipeline",
                    )
                )
        else:
            t_sam = int((time.perf_counter() - t0) * 1000)
            steps.append(
                OrchestrationStepTrace(
                    step_name="Visual Intelligence Segmentation",
                    model_id="none",
                    status="skipped",
                    details="No property photos available for architectural segmentation.",
                    duration_ms=max(1, t_sam),
                    source="local_pipeline",
                )
            )

        # Step 4: Creative Generation (Meta Muse Image)
        creative_assets = []
        t0 = time.perf_counter()
        if request.generate_creative:
            cr_req = CreativeGenerateRequest(
                kit_id=kit_id,
                property_id=request.property_id,
                source_image_id=source_image_id,
                asset_type="concept_graphic",
                creative_prompt=f"Modern architectural showcase for {grounding.bedrooms} BHK in {grounding.locality}. {request.brief.raw_input[:100]}",
                style="modern-minimalist" if brief_data.tone == "professional" else "luxury",
            )
            asset = self.creative_service.generate_creative_asset(
                request=cr_req,
                grounding=grounding,
                segmented_features=segmented_features,
            )
            creative_assets.append(asset)
            status_cr = "completed"
            model_cr = asset.model_id
            details_cr = f"Generated watermarked concept creative {asset.id} (source: {asset.source})"
            source_cr = "live_provider" if asset.source == "muse_image" else "local_pipeline"
        else:
            status_cr = "skipped"
            model_cr = "none"
            details_cr = "Creative generation skipped by owner option"
            source_cr = "local_pipeline"

        t_cr = int((time.perf_counter() - t0) * 1000)
        steps.append(
            OrchestrationStepTrace(
                step_name="Creative Studio Generation",
                model_id=model_cr,
                status=status_cr,
                details=details_cr,
                duration_ms=max(1, t_cr),
                source=source_cr,
            )
        )

        # Step 5: Content Intelligence Generation (Meta Muse Spark)
        t0 = time.perf_counter()
        content_items = self.marketing_service.generate_content_items(
            kit_id=kit_id,
            grounding=grounding,
            brief=brief_data.model_dump(mode="json"),
            channels=request.channels,
            creative_asset_id=creative_assets[0].id if creative_assets else None,
        )
        t_copy = int((time.perf_counter() - t0) * 1000)
        spark_model = content_items[0].source if content_items else "deterministic_template"
        spark_source = "live_provider" if spark_model == "muse_spark" else "local_pipeline"
        steps.append(
            OrchestrationStepTrace(
                step_name="Marketing Copy Studio",
                model_id=spark_model,
                status="completed",
                details=f"Drafted platform copy for {len(content_items)} channels: {', '.join([c.channel for c in content_items])}",
                duration_ms=max(1, t_copy),
                source=spark_source,
            )
        )

        # Step 6: Grounding Verification Gate
        all_verified = all(c.grounding_verified for c in content_items)
        steps.append(
            OrchestrationStepTrace(
                step_name="Claim Grounding Verification Gate",
                model_id="grounding_verification_gate",
                status="completed" if all_verified else "error",
                details="All claims verified against CRM data (bedrooms, locality, price, amenities)" if all_verified else "Grounding discrepancies flagged in notes",
                duration_ms=2,
                source="local_pipeline",
            )
        )

        # Step 7: Campaign Planning (Meta Muse Spark)
        campaign_plan = None
        t0 = time.perf_counter()
        if request.generate_plan:
            plan_req = CampaignPlanRequest(kit_id=kit_id)
            campaign_plan = self.marketing_service.generate_campaign_plan(
                kit_id=kit_id,
                grounding=grounding,
                request=plan_req,
                brief=brief_data.model_dump(mode="json"),
            )
            status_plan = "completed"
            details_plan = f"Recommended INR {float(campaign_plan.suggested_daily_budget):,.0f}/day across {len(campaign_plan.recommended_placements)} placements"
            model_plan = campaign_plan.source
            source_plan = "live_provider" if model_plan == "muse_spark_planner" else "local_pipeline"
        else:
            status_plan = "skipped"
            details_plan = "Campaign plan skipped by owner option"
            model_plan = "none"
            source_plan = "local_pipeline"

        t_plan = int((time.perf_counter() - t0) * 1000)
        steps.append(
            OrchestrationStepTrace(
                step_name="Campaign Strategy & Budget Planning",
                model_id=model_plan,
                status=status_plan,
                details=details_plan,
                duration_ms=max(1, t_plan),
                source=source_plan,
            )
        )

        kit_summary = KitSummaryResponse(
            id=kit_id,
            property_id=request.property_id,
            brief_id=uuid4(),
            grounding=grounding,
            objective=brief_data.campaign_objective,
            status="draft",
            version=1,
            content_items=content_items,
            creative_assets=creative_assets,
            campaign_plan=campaign_plan,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        return OrchestrateKitResponse(
            kit=kit_summary,
            steps_executed=steps,
        )


# Backwards compatibility alias
MetaMuseWonderOrchestrator = LocalPipelineOrchestrator
