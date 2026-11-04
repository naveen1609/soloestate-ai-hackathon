"""Owner Approval and Asset Export service for SoloEstate AI Growth Engine-lite.
Enforces the strict separation between content approval and future external publish/spend authorization.
Supports in-place editing, re-grounding checks, revision control, status decisions, and structured kit exports.
"""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.growth_engine.schemas import (
    CampaignPlanDecision,
    CampaignPlanResponse,
    ContentItemEditDecision,
    ContentItemResponse,
    CreativeAssetDecision,
    CreativeAssetResponse,
    KitExportPackage,
    KitSummaryResponse,
    PropertyGrounding,
)
from app.growth_engine.services.grounding_service import (
    GroundingError,
    GroundingService,
    PropertyInactiveError,
    PropertyNotFoundError,
    StaleGroundingError,
    make_structured_copy,
    render_structured_copy,
    render_fact_value,
)


class ApprovalServiceError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.status_code = status_code
        super().__init__(message)


class GrowthApprovalService:
    def __init__(self):
        self.grounding_service = GroundingService()

    def validate_kit_lifecycle(self, kit: KitSummaryResponse) -> None:
        """Reject modifications on terminal or archived kits."""
        if kit.status == "archived":
            raise ApprovalServiceError(
                "kit_archived",
                f"Kit {kit.id} is archived. No modifications or approval decisions are permitted.",
                409,
            )

    def revalidate_crm_property(
        self,
        current_property_record: dict[str, Any] | None,
        expected_hash: str,
        property_id: UUID,
    ) -> PropertyGrounding:
        """Ensure property still exists in CRM, is active, and facts have not drifted."""
        if not current_property_record:
            raise PropertyNotFoundError(property_id)

        current_grounding = self.grounding_service.snapshot_from_dict(current_property_record)

        if not current_grounding.active:
            raise PropertyInactiveError(property_id)

        if current_grounding.grounding_hash != expected_hash:
            raise StaleGroundingError(expected_hash, current_grounding.grounding_hash)

        return current_grounding

    def decide_content_item(
        self,
        item: ContentItemResponse,
        decision: ContentItemEditDecision,
        grounding: PropertyGrounding,
        current_property_record: dict[str, Any] | None = None,
        expected_hash: str | None = None,
    ) -> ContentItemResponse:
        """Apply owner review decision, optional in-place copy edits, and re-verify grounding."""
        # 1. Revision / concurrency control
        if decision.expected_version is not None and decision.expected_version != item.version:
            raise ApprovalServiceError(
                "version_conflict",
                f"Content item revision conflict: expected version {decision.expected_version}, but current version is {item.version}.",
                409,
            )

        # 2. Revalidate live CRM property facts if CRM record provided
        active_grounding = grounding
        target_hash = expected_hash or grounding.grounding_hash
        if current_property_record is not None:
            active_grounding = self.revalidate_crm_property(
                current_property_record, target_hash, grounding.property_id
            )

        structured = item.structured_copy
        if decision.template_id:
            if decision.status != "draft":
                raise ApprovalServiceError("review_required", "Save and review the new template before approval.", 422)
            structured = make_structured_copy(item.channel, decision.template_id, active_grounding)

        rendered = None
        if structured and structured.channel == item.channel:
            try:
                rendered = render_structured_copy(structured, active_grounding)
            except GroundingError as exc:
                if decision.status != "rejected":
                    raise ApprovalServiceError(exc.code, str(exc), exc.status_code) from exc

        stored = (item.headline, item.body, item.call_to_action, item.hashtags)
        edits = (decision.headline, decision.body, decision.call_to_action, decision.hashtags)
        if any(value is not None and value != current for value, current in zip(edits, stored)):
            raise ApprovalServiceError("grounding_violation", "Choose a server-rendered template to edit copy.", 422)
        if decision.status == "approved" and rendered != stored:
            raise ApprovalServiceError("grounding_violation", "Regenerate and review this item before approval.", 422)
        headline, body, cta, hashtags = rendered or stored

        # 5. State transitions check
        valid_transitions = {
            "draft": {"draft", "approved", "rejected"},
            "approved": {"draft", "rejected"},
            "rejected": {"draft", "approved"},
        }
        if decision.status not in valid_transitions.get(item.status, set()):
            raise ApprovalServiceError(
                "invalid_state_transition",
                f"Cannot transition content item from '{item.status}' to '{decision.status}'.",
                400,
            )

        approved_at = datetime.now(timezone.utc) if decision.status == "approved" else None
        source = "owner_edit" if decision.template_id else item.source

        return ContentItemResponse(
            id=item.id,
            kit_id=item.kit_id,
            channel=item.channel,
            headline=headline,
            body=body,
            call_to_action=cta,
            hashtags=hashtags,
            creative_asset_id=item.creative_asset_id,
            grounding_verified=rendered is not None,
            verification_notes="Rendered from current CRM references." if rendered else "Regeneration required.",
            status=decision.status,
            approval_notes=decision.approval_notes or item.approval_notes,
            approved_at=approved_at,
            version=item.version + 1,
            source=source,
            fact_references=structured.fact_references if structured else [],
            structured_copy=structured,
            claim_checks={"structured_copy": structured.model_dump(mode="json")} if structured else {},
            rejection_reason=decision.approval_notes if decision.status == "rejected" else "",
            created_at=item.created_at,
            updated_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def validate_creative_export(asset: CreativeAssetResponse) -> None:
        if (
            asset.is_authentic or asset.segmentation_data.get("export_format") != "labelled_concept_v1"
            or not asset.image_url.startswith(("data:image/png;base64,", "data:image/svg+xml;utf8,"))
        ):
            raise ApprovalServiceError("creative_regeneration_required", "Regenerate this creative to attach a durable labelled asset.", 422)

    def decide_creative_asset(
        self,
        asset: CreativeAssetResponse,
        decision: CreativeAssetDecision,
        current_property_record: dict[str, Any] | None = None,
        expected_hash: str | None = None,
    ) -> CreativeAssetResponse:
        """Apply owner review decision to a creative concept asset."""
        if decision.expected_version is not None and decision.expected_version != asset.version:
            raise ApprovalServiceError(
                "version_conflict",
                f"Creative asset revision conflict: expected version {decision.expected_version}, but current version is {asset.version}.",
                409,
            )

        if current_property_record is not None and expected_hash is not None:
            self.revalidate_crm_property(current_property_record, expected_hash, asset.property_id)

        if decision.status == "approved":
            self.validate_creative_export(asset)

        return CreativeAssetResponse(
            id=asset.id,
            kit_id=asset.kit_id,
            property_id=asset.property_id,
            source_image_id=asset.source_image_id,
            asset_type=asset.asset_type,
            is_authentic=asset.is_authentic,
            concept_watermark=asset.concept_watermark,
            image_url=asset.image_url,
            segmentation_data=asset.segmentation_data,
            prompt_text=asset.prompt_text,
            prompt_hash=asset.prompt_hash,
            model_id=asset.model_id,
            source=asset.source,
            status=decision.status,
            version=asset.version + 1,
            rejection_reason=decision.notes if decision.status == "rejected" else "",
            created_at=asset.created_at,
        )

    def decide_campaign_plan(
        self,
        plan: CampaignPlanResponse,
        decision: CampaignPlanDecision,
        current_property_record: dict[str, Any] | None = None,
        expected_hash: str | None = None,
    ) -> CampaignPlanResponse:
        """Approve or reject campaign strategy. Content approval NEVER authorizes live spend."""
        if decision.expected_version is not None and decision.expected_version != plan.version:
            raise ApprovalServiceError(
                "version_conflict",
                f"Campaign plan revision conflict: expected version {decision.expected_version}, but current version is {plan.version}.",
                409,
            )

        if decision.plan_id and decision.plan_id != plan.id:
            raise ApprovalServiceError(
                "plan_id_mismatch",
                f"Campaign plan identity mismatch: decision targeted plan {decision.plan_id}, but current active plan is {plan.id}.",
                409,
            )

        active_grounding = None
        if current_property_record is not None and expected_hash is not None:
            active_grounding = self.revalidate_crm_property(current_property_record, expected_hash, plan.property_id)

        if decision.status == "approved":
            if active_grounding is None:
                raise ApprovalServiceError("grounding_violation", "Current CRM facts are required for campaign approval.", 422)
            self.validate_plan_render(plan, active_grounding)

        staged = dict(plan.staged_launch_payload)
        staged["content_plan_status"] = decision.status
        staged["launch_authorized"] = False
        staged["boundary_notice"] = "Campaign plan approved as marketing strategy only. No ad account spend is authorized in this MVP."

        return CampaignPlanResponse(
            id=plan.id,
            kit_id=plan.kit_id,
            property_id=plan.property_id,
            objective=plan.objective,
            target_audience_spec=plan.target_audience_spec,
            recommended_placements=plan.recommended_placements,
            suggested_daily_budget=plan.suggested_daily_budget,
            suggested_duration_days=plan.suggested_duration_days,
            rationale=plan.rationale,
            staged_launch_payload=staged,
            status=decision.status,
            approved_at=datetime.now(timezone.utc) if decision.status == "approved" else None,
            version=plan.version + 1,
            source=plan.source,
            rejection_reason=decision.notes if decision.status == "rejected" else "",
            created_at=plan.created_at,
            updated_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def validate_plan_render(plan: CampaignPlanResponse, grounding: PropertyGrounding) -> None:
        from app.growth_engine.services.marketing_intelligence_service import campaign_fields

        expected = campaign_fields(grounding, plan.suggested_daily_budget, plan.suggested_duration_days)
        actual = {name: getattr(plan, name) for name in expected}
        if "content_plan_status" in plan.staged_launch_payload:
            expected["staged_launch_payload"].update(
                content_plan_status=plan.status,
                boundary_notice="Campaign plan approved as marketing strategy only. No ad account spend is authorized in this MVP.",
            )
        if plan.property_id != grounding.property_id or actual != expected:
            raise ApprovalServiceError("grounding_violation", "Regenerate the campaign from current CRM facts before approval or export.", 422)

    def export_kit_package(
        self,
        kit: KitSummaryResponse,
        current_property_record: dict[str, Any] | None = None,
        *,
        validate_live_crm: bool = False,
    ) -> KitExportPackage:
        """Package approved marketing assets, copy, and plans for export.
        Independently re-verifies live CRM property facts and claim validity before exporting.
        """
        # Revalidate live CRM facts
        if validate_live_crm or current_property_record is not None:
            active_grounding = self.revalidate_crm_property(
                current_property_record, kit.grounding.grounding_hash, kit.property_id
            )
        else:
            active_grounding = kit.grounding

        formatted_price = render_fact_value("property_price", active_grounding)

        approved_content = []
        for item in kit.content_items:
            if item.status == "approved":
                stored = (item.headline, item.body, item.call_to_action, item.hashtags)
                try:
                    rendered = (
                        render_structured_copy(item.structured_copy, active_grounding)
                        if item.structured_copy and item.structured_copy.channel == item.channel else None
                    )
                except GroundingError as exc:
                    raise ApprovalServiceError("export_grounding_violation", str(exc), 422) from exc
                if rendered != stored:
                    raise ApprovalServiceError("export_grounding_violation", "Approved copy does not match its CRM rendering. Regenerate it before export.", 422)
                approved_content.append({
                    "id": str(item.id),
                    "channel": item.channel,
                    "headline": item.headline,
                    "body": item.body,
                    "call_to_action": item.call_to_action,
                    "hashtags": item.hashtags,
                    "status": item.status,
                    "version": item.version,
                    "grounding_verified": True,
                })

        for asset in kit.creative_assets:
            if asset.status == "approved":
                self.validate_creative_export(asset)

        approved_creatives = [
            {
                "id": str(cr.id),
                "asset_type": cr.asset_type,
                "is_authentic": cr.is_authentic,
                "concept_watermark": cr.concept_watermark,
                "image_url": cr.image_url,
                "prompt_hash": cr.prompt_hash,
                "status": cr.status,
                "version": cr.version,
            }
            for cr in kit.creative_assets
            if cr.status == "approved"
        ]

        plan_data = None
        if kit.campaign_plan and kit.campaign_plan.status == "approved":
            try:
                self.validate_plan_render(kit.campaign_plan, active_grounding)
            except ApprovalServiceError as exc:
                raise ApprovalServiceError("export_grounding_violation", str(exc), 422) from exc
            staged = dict(kit.campaign_plan.staged_launch_payload)
            staged["launch_authorized"] = False
            plan_data = {
                "objective": kit.campaign_plan.objective,
                "status": kit.campaign_plan.status,
                "suggested_daily_budget_inr": float(kit.campaign_plan.suggested_daily_budget),
                "suggested_duration_days": kit.campaign_plan.suggested_duration_days,
                "recommended_placements": kit.campaign_plan.recommended_placements,
                "target_audience_spec": kit.campaign_plan.target_audience_spec,
                "rationale": kit.campaign_plan.rationale,
                "staged_launch_payload": staged,
            }

        return KitExportPackage(
            kit_id=kit.id,
            property_id=kit.property_id,
            property_title=active_grounding.title,
            locality=active_grounding.locality,
            price_formatted=formatted_price,
            approved_content=approved_content,
            approved_creatives=approved_creatives,
            campaign_plan=plan_data,
            export_timestamp=datetime.now(timezone.utc),
        )
