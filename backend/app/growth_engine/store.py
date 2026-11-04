"""Durable PostgreSQL persistence store for SoloEstate AI Growth Engine-lite.
Ensures that kit generation, retrieval, editing, approval, and export operate across
independent HTTP requests and process restarts.
Enforces atomic compare-and-update version concurrency, supersession, and photo provenance.
"""

from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

from app.config import Settings
from app.database import connect
from app.growth_engine.fixtures import DEMO_PROPERTY_IMAGES
from app.growth_engine.schemas import (
    CampaignPlanResponse,
    ContentItemResponse,
    CreativeAssetResponse,
    KitSummaryResponse,
    MarketingBriefCreate,
    MarketingBriefResponse,
    PropertyGrounding,
    PropertyImageItem,
)
from app.growth_engine.services.grounding_service import (
    CRMUnavailableError,
    PropertyInactiveError,
    PropertyNotFoundError,
    StaleGroundingError,
)


def copy_metadata(claim_checks):
    from app.growth_engine.schemas import StructuredCopy
    from pydantic import ValidationError

    try:
        structured = StructuredCopy.model_validate((claim_checks or {}).get("structured_copy"))
    except ValidationError:
        return {}
    return {"structured_copy": structured, "fact_references": structured.fact_references}


class GrowthStoreError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 500):
        self.code = code
        self.status_code = status_code
        super().__init__(message)


class GrowthEngineStore:
    def __init__(self, settings: Settings | None = None, *, connection_factory=None):
        self.settings = settings or Settings()
        self._connection_factory = connection_factory

    def get_connection(self) -> psycopg.Connection:
        if self._connection_factory is not None:
            return self._connection_factory()
        return connect(self.settings)

    def seed_demo_images_if_empty(self) -> None:
        """Seed demo authentic property photographs if not already present."""
        try:
            with self.get_connection() as conn:
                with conn.transaction():
                    for img in DEMO_PROPERTY_IMAGES:
                        conn.execute(
                            """
                            INSERT INTO soloestate.growth_property_images
                                (id, property_id, image_url, caption, is_authentic, features, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO NOTHING
                            """,
                            (
                                UUID(img["id"]),
                                UUID(img["property_id"]),
                                img["image_url"],
                                img["caption"],
                                img["is_authentic"],
                                img["features"],
                                img["created_at"],
                            ),
                        )
        except Exception:
            pass

    def get_property_images(self, property_id: UUID, *, include_demo_fixtures: bool = False) -> list[PropertyImageItem]:
        """Fetch authentic property photos for a property from database.
        Exposes fixture photos only in explicit demo/test mode with labeled provenance.
        Never substitutes sample photos silently.
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    rows = cur.execute(
                        """
                        SELECT id, property_id, image_url, caption, is_authentic, features, created_at
                        FROM soloestate.growth_property_images
                        WHERE property_id = %s
                        ORDER BY created_at ASC
                        """,
                        (property_id,),
                    ).fetchall()
                    if rows:
                        return [
                            PropertyImageItem(
                                id=r["id"],
                                property_id=r["property_id"],
                                image_url=r["image_url"],
                                caption=r["caption"],
                                is_authentic=r["is_authentic"],
                                features=r["features"],
                                created_at=r["created_at"],
                            )
                            for r in rows
                        ]

                    # Return demo fixtures only in explicit demo/test mode with labeled provenance
                    if include_demo_fixtures:
                        demo_matches = [img for img in DEMO_PROPERTY_IMAGES if UUID(img["property_id"]) == property_id]
                        if demo_matches:
                            return [
                                PropertyImageItem(
                                    id=UUID(img["id"]),
                                    property_id=UUID(img["property_id"]),
                                    image_url=img["image_url"],
                                    caption=f"[Demo Fixture] {img['caption']}",
                                    is_authentic=False,
                                    features=img["features"],
                                    created_at=datetime.fromisoformat(img["created_at"]),
                                )
                                for img in demo_matches
                            ]
                    return []
        except psycopg.Error as exc:
            logger.error("Database error fetching property images: %s", exc)
            raise GrowthStoreError("db_error", "An internal database error occurred while fetching property images.", 500) from exc

    def save_brief(self, brief: MarketingBriefCreate) -> UUID:
        """Persist marketing brief into database durably with all fields."""
        b_id = uuid4()
        try:
            with self.get_connection() as conn:
                with conn.transaction():
                    conn.execute(
                        """
                        INSERT INTO soloestate.growth_briefs
                            (id, property_id, input_mode, raw_input, target_audience, key_selling_points, tone, campaign_objective, special_offers, status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')
                        """,
                        (
                            b_id,
                            brief.property_id,
                            brief.input_mode,
                            brief.raw_input,
                            brief.target_audience,
                            brief.key_selling_points,
                            brief.tone,
                            brief.campaign_objective,
                            brief.special_offers,
                        ),
                    )
            return b_id
        except psycopg.Error as exc:
            logger.error("Failed to persist marketing brief: %s", exc)
            raise GrowthStoreError("db_error", "An internal database error occurred while persisting marketing brief.", 500) from exc

    def save_kit(
        self,
        kit: KitSummaryResponse,
        brief_data: MarketingBriefCreate | dict[str, Any] | None = None,
    ) -> KitSummaryResponse:
        """Persist generated kit summary and child items in strict dependency order.
        Stores actual brief content and enforces cross-property photo isolation.
        """
        try:
            with self.get_connection() as conn:
                with conn.transaction():
                    # 1. Verify property exists in CRM
                    prop_row = conn.execute(
                        "SELECT id, active FROM soloestate.properties WHERE id = %s",
                        (kit.property_id,),
                    ).fetchone()
                    if not prop_row:
                        raise GrowthStoreError("property_not_found", f"Property {kit.property_id} does not exist in CRM.", 404)
                    if prop_row[1] is False:
                        raise GrowthStoreError("property_inactive", f"Property {kit.property_id} is inactive in CRM.", 422)

                    # 2. Insert or update actual brief content (no placeholder text)
                    if kit.brief_id:
                        if brief_data:
                            if isinstance(brief_data, dict):
                                b_mode = brief_data.get("input_mode", "text")
                                b_raw = brief_data.get("raw_input", kit.objective)
                                b_aud = brief_data.get("target_audience", "")
                                b_pts = brief_data.get("key_selling_points", [])
                                b_tone = brief_data.get("tone", "professional")
                                b_obj = brief_data.get("campaign_objective", kit.objective)
                                b_off = brief_data.get("special_offers", "")
                            else:
                                b_mode = brief_data.input_mode
                                b_raw = brief_data.raw_input
                                b_aud = brief_data.target_audience
                                b_pts = brief_data.key_selling_points
                                b_tone = brief_data.tone
                                b_obj = brief_data.campaign_objective
                                b_off = brief_data.special_offers
                        else:
                            b_mode = "text"
                            b_raw = kit.objective
                            b_aud = "Target audience"
                            b_pts = []
                            b_tone = "professional"
                            b_obj = kit.objective
                            b_off = ""

                        conn.execute(
                            """
                            INSERT INTO soloestate.growth_briefs
                                (id, property_id, input_mode, raw_input, target_audience, key_selling_points, tone, campaign_objective, special_offers, status)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')
                            ON CONFLICT (id) DO UPDATE SET
                                raw_input = EXCLUDED.raw_input,
                                target_audience = EXCLUDED.target_audience,
                                key_selling_points = EXCLUDED.key_selling_points,
                                tone = EXCLUDED.tone,
                                campaign_objective = EXCLUDED.campaign_objective,
                                special_offers = EXCLUDED.special_offers,
                                updated_at = now()
                            """,
                            (
                                kit.brief_id,
                                kit.property_id,
                                b_mode,
                                b_raw,
                                b_aud,
                                b_pts,
                                b_tone,
                                b_obj,
                                b_off,
                            ),
                        )

                    # 3. Insert Kit
                    conn.execute(
                        """
                        INSERT INTO soloestate.growth_kits
                            (id, property_id, brief_id, grounding_snapshot, grounding_version, grounding_hash, objective, status, version)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            status = EXCLUDED.status,
                            version = EXCLUDED.version,
                            updated_at = now()
                        """,
                        (
                            kit.id,
                            kit.property_id,
                            kit.brief_id,
                            json.dumps(kit.grounding.model_dump(mode="json")),
                            kit.grounding.grounding_version,
                            kit.grounding.grounding_hash,
                            kit.objective,
                            kit.status,
                            kit.version,
                        ),
                    )

                    # 4. Insert Creative Assets (strictly verifying source_image_id belongs to kit.property_id)
                    for cr in kit.creative_assets:
                        valid_source_id = None
                        if cr.source_image_id:
                            img_exists = conn.execute(
                                "SELECT 1 FROM soloestate.growth_property_images WHERE id = %s AND property_id = %s",
                                (cr.source_image_id, kit.property_id),
                            ).fetchone()
                            if not img_exists:
                                raise GrowthStoreError(
                                    "cross_property_photo_error",
                                    f"Source image {cr.source_image_id} does not belong to property {kit.property_id}.",
                                    422,
                                )
                            valid_source_id = cr.source_image_id

                        conn.execute(
                            """
                            INSERT INTO soloestate.growth_creative_assets
                                (id, kit_id, property_id, source_image_id, asset_type, is_authentic, concept_watermark, image_url, segmentation_data, prompt_text, prompt_hash, model_id, status, version, source, rejection_reason)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO UPDATE SET
                                status = EXCLUDED.status,
                                version = EXCLUDED.version,
                                rejection_reason = EXCLUDED.rejection_reason
                            """,
                            (
                                cr.id,
                                kit.id,
                                kit.property_id,
                                valid_source_id,
                                cr.asset_type,
                                cr.is_authentic,
                                cr.concept_watermark,
                                cr.image_url,
                                json.dumps(cr.segmentation_data),
                                cr.prompt_text,
                                cr.prompt_hash,
                                cr.model_id,
                                cr.status,
                                cr.version,
                                cr.source,
                                cr.rejection_reason or "",
                            ),
                        )

                    # 5. Insert Content Items
                    for item in kit.content_items:
                        valid_cr_id = None
                        if item.creative_asset_id:
                            cr_exists = conn.execute(
                                "SELECT 1 FROM soloestate.growth_creative_assets WHERE id = %s AND kit_id = %s",
                                (item.creative_asset_id, kit.id),
                            ).fetchone()
                            if cr_exists:
                                valid_cr_id = item.creative_asset_id

                        conn.execute(
                            """
                            INSERT INTO soloestate.growth_content_items
                                (id, kit_id, channel, headline, body, call_to_action, hashtags, creative_asset_id, grounding_verified, verification_notes, status, approval_notes, approved_at, version, source, claim_checks, rejection_reason)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO UPDATE SET
                                headline = EXCLUDED.headline,
                                body = EXCLUDED.body,
                                call_to_action = EXCLUDED.call_to_action,
                                hashtags = EXCLUDED.hashtags,
                                status = EXCLUDED.status,
                                approval_notes = EXCLUDED.approval_notes,
                                approved_at = EXCLUDED.approved_at,
                                version = EXCLUDED.version,
                                source = EXCLUDED.source,
                                claim_checks = EXCLUDED.claim_checks,
                                rejection_reason = EXCLUDED.rejection_reason,
                                updated_at = now()
                            """,
                            (
                                item.id,
                                kit.id,
                                item.channel,
                                item.headline,
                                item.body,
                                item.call_to_action,
                                item.hashtags,
                                valid_cr_id,
                                item.grounding_verified,
                                item.verification_notes,
                                item.status,
                                item.approval_notes,
                                item.approved_at,
                                item.version,
                                item.source,
                                json.dumps(item.claim_checks),
                                item.rejection_reason or "",
                            ),
                        )

                    # 6. Insert Campaign Plan (ensuring single active plan)
                    if kit.campaign_plan:
                        plan = kit.campaign_plan
                        # Mark any prior plan for this kit as superseded
                        conn.execute(
                            "UPDATE soloestate.growth_campaign_plans SET is_active = false, status = 'superseded' WHERE kit_id = %s AND id != %s",
                            (kit.id, plan.id),
                        )
                        conn.execute(
                            """
                            INSERT INTO soloestate.growth_campaign_plans
                                (id, kit_id, property_id, objective, target_audience_spec, recommended_placements, suggested_daily_budget, suggested_duration_days, rationale, staged_launch_payload, status, approved_at, version, source, rejection_reason, is_active)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true)
                            ON CONFLICT (id) DO UPDATE SET
                                status = EXCLUDED.status,
                                approved_at = EXCLUDED.approved_at,
                                version = EXCLUDED.version,
                                staged_launch_payload = EXCLUDED.staged_launch_payload,
                                rejection_reason = EXCLUDED.rejection_reason,
                                is_active = true,
                                updated_at = now()
                            """,
                            (
                                plan.id,
                                kit.id,
                                kit.property_id,
                                plan.objective,
                                json.dumps(plan.target_audience_spec),
                                plan.recommended_placements,
                                plan.suggested_daily_budget,
                                plan.suggested_duration_days,
                                plan.rationale,
                                json.dumps(plan.staged_launch_payload),
                                plan.status,
                                plan.approved_at,
                                plan.version,
                                plan.source,
                                plan.rejection_reason or "",
                            ),
                        )
            return kit
        except psycopg.Error as exc:
            logger.error("Durable persistence failed: %s", exc)
            raise GrowthStoreError("db_error", "An internal database error occurred during durable persistence.", 500) from exc

    def _fence_crm_revision(self, cur, kit_id: UUID) -> None:
        """Atomically lock and verify the CRM property revision inside the decision transaction.
        Uses FOR SHARE OF p so concurrent CRM mutations cannot race.
        Fails closed with typed exceptions if CRM revision changed, property is inactive, or missing.
        """
        prop_fence = cur.execute(
            """
            SELECT k.property_id, k.grounding_version, k.grounding_hash, p.version AS crm_version, p.active
            FROM soloestate.growth_kits k
            JOIN soloestate.properties p ON p.id = k.property_id
            WHERE k.id = %s
            FOR SHARE OF p
            """,
            (kit_id,),
        ).fetchone()

        if not prop_fence:
            cur.execute("SELECT property_id FROM soloestate.growth_kits WHERE id = %s", (kit_id,))
            kit_row = cur.fetchone()
            if not kit_row:
                raise GrowthStoreError("kit_not_found", f"Kit {kit_id} not found.", 404)
            raise PropertyNotFoundError(kit_row["property_id"])

        if prop_fence["active"] is False:
            raise PropertyInactiveError(prop_fence["property_id"])

        if prop_fence["crm_version"] != prop_fence["grounding_version"]:
            raise StaleGroundingError(
                prop_fence["grounding_hash"],
                f"crm_version_{prop_fence['crm_version']}",
            )

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
        """Atomic compare-and-update of a single content item.
        Fences CRM property freshness inside transaction with FOR SHARE OF p.
        Updates only the target row and returns HTTP 409 if expected_version does not match.
        Never updates or upserts sibling items or stale kit aggregates.
        """
        try:
            with self.get_connection() as conn:
                with conn.transaction():
                    with conn.cursor(row_factory=dict_row) as cur:
                        self._fence_crm_revision(cur, kit_id)

                        updated_row = cur.execute(
                            """
                            UPDATE soloestate.growth_content_items
                            SET status = %s,
                                headline = COALESCE(%s, headline),
                                body = COALESCE(%s, body),
                                call_to_action = COALESCE(%s, call_to_action),
                                hashtags = COALESCE(%s, hashtags),
                                approval_notes = %s,
                                rejection_reason = %s,
                                source = %s,
                                grounding_verified = %s,
                                verification_notes = %s,
                                claim_checks = %s,
                                approved_at = CASE WHEN %s = 'approved' THEN now() ELSE NULL END,
                                version = version + 1,
                                updated_at = now()
                            WHERE id = %s AND kit_id = %s AND status != 'superseded' AND version = %s
                            RETURNING id, kit_id, channel, headline, body, call_to_action, hashtags,
                                      creative_asset_id, grounding_verified, verification_notes,
                                      status, approval_notes, approved_at, version, source,
                                      claim_checks, rejection_reason, created_at, updated_at
                            """,
                            (
                                status,
                                headline,
                                body,
                                call_to_action,
                                hashtags,
                                approval_notes,
                                rejection_reason,
                                source,
                                grounding_verified,
                                verification_notes,
                                json.dumps(claim_checks or {}),
                                status,
                                item_id,
                                kit_id,
                                expected_version,
                            ),
                        ).fetchone()

                        if not updated_row:
                            cur.execute(
                                "SELECT status, version FROM soloestate.growth_content_items WHERE id = %s AND kit_id = %s",
                                (item_id, kit_id),
                            )
                            existing = cur.fetchone()
                            if not existing:
                                raise GrowthStoreError("content_item_not_found", f"Content item {item_id} not found in kit {kit_id}.", 404)
                            if existing["status"] == "superseded":
                                raise GrowthStoreError(
                                    "item_superseded",
                                    f"Content item {item_id} has been superseded by a newer revision and cannot be modified.",
                                    409,
                                )
                            raise GrowthStoreError(
                                "version_conflict",
                                f"Content item version conflict: current revision is {existing['version']}, but expected {expected_version}.",
                                409,
                            )

                        self._refresh_kit_status(conn, kit_id)

                        return ContentItemResponse(
                            id=updated_row["id"],
                            kit_id=updated_row["kit_id"],
                            channel=updated_row["channel"],
                            headline=updated_row["headline"],
                            body=updated_row["body"],
                            call_to_action=updated_row["call_to_action"],
                            hashtags=updated_row["hashtags"] or [],
                            creative_asset_id=updated_row["creative_asset_id"],
                            grounding_verified=updated_row["grounding_verified"],
                            verification_notes=updated_row["verification_notes"],
                            status=updated_row["status"],
                            approval_notes=updated_row["approval_notes"],
                            approved_at=updated_row["approved_at"],
                            version=updated_row["version"],
                            source=updated_row["source"],
                            claim_checks=updated_row["claim_checks"] or {},
                            **copy_metadata(updated_row["claim_checks"]),
                            rejection_reason=updated_row["rejection_reason"] or "",
                            created_at=updated_row["created_at"],
                            updated_at=updated_row["updated_at"],
                        )
        except (PropertyNotFoundError, PropertyInactiveError, StaleGroundingError, GrowthStoreError):
            raise
        except psycopg.OperationalError as exc:
            logger.error("Database connection failure during content item decision: %s", exc)
            raise CRMUnavailableError(details=str(exc)) from exc
        except psycopg.Error as exc:
            logger.error("Database error during content item update: %s", exc)
            raise GrowthStoreError("db_error", "An internal database error occurred during content item update.", 500) from exc

    def update_creative_asset_decision(
        self,
        kit_id: UUID,
        asset_id: UUID,
        expected_version: int,
        status: str,
        rejection_reason: str = "",
    ) -> CreativeAssetResponse:
        """Atomic compare-and-update of a single creative asset.
        Fences CRM property freshness inside transaction with FOR SHARE OF p.
        """
        try:
            with self.get_connection() as conn:
                with conn.transaction():
                    with conn.cursor(row_factory=dict_row) as cur:
                        self._fence_crm_revision(cur, kit_id)

                        updated_row = cur.execute(
                            """
                            UPDATE soloestate.growth_creative_assets
                            SET status = %s,
                                rejection_reason = %s,
                                version = version + 1
                            WHERE id = %s AND kit_id = %s AND status != 'superseded' AND version = %s
                            RETURNING id, kit_id, property_id, source_image_id, asset_type,
                                      is_authentic, concept_watermark, image_url, segmentation_data,
                                      prompt_text, prompt_hash, model_id, status, version, source,
                                      rejection_reason, created_at
                            """,
                            (
                                status,
                                rejection_reason,
                                asset_id,
                                kit_id,
                                expected_version,
                            ),
                        ).fetchone()

                        if not updated_row:
                            cur.execute(
                                "SELECT status, version FROM soloestate.growth_creative_assets WHERE id = %s AND kit_id = %s",
                                (asset_id, kit_id),
                            )
                            existing = cur.fetchone()
                            if not existing:
                                raise GrowthStoreError("creative_asset_not_found", f"Creative asset {asset_id} not found in kit {kit_id}.", 404)
                            if existing["status"] == "superseded":
                                raise GrowthStoreError(
                                    "asset_superseded",
                                    f"Creative asset {asset_id} has been superseded by a newer revision and cannot be modified.",
                                    409,
                                )
                            raise GrowthStoreError(
                                "version_conflict",
                                f"Creative asset version conflict: current revision is {existing['version']}, but expected {expected_version}.",
                                409,
                            )

                        self._refresh_kit_status(conn, kit_id)

                        return CreativeAssetResponse(
                            id=updated_row["id"],
                            kit_id=updated_row["kit_id"],
                            property_id=updated_row["property_id"],
                            source_image_id=updated_row["source_image_id"],
                            asset_type=updated_row["asset_type"],
                            is_authentic=updated_row["is_authentic"],
                            concept_watermark=updated_row["concept_watermark"],
                            image_url=updated_row["image_url"],
                            segmentation_data=updated_row["segmentation_data"] or {},
                            prompt_text=updated_row["prompt_text"],
                            prompt_hash=updated_row["prompt_hash"],
                            model_id=updated_row["model_id"],
                            status=updated_row["status"],
                            version=updated_row["version"],
                            source=updated_row["source"],
                            rejection_reason=updated_row["rejection_reason"] or "",
                            created_at=updated_row["created_at"],
                        )
        except psycopg.Error as exc:
            logger.error("Database error during creative asset update: %s", exc)
            raise GrowthStoreError("db_error", "An internal database error occurred during creative asset update.", 500) from exc

    def update_campaign_plan_decision(
        self,
        kit_id: UUID,
        plan_id: UUID,
        expected_version: int,
        status: str,
        rejection_reason: str = "",
        staged_launch_payload: dict[str, Any] | None = None,
    ) -> CampaignPlanResponse:
        """Atomic compare-and-update of active campaign plan.
        Fences CRM property freshness inside transaction with FOR SHARE OF p.
        Requires exact plan_id and expected_version. Fails closed if plan is superseded or inactive.
        """
        try:
            with self.get_connection() as conn:
                with conn.transaction():
                    with conn.cursor(row_factory=dict_row) as cur:
                        self._fence_crm_revision(cur, kit_id)

                        updated_row = cur.execute(
                            """
                            UPDATE soloestate.growth_campaign_plans
                            SET status = %s,
                                rejection_reason = %s,
                                staged_launch_payload = COALESCE(%s, staged_launch_payload),
                                approved_at = CASE WHEN %s = 'approved' THEN now() ELSE NULL END,
                                version = version + 1,
                                updated_at = now()
                            WHERE id = %s AND kit_id = %s AND is_active = true AND version = %s
                            RETURNING id, kit_id, property_id, objective, target_audience_spec,
                                      recommended_placements, suggested_daily_budget,
                                      suggested_duration_days, rationale, staged_launch_payload,
                                      status, approved_at, version, source, rejection_reason,
                                      created_at, updated_at
                            """,
                            (
                                status,
                                rejection_reason,
                                json.dumps(staged_launch_payload) if staged_launch_payload else None,
                                status,
                                plan_id,
                                kit_id,
                                expected_version,
                            ),
                        ).fetchone()

                        if not updated_row:
                            cur.execute(
                                "SELECT id, version, is_active FROM soloestate.growth_campaign_plans WHERE id = %s AND kit_id = %s",
                                (plan_id, kit_id),
                            )
                            existing = cur.fetchone()
                            if not existing:
                                raise GrowthStoreError("campaign_plan_not_found", f"Campaign plan {plan_id} not found in kit {kit_id}.", 404)
                            if not existing["is_active"]:
                                raise GrowthStoreError(
                                    "plan_superseded",
                                    f"Campaign plan {plan_id} is superseded and cannot be modified.",
                                    409,
                                )
                            raise GrowthStoreError(
                                "version_conflict",
                                f"Campaign plan version conflict: current revision is {existing['version']}, but expected {expected_version}.",
                                409,
                            )

                        self._refresh_kit_status(conn, kit_id)

                        return CampaignPlanResponse(
                            id=updated_row["id"],
                            kit_id=updated_row["kit_id"],
                            property_id=updated_row["property_id"],
                            objective=updated_row["objective"],
                            target_audience_spec=updated_row["target_audience_spec"] or {},
                            recommended_placements=updated_row["recommended_placements"] or [],
                            suggested_daily_budget=Decimal(str(updated_row["suggested_daily_budget"])),
                            suggested_duration_days=updated_row["suggested_duration_days"],
                            rationale=updated_row["rationale"],
                            staged_launch_payload=updated_row["staged_launch_payload"] or {},
                            status=updated_row["status"],
                            approved_at=updated_row["approved_at"],
                            version=updated_row["version"],
                            source=updated_row["source"],
                            rejection_reason=updated_row["rejection_reason"] or "",
                            created_at=updated_row["created_at"],
                            updated_at=updated_row["updated_at"],
                        )
        except (PropertyNotFoundError, PropertyInactiveError, StaleGroundingError, GrowthStoreError):
            raise
        except psycopg.OperationalError as exc:
            logger.error("Database connection failure during campaign plan decision: %s", exc)
            raise CRMUnavailableError(details=str(exc)) from exc
        except psycopg.Error as exc:
            logger.error("Database error during campaign plan update: %s", exc)
            raise GrowthStoreError("db_error", "An internal database error occurred during campaign plan update.", 500) from exc

    def supersede_content_for_channels(self, kit_id: UUID, channels: list[str]) -> None:
        """Mark old content items for specific channels as superseded."""
        try:
            with self.get_connection() as conn:
                with conn.transaction():
                    conn.execute(
                        """
                        UPDATE soloestate.growth_content_items
                        SET status = 'superseded',
                            updated_at = now()
                        WHERE kit_id = %s AND channel = ANY(%s) AND status != 'superseded'
                        """,
                        (kit_id, channels),
                    )
        except psycopg.Error as exc:
            logger.error("Database error superseding content items: %s", exc)
            raise GrowthStoreError("db_error", "An internal database error occurred while superseding content items.", 500) from exc

    def save_creative_asset(self, kit_id: UUID, asset: CreativeAssetResponse) -> CreativeAssetResponse:
        """Targeted single-row write: Persist a generated creative asset without whole-kit upsert."""
        try:
            with self.get_connection() as conn:
                with conn.transaction():
                    kit_row = conn.execute(
                        "SELECT property_id FROM soloestate.growth_kits WHERE id = %s",
                        (kit_id,),
                    ).fetchone()
                    if not kit_row:
                        raise GrowthStoreError("kit_not_found", f"Kit {kit_id} not found.", 404)
                    prop_id = kit_row[0]

                    valid_source_id = None
                    if asset.source_image_id:
                        img_exists = conn.execute(
                            "SELECT 1 FROM soloestate.growth_property_images WHERE id = %s AND property_id = %s",
                            (asset.source_image_id, prop_id),
                        ).fetchone()
                        if img_exists:
                            valid_source_id = asset.source_image_id
                        else:
                            raise GrowthStoreError(
                                "invalid_source_image",
                                f"Source image {asset.source_image_id} does not belong to property {prop_id}.",
                                422,
                            )

                    conn.execute(
                        """
                        INSERT INTO soloestate.growth_creative_assets
                            (id, kit_id, property_id, source_image_id, asset_type, is_authentic, concept_watermark, image_url, segmentation_data, prompt_text, prompt_hash, model_id, status, version, source, rejection_reason)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            asset.id,
                            kit_id,
                            prop_id,
                            valid_source_id,
                            asset.asset_type,
                            asset.is_authentic,
                            asset.concept_watermark,
                            asset.image_url,
                            json.dumps(asset.segmentation_data or {}),
                            asset.prompt_text,
                            asset.prompt_hash,
                            asset.model_id,
                            asset.status,
                            asset.version,
                            asset.source,
                            asset.rejection_reason or "",
                        ),
                    )
                    self._refresh_kit_status(conn, kit_id)
            return asset
        except psycopg.Error as exc:
            logger.error("Failed to persist creative asset: %s", exc)
            raise GrowthStoreError("db_error", "An internal database error occurred while persisting creative asset.", 500) from exc

    def replace_content_items_for_channels(
        self,
        kit_id: UUID,
        channels: list[str],
        new_items: list[ContentItemResponse],
    ) -> list[ContentItemResponse]:
        """Atomically supersede previous channel items and insert new replacements in ONE transaction.
        Serializes concurrent replacements per kit/channel using transaction-scoped advisory locks.
        PostgreSQL unique partial index guarantees single active revision invariant.
        If any insert fails, previous content items and approvals remain intact.
        """
        try:
            with self.get_connection() as conn:
                with conn.transaction():
                    # 1. Acquire transaction advisory locks for each channel to serialize concurrent replacements
                    for ch in sorted(channels):
                        conn.execute(
                            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                            (f"growth:content:{kit_id}:{ch}",),
                        )

                    # 2. Supersede previous active content items for these channels
                    conn.execute(
                        """
                        UPDATE soloestate.growth_content_items
                        SET status = 'superseded', updated_at = now()
                        WHERE kit_id = %s AND channel = ANY(%s) AND status != 'superseded'
                        """,
                        (kit_id, channels),
                    )

                    # 3. Insert new replacement items
                    for item in new_items:
                        valid_cr_id = None
                        if item.creative_asset_id:
                            cr_exists = conn.execute(
                                "SELECT 1 FROM soloestate.growth_creative_assets WHERE id = %s AND kit_id = %s",
                                (item.creative_asset_id, kit_id),
                            ).fetchone()
                            if cr_exists:
                                valid_cr_id = item.creative_asset_id

                        conn.execute(
                            """
                            INSERT INTO soloestate.growth_content_items
                                (id, kit_id, channel, headline, body, call_to_action, hashtags, creative_asset_id, grounding_verified, verification_notes, status, approval_notes, approved_at, version, source, claim_checks, rejection_reason)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                item.id,
                                kit_id,
                                item.channel,
                                item.headline,
                                item.body,
                                item.call_to_action,
                                item.hashtags,
                                valid_cr_id,
                                item.grounding_verified,
                                item.verification_notes,
                                item.status,
                                item.approval_notes,
                                item.approved_at,
                                item.version,
                                item.source,
                                json.dumps(item.claim_checks),
                                item.rejection_reason or "",
                            ),
                        )
                    self._refresh_kit_status(conn, kit_id)
            return new_items
        except psycopg.errors.UniqueViolation as exc:
            logger.error("Unique constraint violation during content replacement: %s", exc)
            raise GrowthStoreError(
                "content_replacement_conflict",
                f"Concurrent content replacement conflict on kit {kit_id} for channel(s) {channels}. Retry request.",
                409,
            ) from exc
        except psycopg.Error as exc:
            logger.error("Failed to atomically replace content items: %s", exc)
            raise GrowthStoreError("db_error", "An internal database error occurred while replacing content items.", 500) from exc

    def replace_campaign_plan(self, kit_id: UUID, plan: CampaignPlanResponse) -> CampaignPlanResponse:
        """Atomically supersede any prior active campaign plan and insert the new active plan in ONE transaction."""
        try:
            with self.get_connection() as conn:
                with conn.transaction():
                    kit_row = conn.execute(
                        "SELECT property_id FROM soloestate.growth_kits WHERE id = %s",
                        (kit_id,),
                    ).fetchone()
                    if not kit_row:
                        raise GrowthStoreError("kit_not_found", f"Kit {kit_id} not found.", 404)
                    prop_id = kit_row[0]

                    # 1. Supersede any prior active plans for this kit
                    conn.execute(
                        """
                        UPDATE soloestate.growth_campaign_plans
                        SET is_active = false, status = 'superseded', updated_at = now()
                        WHERE kit_id = %s AND is_active = true
                        """,
                        (kit_id,),
                    )

                    # 2. Insert new active plan
                    conn.execute(
                        """
                        INSERT INTO soloestate.growth_campaign_plans
                            (id, kit_id, property_id, objective, target_audience_spec, recommended_placements, suggested_daily_budget, suggested_duration_days, rationale, staged_launch_payload, status, approved_at, version, source, rejection_reason, is_active)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true)
                        """,
                        (
                            plan.id,
                            kit_id,
                            prop_id,
                            plan.objective,
                            json.dumps(plan.target_audience_spec),
                            plan.recommended_placements,
                            plan.suggested_daily_budget,
                            plan.suggested_duration_days,
                            plan.rationale,
                            json.dumps(plan.staged_launch_payload),
                            plan.status,
                            plan.approved_at,
                            plan.version,
                            plan.source,
                            plan.rejection_reason or "",
                        ),
                    )
                    self._refresh_kit_status(conn, kit_id)
            return plan
        except psycopg.Error as exc:
            logger.error("Failed to replace campaign plan: %s", exc)
            raise GrowthStoreError("db_error", "An internal database error occurred while replacing campaign plan.", 500) from exc

    def _refresh_kit_status(self, conn: psycopg.Connection, kit_id: UUID) -> None:
        """Calculate and update kit status atomically based on active child item statuses."""
        conn.execute(
            """
            UPDATE soloestate.growth_kits
            SET status = (
                CASE
                    WHEN NOT EXISTS (
                        SELECT 1 FROM soloestate.growth_content_items
                        WHERE kit_id = %s AND status NOT IN ('approved', 'superseded')
                    ) AND NOT EXISTS (
                        SELECT 1 FROM soloestate.growth_creative_assets
                        WHERE kit_id = %s AND status NOT IN ('approved', 'superseded')
                    ) THEN 'approved'
                    WHEN EXISTS (
                        SELECT 1 FROM soloestate.growth_content_items
                        WHERE kit_id = %s AND status = 'approved'
                    ) OR EXISTS (
                        SELECT 1 FROM soloestate.growth_creative_assets
                        WHERE kit_id = %s AND status = 'approved'
                    ) THEN 'partially_approved'
                    ELSE 'draft'
                END
            ),
            updated_at = now()
            WHERE id = %s
            """,
            (kit_id, kit_id, kit_id, kit_id, kit_id),
        )

    def get_kit(self, kit_id: UUID) -> KitSummaryResponse | None:
        """Durable read of an active marketing kit across separate HTTP requests and restarts.
        Excludes superseded content revisions and returns the single active campaign plan.
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    kit_row = cur.execute(
                        """
                        SELECT id, property_id, brief_id, grounding_snapshot, grounding_version, grounding_hash, objective, status, version, created_at, updated_at
                        FROM soloestate.growth_kits
                        WHERE id = %s
                        """,
                        (kit_id,),
                    ).fetchone()
                    if not kit_row:
                        return None

                    # Read child content items (excluding superseded revisions)
                    content_rows = cur.execute(
                        """
                        SELECT id, kit_id, channel, headline, body, call_to_action, hashtags, creative_asset_id,
                               grounding_verified, verification_notes, status, approval_notes, approved_at,
                               version, source, claim_checks, rejection_reason, created_at, updated_at
                        FROM soloestate.growth_content_items
                        WHERE kit_id = %s AND status != 'superseded'
                        ORDER BY created_at ASC
                        """,
                        (kit_id,),
                    ).fetchall()

                    # Read child creatives (excluding superseded revisions)
                    creative_rows = cur.execute(
                        """
                        SELECT id, kit_id, property_id, source_image_id, asset_type, is_authentic, concept_watermark,
                               image_url, segmentation_data, prompt_text, prompt_hash, model_id, status,
                               version, source, rejection_reason, created_at
                        FROM soloestate.growth_creative_assets
                        WHERE kit_id = %s AND status != 'superseded'
                        ORDER BY created_at ASC
                        """,
                        (kit_id,),
                    ).fetchall()

                    # Read single active campaign plan deterministically
                    plan_row = cur.execute(
                        """
                        SELECT id, kit_id, property_id, objective, target_audience_spec, recommended_placements,
                               suggested_daily_budget, suggested_duration_days, rationale, staged_launch_payload,
                               status, approved_at, version, source, rejection_reason, created_at, updated_at
                        FROM soloestate.growth_campaign_plans
                        WHERE kit_id = %s AND is_active = true
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        (kit_id,),
                    ).fetchone()

                    grounding_data = kit_row["grounding_snapshot"]
                    if isinstance(grounding_data, str):
                        grounding_data = json.loads(grounding_data)

                    content_items = [
                        ContentItemResponse(
                            id=r["id"],
                            kit_id=r["kit_id"],
                            channel=r["channel"],
                            headline=r["headline"],
                            body=r["body"],
                            call_to_action=r["call_to_action"],
                            hashtags=r["hashtags"] or [],
                            creative_asset_id=r["creative_asset_id"],
                            grounding_verified=r["grounding_verified"],
                            verification_notes=r["verification_notes"],
                            status=r["status"],
                            approval_notes=r["approval_notes"],
                            approved_at=r["approved_at"],
                            version=r.get("version", 1),
                            source=r.get("source", "model"),
                            claim_checks=r.get("claim_checks") or {},
                            **copy_metadata(r.get("claim_checks")),
                            rejection_reason=r.get("rejection_reason", ""),
                            created_at=r["created_at"],
                            updated_at=r["updated_at"],
                        )
                        for r in content_rows
                    ]

                    creative_assets = [
                        CreativeAssetResponse(
                            id=r["id"],
                            kit_id=r["kit_id"],
                            property_id=r["property_id"],
                            source_image_id=r["source_image_id"],
                            asset_type=r["asset_type"],
                            is_authentic=r["is_authentic"],
                            concept_watermark=r["concept_watermark"],
                            image_url=r["image_url"],
                            segmentation_data=r["segmentation_data"] or {},
                            prompt_text=r["prompt_text"],
                            prompt_hash=r["prompt_hash"],
                            model_id=r["model_id"],
                            status=r["status"],
                            version=r.get("version", 1),
                            source=r.get("source", "model"),
                            rejection_reason=r.get("rejection_reason", ""),
                            created_at=r["created_at"],
                        )
                        for r in creative_rows
                    ]

                    campaign_plan = None
                    if plan_row:
                        campaign_plan = CampaignPlanResponse(
                            id=plan_row["id"],
                            kit_id=plan_row["kit_id"],
                            property_id=plan_row["property_id"],
                            objective=plan_row["objective"],
                            target_audience_spec=plan_row["target_audience_spec"] or {},
                            recommended_placements=plan_row["recommended_placements"] or [],
                            suggested_daily_budget=Decimal(str(plan_row["suggested_daily_budget"])),
                            suggested_duration_days=plan_row["suggested_duration_days"],
                            rationale=plan_row["rationale"],
                            staged_launch_payload=plan_row["staged_launch_payload"] or {},
                            status=plan_row["status"],
                            approved_at=plan_row["approved_at"],
                            version=plan_row.get("version", 1),
                            source=plan_row.get("source", "model"),
                            rejection_reason=plan_row.get("rejection_reason", ""),
                            created_at=plan_row["created_at"],
                            updated_at=plan_row["updated_at"],
                        )

                    return KitSummaryResponse(
                        id=kit_row["id"],
                        property_id=kit_row["property_id"],
                        brief_id=kit_row["brief_id"],
                        grounding=PropertyGrounding.model_validate(grounding_data),
                        objective=kit_row["objective"],
                        status=kit_row["status"],
                        version=kit_row.get("version", 1),
                        content_items=content_items,
                        creative_assets=creative_assets,
                        campaign_plan=campaign_plan,
                        created_at=kit_row["created_at"],
                        updated_at=kit_row["updated_at"],
                    )
        except psycopg.Error as exc:
            logger.error("Database error retrieving kit: %s", exc)
            raise GrowthStoreError("db_error", "An internal database error occurred while retrieving marketing kit.", 500) from exc

    def list_kits(self, property_id: UUID | None = None) -> list[KitSummaryResponse]:
        """List kits from database."""
        try:
            with self.get_connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    if property_id:
                        cur.execute(
                            "SELECT id FROM soloestate.growth_kits WHERE property_id = %s ORDER BY created_at DESC",
                            (property_id,),
                        )
                    else:
                        cur.execute("SELECT id FROM soloestate.growth_kits ORDER BY created_at DESC")
                    ids = [r["id"] for r in cur.fetchall()]

            kits = []
            for kid in ids:
                k = self.get_kit(kid)
                if k:
                    kits.append(k)
            return kits
        except psycopg.Error as exc:
            logger.error("Database error listing kits: %s", exc)
            raise GrowthStoreError("db_error", "An internal database error occurred while listing marketing kits.", 500) from exc
