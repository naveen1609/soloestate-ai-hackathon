"""Real PostgreSQL database verification for SoloEstate AI Growth Engine-lite.
Enforces that:
1. Every provenance source and status is accepted by migration 0003 check constraints.
2. Cross-property photo binding is rejected by compound foreign key constraints.
3. Compare-and-update version concurrency detects conflicts and returns 409.
4. Supersession correctly marks old items and excludes them from active queries.
5. All tests run in isolated rollback transactions without leaving test data.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
import threading
from uuid import uuid4
import psycopg
import pytest

from app.config import Settings
from app.database import connect
from app.growth_engine.schemas import (
    CampaignPlanDecision,
    CampaignPlanResponse,
    ContentItemEditDecision,
    ContentItemResponse,
    CreativeAssetDecision,
    CreativeAssetResponse,
    KitSummaryResponse,
    MarketingBriefCreate,
    PropertyGrounding,
)
from app.growth_engine.services.grounding_service import StaleGroundingError
from app.growth_engine.store import GrowthEngineStore, GrowthStoreError

pytestmark = pytest.mark.integrity_database


@pytest.fixture
def db_conn(request):
    if not request.config.getoption("--run-integrity-db-tests"):
        pytest.skip("Pass --run-integrity-db-tests to run real PostgreSQL verification")

    test_db_url = os.environ.get("TEST_DATABASE_URL")
    if not test_db_url or not test_db_url.strip():
        pytest.skip(
            "TEST_DATABASE_URL environment variable is required to run real PostgreSQL integrity tests. "
            "Fallback to application/demo settings is strictly forbidden to protect shared/production data."
        )

    if "supabase" in test_db_url.lower():
        pytest.fail("TEST_DATABASE_URL must not point at Supabase production/shared database.")

    with psycopg.connect(test_db_url, autocommit=False) as conn:
        with conn.transaction(force_rollback=True):
            yield conn


def make_test_property(conn) -> tuple:
    prop_id = uuid4()
    locality = f"Bengaluru Test {uuid4().hex[:6]}"
    conn.execute(
        """
        INSERT INTO soloestate.properties (id, title, locality, price, bedrooms, description, active)
        VALUES (%s, 'Real DB Test Property', %s, 10500000, 2, '2 BHK test unit', true)
        """,
        (prop_id, locality),
    )
    return prop_id, locality


def test_growth_database_provenance_and_status_constraints(db_conn):
    """Phase 1: Verify every valid runtime provenance and status passes migration 0003 constraints."""
    prop_id, locality = make_test_property(db_conn)
    brief_id = uuid4()
    kit_id = uuid4()

    db_conn.execute(
        """
        INSERT INTO soloestate.growth_briefs (id, property_id, input_mode, raw_input, target_audience, key_selling_points, tone, campaign_objective, special_offers, status)
        VALUES (%s, %s, 'text', 'Test input', 'Tech leads', %s, 'professional', 'lead_generation', '', 'active')
        """,
        (brief_id, prop_id, ["Balcony"]),
    )

    db_conn.execute(
        """
        INSERT INTO soloestate.growth_kits (id, property_id, brief_id, grounding_snapshot, grounding_version, grounding_hash, objective, status, version)
        VALUES (%s, %s, %s, '{}', 1, %s, 'lead_generation', 'draft', 1)
        """,
        (kit_id, prop_id, brief_id, "a" * 64),
    )

    # 1. Test Content Item sources and statuses
    valid_content_sources = ["muse_spark", "deterministic_template", "owner_edit", "model"]
    valid_content_statuses = ["draft", "approved", "rejected", "superseded"]

    for src in valid_content_sources:
        for st in valid_content_statuses:
            case_kit_id = uuid4()
            db_conn.execute(
                """
                INSERT INTO soloestate.growth_kits (id, property_id, brief_id, grounding_snapshot, grounding_version, grounding_hash, objective, status, version)
                VALUES (%s, %s, %s, '{}', 1, %s, 'lead_generation', 'draft', 1)
                """,
                (case_kit_id, prop_id, brief_id, "a" * 64),
            )
            db_conn.execute(
                """
                INSERT INTO soloestate.growth_content_items
                    (id, kit_id, channel, headline, body, call_to_action, hashtags, source, status, grounding_verified, version)
                VALUES (%s, %s, 'instagram_feed', 'Headline', 'Body', 'CTA', %s, %s, %s, true, 1)
                """,
                (uuid4(), case_kit_id, ["#tag"], src, st),
            )

    # 2. Test Creative Asset sources and statuses
    valid_creative_sources = ["muse_image", "svg_concept_fallback", "sam_3_1", "uploaded", "model"]
    valid_creative_statuses = ["draft", "approved", "rejected", "superseded"]

    for src in valid_creative_sources:
        for st in valid_creative_statuses:
            db_conn.execute(
                """
                INSERT INTO soloestate.growth_creative_assets
                    (id, kit_id, property_id, asset_type, is_authentic, concept_watermark, image_url, prompt_text, prompt_hash, model_id, source, status, version)
                VALUES (%s, %s, %s, 'concept_graphic', false, 'Watermark', 'https://cdn.example.com/img.jpg', 'prompt', %s, 'model-1', %s, %s, 1)
                """,
                (uuid4(), kit_id, prop_id, "b" * 64, src, st),
            )

    # 3. Test Campaign Plan sources and statuses
    valid_plan_sources = ["muse_spark_planner", "muse_spark", "deterministic_template", "owner_edit", "model"]
    valid_plan_statuses = ["draft", "approved", "rejected", "superseded"]

    for src in valid_plan_sources:
        for st in valid_plan_statuses:
            db_conn.execute(
                """
                INSERT INTO soloestate.growth_campaign_plans
                    (id, kit_id, property_id, objective, target_audience_spec, recommended_placements, suggested_daily_budget, suggested_duration_days, rationale, staged_launch_payload, source, status, is_active, version)
                VALUES (%s, %s, %s, 'Lead Gen', '{}', %s, 1500, 14, 'Rationale', '{}', %s, %s, false, 1)
                """,
                (uuid4(), kit_id, prop_id, ["Instagram Feed"], src, st),
            )

    # 4. Negative test: Illegal provenance fails closed
    with pytest.raises(psycopg.Error):
        with db_conn.transaction():
            db_conn.execute(
                """
                INSERT INTO soloestate.growth_content_items
                    (id, kit_id, channel, headline, body, call_to_action, hashtags, source, status, grounding_verified, version)
                VALUES (%s, %s, 'instagram_feed', 'Headline', 'Body', 'CTA', %s, 'hallucinated_source', 'draft', true, 1)
                """,
                (uuid4(), kit_id, ["#tag"]),
            )


def test_growth_database_cross_property_photo_isolation(db_conn):
    """Phase 3: Database compound foreign key must reject binding Property B's photo to Property A's creative asset."""
    prop_a, _ = make_test_property(db_conn)
    prop_b, _ = make_test_property(db_conn)

    photo_a = uuid4()
    photo_b = uuid4()

    db_conn.execute(
        """
        INSERT INTO soloestate.growth_property_images (id, property_id, image_url, caption, is_authentic)
        VALUES (%s, %s, 'https://cdn.example.com/photo_a.jpg', 'Property A Facade', true)
        """,
        (photo_a, prop_a),
    )
    db_conn.execute(
        """
        INSERT INTO soloestate.growth_property_images (id, property_id, image_url, caption, is_authentic)
        VALUES (%s, %s, 'https://cdn.example.com/photo_b.jpg', 'Property B Balcony', true)
        """,
        (photo_b, prop_b),
    )

    kit_a = uuid4()
    brief_a = uuid4()
    db_conn.execute(
        """
        INSERT INTO soloestate.growth_briefs (id, property_id, input_mode, raw_input, status)
        VALUES (%s, %s, 'text', 'Brief A', 'active')
        """,
        (brief_a, prop_a),
    )
    db_conn.execute(
        """
        INSERT INTO soloestate.growth_kits (id, property_id, brief_id, grounding_snapshot, grounding_version, grounding_hash, objective, status, version)
        VALUES (%s, %s, %s, '{}', 1, %s, 'lead_generation', 'draft', 1)
        """,
        (kit_a, prop_a, brief_a, "c" * 64),
    )

    # Case 1: Valid photo A on property A succeeds
    db_conn.execute(
        """
        INSERT INTO soloestate.growth_creative_assets
            (id, kit_id, property_id, source_image_id, asset_type, is_authentic, concept_watermark, image_url, prompt_text, prompt_hash, model_id, source, status, version)
        VALUES (%s, %s, %s, %s, 'concept_graphic', false, 'Watermark', 'https://cdn.example.com/cr_a.jpg', 'prompt', %s, 'model', 'muse_image', 'draft', 1)
        """,
        (uuid4(), kit_a, prop_a, photo_a, "d" * 64),
    )

    # Case 2: Photo B on property A MUST violate compound foreign key constraint and fail closed!
    with pytest.raises(psycopg.Error) as exc_fk:
        with db_conn.transaction():
            db_conn.execute(
                """
                INSERT INTO soloestate.growth_creative_assets
                    (id, kit_id, property_id, source_image_id, asset_type, is_authentic, concept_watermark, image_url, prompt_text, prompt_hash, model_id, source, status, version)
                VALUES (%s, %s, %s, %s, 'concept_graphic', false, 'Watermark', 'https://cdn.example.com/cr_bad.jpg', 'prompt', %s, 'model', 'muse_image', 'draft', 1)
                """,
                (uuid4(), kit_a, prop_a, photo_b, "e" * 64),
            )
    assert "growth_creative_assets_source_photo_fkey" in str(exc_fk.value) or "foreign key" in str(exc_fk.value).lower()


def test_growth_database_atomic_compare_and_update_concurrency(db_conn):
    """Phase 2: Atomic compare-and-update updates single row, increments version, and rejects stale revision with 409."""
    prop_id, locality = make_test_property(db_conn)

    @contextmanager
    def factory():
        yield db_conn

    store = GrowthEngineStore(connection_factory=factory)

    brief_data = MarketingBriefCreate(
        property_id=prop_id,
        raw_input="2 BHK modern apartment",
        target_audience="Young families",
        key_selling_points=["Spacious balcony", "Granite kitchen"],
        tone="professional",
        campaign_objective="lead_generation",
    )
    brief_id = uuid4()
    kit_id = uuid4()
    item_id = uuid4()
    cr_id = uuid4()
    plan_id = uuid4()

    grounding = PropertyGrounding(
        property_id=prop_id,
        title="Real DB Concurrency Test Unit",
        locality=locality,
        price_inr=Decimal("10500000"),
        bedrooms=2,
        description="2 BHK test apartment",
        active=True,
        grounding_version=1,
        grounding_hash="f" * 64,
    )

    kit = KitSummaryResponse(
        id=kit_id,
        property_id=prop_id,
        brief_id=brief_id,
        grounding=grounding,
        objective="lead_generation",
        status="draft",
        version=1,
        content_items=[
            ContentItemResponse(
                id=item_id,
                kit_id=kit_id,
                channel="instagram_feed",
                headline="Draft Headline V1",
                body="Draft Body V1",
                call_to_action="DM now",
                hashtags=["#Test"],
                grounding_verified=True,
                verification_notes="Verified",
                status="draft",
                version=1,
                source="muse_spark",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        ],
        creative_assets=[
            CreativeAssetResponse(
                id=cr_id,
                kit_id=kit_id,
                property_id=prop_id,
                asset_type="concept_graphic",
                is_authentic=False,
                concept_watermark="Watermark",
                image_url="https://cdn.example.com/cr.jpg",
                prompt_text="Prompt",
                prompt_hash="g" * 64,
                model_id="muse-image-1.0",
                source="muse_image",
                status="draft",
                version=1,
                created_at=datetime.now(timezone.utc),
            )
        ],
        campaign_plan=CampaignPlanResponse(
            id=plan_id,
            kit_id=kit_id,
            property_id=prop_id,
            objective="lead_generation",
            target_audience_spec={"city": "Bengaluru"},
            recommended_placements=["Instagram Feed"],
            suggested_daily_budget=Decimal("1500.00"),
            suggested_duration_days=14,
            rationale="Test rationale",
            staged_launch_payload={"launch_authorized": False},
            status="draft",
            version=1,
            source="muse_spark_planner",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    # Durable commit into PostgreSQL
    store.save_kit(kit, brief_data=brief_data)

    # 1. Update Content Item Version 1 -> Version 2 (Atomic Success)
    updated_item = store.update_content_item_decision(
        kit_id=kit_id,
        item_id=item_id,
        expected_version=1,
        status="approved",
        headline="Approved Headline V2",
        approval_notes="Approved by owner",
    )
    assert updated_item.version == 2
    assert updated_item.status == "approved"
    assert updated_item.headline == "Approved Headline V2"

    # 2. Concurrent update submitting expected_version=1 MUST raise 409 conflict
    with pytest.raises(GrowthStoreError) as exc_conflict:
        store.update_content_item_decision(
            kit_id=kit_id,
            item_id=item_id,
            expected_version=1,  # stale version!
            status="rejected",
        )
    assert exc_conflict.value.status_code == 409
    assert exc_conflict.value.code == "version_conflict"

    # 3. Creative Asset compare-and-update
    updated_cr = store.update_creative_asset_decision(
        kit_id=kit_id,
        asset_id=cr_id,
        expected_version=1,
        status="approved",
    )
    assert updated_cr.version == 2
    assert updated_cr.status == "approved"

    with pytest.raises(GrowthStoreError) as exc_cr_conflict:
        store.update_creative_asset_decision(
            kit_id=kit_id,
            asset_id=cr_id,
            expected_version=1,
            status="rejected",
        )
    assert exc_cr_conflict.value.status_code == 409

    # 4. Campaign Plan compare-and-update
    updated_plan = store.update_campaign_plan_decision(
        kit_id=kit_id,
        plan_id=plan_id,
        expected_version=1,
        status="approved",
    )
    assert updated_plan.version == 2
    assert updated_plan.status == "approved"

    with pytest.raises(GrowthStoreError) as exc_plan_conflict:
        store.update_campaign_plan_decision(
            kit_id=kit_id,
            plan_id=plan_id,
            expected_version=1,
            status="rejected",
        )
    assert exc_plan_conflict.value.status_code == 409

    # 5. Codex 3: Delayed approval of old plan cannot approve replacement active plan
    plan_b_id = uuid4()
    plan_b = CampaignPlanResponse(
        id=plan_b_id,
        kit_id=kit_id,
        property_id=prop_id,
        objective="lead_generation",
        target_audience_spec={"city": "Bengaluru"},
        recommended_placements=["Facebook Feed"],
        suggested_daily_budget=Decimal("2000.00"),
        suggested_duration_days=7,
        rationale="Replacement plan B",
        staged_launch_payload={"launch_authorized": False},
        status="draft",
        version=1,
        source="muse_spark_planner",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    store.replace_campaign_plan(kit_id, plan_b)

    # Attempting to approve old plan_id must fail closed
    with pytest.raises(GrowthStoreError) as exc_old_plan:
        store.update_campaign_plan_decision(
            kit_id=kit_id,
            plan_id=plan_id,
            expected_version=1,
            status="approved",
        )
    assert exc_old_plan.value.status_code == 409


def test_growth_database_single_active_plan_constraint(db_conn):
    """Codex 3: Migration 0004 unique partial index ensures only one plan can have is_active=true per kit."""
    prop_id, _ = make_test_property(db_conn)
    brief_id = uuid4()
    kit_id = uuid4()

    db_conn.execute(
        """
        INSERT INTO soloestate.growth_briefs (id, property_id, input_mode, raw_input, status)
        VALUES (%s, %s, 'text', 'Test brief', 'active')
        """,
        (brief_id, prop_id),
    )
    db_conn.execute(
        """
        INSERT INTO soloestate.growth_kits (id, property_id, brief_id, grounding_snapshot, grounding_version, grounding_hash, objective, status, version)
        VALUES (%s, %s, %s, '{}', 1, %s, 'lead_generation', 'draft', 1)
        """,
        (kit_id, prop_id, brief_id, "h" * 64),
    )

    # Insert first active plan -> should succeed
    db_conn.execute(
        """
        INSERT INTO soloestate.growth_campaign_plans
            (id, kit_id, property_id, objective, target_audience_spec, recommended_placements, suggested_daily_budget, suggested_duration_days, rationale, staged_launch_payload, source, status, is_active, version)
        VALUES (%s, %s, %s, 'Lead Gen', '{}', %s, 1500, 14, 'Plan 1', '{}', 'muse_spark_planner', 'draft', true, 1)
        """,
        (uuid4(), kit_id, prop_id, ["Instagram Feed"]),
    )

    # Insert second active plan for same kit_id -> must violate unique partial index
    with pytest.raises(psycopg.Error) as exc_unique:
        with db_conn.transaction():
            db_conn.execute(
                """
                INSERT INTO soloestate.growth_campaign_plans
                    (id, kit_id, property_id, objective, target_audience_spec, recommended_placements, suggested_daily_budget, suggested_duration_days, rationale, staged_launch_payload, source, status, is_active, version)
                VALUES (%s, %s, %s, 'Lead Gen', '{}', %s, 2000, 7, 'Plan 2', '{}', 'muse_spark_planner', 'draft', true, 1)
                """,
                (uuid4(), kit_id, prop_id, ["Facebook Feed"]),
            )
    assert "growth_campaign_plans_single_active_idx" in str(exc_unique.value) or "unique" in str(exc_unique.value).lower()


def test_growth_database_photo_deletion_trigger_preserves_creative(db_conn):
    """Codex 3: Migration 0004 trigger clears source_image_id on referencing creative assets before photo delete."""
    prop_id, _ = make_test_property(db_conn)
    photo_id = uuid4()
    brief_id = uuid4()
    kit_id = uuid4()
    cr_id = uuid4()

    db_conn.execute(
        """
        INSERT INTO soloestate.growth_property_images (id, property_id, image_url, caption, is_authentic)
        VALUES (%s, %s, 'https://cdn.example.com/photo.jpg', 'Living room', true)
        """,
        (photo_id, prop_id),
    )
    db_conn.execute(
        """
        INSERT INTO soloestate.growth_briefs (id, property_id, input_mode, raw_input, status)
        VALUES (%s, %s, 'text', 'Test brief', 'active')
        """,
        (brief_id, prop_id),
    )
    db_conn.execute(
        """
        INSERT INTO soloestate.growth_kits (id, property_id, brief_id, grounding_snapshot, grounding_version, grounding_hash, objective, status, version)
        VALUES (%s, %s, %s, '{}', 1, %s, 'lead_generation', 'draft', 1)
        """,
        (kit_id, prop_id, brief_id, "i" * 64),
    )
    db_conn.execute(
        """
        INSERT INTO soloestate.growth_creative_assets
            (id, kit_id, property_id, source_image_id, asset_type, is_authentic, concept_watermark, image_url, prompt_text, prompt_hash, model_id, source, status, version)
        VALUES (%s, %s, %s, %s, 'concept_graphic', false, 'Watermark', 'https://cdn.example.com/cr.jpg', 'prompt', %s, 'model', 'muse_image', 'draft', 1)
        """,
        (cr_id, kit_id, prop_id, photo_id, "j" * 64),
    )

    # Delete photo from growth_property_images
    db_conn.execute("DELETE FROM soloestate.growth_property_images WHERE id = %s", (photo_id,))

    # Verify photo is deleted
    photo_row = db_conn.execute("SELECT id FROM soloestate.growth_property_images WHERE id = %s", (photo_id,)).fetchone()
    assert photo_row is None

    # Verify creative asset row was preserved, its non-null property_id intact, and source_image_id set to NULL
    cr_row = db_conn.execute(
        "SELECT id, property_id, source_image_id FROM soloestate.growth_creative_assets WHERE id = %s",
        (cr_id,),
    ).fetchone()
    assert cr_row is not None
    assert cr_row[1] == prop_id
    assert cr_row[2] is None


def test_growth_database_crm_freshness_fencing_two_connections(request):
    """Codex 4/5 Priority 2: Real two-connection test proving CRM freshness race is fenced.
    1. Pre-commits test fixture on setup connection.
    2. Connection A reads and validates property revision 1.
    3. Connection B updates the property to revision 2 and commits (asserts 1 row updated).
    4. Connection A attempts approval. It must fail as stale (409) and NOT commit approval against old revision.
    5. Reverse ordering: Approval transaction on Conn A holds property lock first; Conn B writer waits/blocks until release.
    """
    if not request.config.getoption("--run-integrity-db-tests"):
        pytest.skip("Pass --run-integrity-db-tests to run real PostgreSQL verification")

    test_db_url = os.environ.get("TEST_DATABASE_URL")
    if not test_db_url or not test_db_url.strip():
        pytest.skip("TEST_DATABASE_URL environment variable is required to run real PostgreSQL integrity tests.")

    if "supabase" in test_db_url.lower():
        pytest.fail("TEST_DATABASE_URL must not point at Supabase production/shared database.")

    prop_id = uuid4()
    brief_id = uuid4()
    kit_id = uuid4()
    item_id = uuid4()

    conn_setup = psycopg.connect(test_db_url, autocommit=False)
    conn_a = psycopg.connect(test_db_url, autocommit=False)
    conn_b = psycopg.connect(test_db_url, autocommit=False)

    try:
        # Pre-commit fixture so both connections A and B see identical starting state
        with conn_setup.transaction():
            conn_setup.execute(
                """
                INSERT INTO soloestate.properties (id, title, locality, price, bedrooms, description, active, version)
                VALUES (%s, 'CRM Freshness Test Prop', 'Indiranagar', 10500000, 2, '2 BHK unit', true, 1)
                """,
                (prop_id,),
            )
            conn_setup.execute(
                """
                INSERT INTO soloestate.growth_briefs (id, property_id, input_mode, raw_input, status)
                VALUES (%s, %s, 'text', 'Test brief', 'active')
                """,
                (brief_id, prop_id),
            )
            conn_setup.execute(
                """
                INSERT INTO soloestate.growth_kits (id, property_id, brief_id, grounding_snapshot, grounding_version, grounding_hash, objective, status, version)
                VALUES (%s, %s, %s, '{}', 1, %s, 'lead_generation', 'draft', 1)
                """,
                (kit_id, prop_id, brief_id, "k" * 64),
            )
            conn_setup.execute(
                """
                INSERT INTO soloestate.growth_content_items
                    (id, kit_id, channel, headline, body, call_to_action, hashtags, source, status, grounding_verified, version)
                VALUES (%s, %s, 'instagram_feed', 'Headline V1', 'Body V1', 'CTA', %s, 'muse_spark', 'draft', true, 1)
                """,
                (item_id, kit_id, ["#tag"]),
            )

        # Step 1: Connection A verifies starting revision is 1
        with conn_a.transaction():
            row_a = conn_a.execute("SELECT version FROM soloestate.properties WHERE id = %s", (prop_id,)).fetchone()
        assert row_a is not None and row_a[0] == 1

        # Step 2: Connection B updates CRM property to revision 2 and commits
        with conn_b.transaction():
            cur_b = conn_b.execute(
                "UPDATE soloestate.properties SET version = 2 WHERE id = %s RETURNING id",
                (prop_id,),
            )
            assert cur_b.fetchone() is not None, "Connection B failed to update property revision."

        # Step 3: Connection A attempts to approve item_id against expected version 1
        @contextmanager
        def factory_a():
            yield conn_a

        store_a = GrowthEngineStore(connection_factory=factory_a)

        # Connection A MUST fail closed with StaleGroundingError (409)
        with pytest.raises(StaleGroundingError) as exc_stale:
            store_a.update_content_item_decision(
                kit_id=kit_id,
                item_id=item_id,
                expected_version=1,
                status="approved",
            )
        assert exc_stale.value.status_code == 409
        assert exc_stale.value.code == "stale_grounding"

        # Verify persisted item on connection A remained draft, NOT approved
        with conn_a.transaction():
            item_row = conn_a.execute(
                "SELECT status, version FROM soloestate.growth_content_items WHERE id = %s",
                (item_id,),
            ).fetchone()
            assert item_row[0] == "draft"
            assert item_row[1] == 1

        # Refresh the fixture's revision for the reverse ordering check.
        with conn_setup.transaction():
            conn_setup.execute("UPDATE soloestate.growth_kits SET grounding_version = 2 WHERE id = %s", (kit_id,))

        # Step 4: Reverse Ordering Test:
        # Connection A starts transaction and holds lock on property.
        # Connection B attempts to update the property and must wait/block until Connection A releases.
        writer_started = threading.Event()
        writer_finished = threading.Event()
        writer_error = []

        def background_writer():
            writer_started.set()
            try:
                with conn_b.transaction():
                    conn_b.execute("UPDATE soloestate.properties SET version = 3 WHERE id = %s", (prop_id,))
            except Exception as e:
                writer_error.append(e)
            finally:
                writer_finished.set()

        with conn_a.transaction():
            # Connection A holds lock on property
            store_a.update_content_item_decision(
                kit_id=kit_id, item_id=item_id, expected_version=1, status="approved",
            )

            # Launch background writer on connection B
            writer_thread = threading.Thread(target=background_writer)
            writer_thread.daemon = True
            writer_thread.start()

            writer_started.wait(timeout=2.0)
            # Give time to attempt update; writer MUST be blocked waiting for lock
            blocked = not writer_finished.wait(timeout=0.3)
            assert blocked, "Connection B should have waited while Connection A held property lock."

        # Connection A committed transaction and released lock; writer must now complete
        writer_thread.join(timeout=3.0)
        assert not writer_error, f"Writer thread failed: {writer_error}"
        assert writer_finished.is_set(), "Writer thread did not complete after lock release."

        # Verify final updated version is 3
        with conn_setup.transaction():
            final_row = conn_setup.execute("SELECT version FROM soloestate.properties WHERE id = %s", (prop_id,)).fetchone()
            assert final_row[0] == 3

    finally:
        # Fixture cleanup
        try:
            with conn_setup.transaction():
                conn_setup.execute("DELETE FROM soloestate.growth_content_items WHERE kit_id = %s", (kit_id,))
                conn_setup.execute("DELETE FROM soloestate.growth_kits WHERE id = %s", (kit_id,))
                conn_setup.execute("DELETE FROM soloestate.growth_briefs WHERE id = %s", (brief_id,))
                conn_setup.execute("DELETE FROM soloestate.properties WHERE id = %s", (prop_id,))
        except Exception:
            pass
        conn_a.close()
        conn_b.close()
        conn_setup.close()


def test_growth_database_synchronized_channel_regeneration_empty_and_approved(request):
    """Codex 5 Priority 2: Synchronized two-connection channel regeneration (empty and existing approved).
    Uses threading.Barrier to fire concurrent replacements at the exact same instant on separate connections.
    Verifies that exactly one active non-superseded item exists in both cases and old revisions are marked superseded.
    """
    if not request.config.getoption("--run-integrity-db-tests"):
        pytest.skip("Pass --run-integrity-db-tests to run real PostgreSQL verification")

    test_db_url = os.environ.get("TEST_DATABASE_URL")
    if not test_db_url or not test_db_url.strip():
        pytest.skip("TEST_DATABASE_URL environment variable is required to run real PostgreSQL integrity tests.")

    prop_id = uuid4()
    brief_id = uuid4()
    kit_id = uuid4()

    conn_setup = psycopg.connect(test_db_url, autocommit=False)
    conn1 = psycopg.connect(test_db_url, autocommit=False)
    conn2 = psycopg.connect(test_db_url, autocommit=False)

    try:
        with conn_setup.transaction():
            conn_setup.execute(
                """
                INSERT INTO soloestate.properties (id, title, locality, price, bedrooms, description, active, version)
                VALUES (%s, 'Concurrent Channel Prop', 'Indiranagar', 10500000, 2, '2 BHK', true, 1)
                """,
                (prop_id,),
            )
            conn_setup.execute(
                """
                INSERT INTO soloestate.growth_briefs (id, property_id, input_mode, raw_input, status)
                VALUES (%s, %s, 'text', 'Test brief', 'active')
                """,
                (brief_id, prop_id),
            )
            conn_setup.execute(
                """
                INSERT INTO soloestate.growth_kits (id, property_id, brief_id, grounding_snapshot, grounding_version, grounding_hash, objective, status, version)
                VALUES (%s, %s, %s, '{}', 1, %s, 'lead_generation', 'draft', 1)
                """,
                (kit_id, prop_id, brief_id, "s" * 64),
            )

        # --- Case A: Initially Empty Channel ('facebook_feed') ---
        barrier_empty = threading.Barrier(2)
        errors_empty = []

        @contextmanager
        def factory_1():
            yield conn1

        @contextmanager
        def factory_2():
            yield conn2

        store_1 = GrowthEngineStore(connection_factory=factory_1)
        store_2 = GrowthEngineStore(connection_factory=factory_2)

        def worker_empty_1():
            try:
                item = ContentItemResponse(
                    id=uuid4(),
                    kit_id=kit_id,
                    channel="facebook_feed",
                    headline="Sync Empty 1",
                    body="Body",
                    call_to_action="CTA",
                    hashtags=["#tag"],
                    grounding_verified=True,
                    verification_notes="",
                    status="draft",
                    version=1,
                    source="muse_spark",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                barrier_empty.wait(timeout=3.0)
                store_1.replace_content_items_for_channels(kit_id, ["facebook_feed"], [item])
            except Exception as e:
                errors_empty.append(("worker_1", e))

        def worker_empty_2():
            try:
                item = ContentItemResponse(
                    id=uuid4(),
                    kit_id=kit_id,
                    channel="facebook_feed",
                    headline="Sync Empty 2",
                    body="Body",
                    call_to_action="CTA",
                    hashtags=["#tag"],
                    grounding_verified=True,
                    verification_notes="",
                    status="draft",
                    version=1,
                    source="deterministic_template",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                barrier_empty.wait(timeout=3.0)
                store_2.replace_content_items_for_channels(kit_id, ["facebook_feed"], [item])
            except Exception as e:
                errors_empty.append(("worker_2", e))

        t1 = threading.Thread(target=worker_empty_1)
        t2 = threading.Thread(target=worker_empty_2)
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        # Exactly ONE active item must exist for facebook_feed
        with conn_setup.transaction():
            active_fb = conn_setup.execute(
                "SELECT id, status FROM soloestate.growth_content_items WHERE kit_id = %s AND channel = 'facebook_feed' AND status != 'superseded'",
                (kit_id,),
            ).fetchall()
            assert len(active_fb) == 1, f"Expected exactly 1 active item for facebook_feed, got {len(active_fb)}"

        # --- Case B: Existing Approved Channel ('instagram_feed') ---
        initial_approved_id = uuid4()
        with conn_setup.transaction():
            conn_setup.execute(
                """
                INSERT INTO soloestate.growth_content_items
                    (id, kit_id, channel, headline, body, call_to_action, hashtags, source, status, grounding_verified, version)
                VALUES (%s, %s, 'instagram_feed', 'Pre-existing Approved', 'Body', 'CTA', %s, 'muse_spark', 'approved', true, 1)
                """,
                (initial_approved_id, kit_id, ["#tag"]),
            )

        barrier_approved = threading.Barrier(2)
        errors_approved = []

        def worker_approved_1():
            try:
                item = ContentItemResponse(
                    id=uuid4(),
                    kit_id=kit_id,
                    channel="instagram_feed",
                    headline="Sync Approved Replacement 1",
                    body="Body",
                    call_to_action="CTA",
                    hashtags=["#tag"],
                    grounding_verified=True,
                    verification_notes="",
                    status="draft",
                    version=2,
                    source="muse_spark",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                barrier_approved.wait(timeout=3.0)
                store_1.replace_content_items_for_channels(kit_id, ["instagram_feed"], [item])
            except Exception as e:
                errors_approved.append(("worker_1", e))

        def worker_approved_2():
            try:
                item = ContentItemResponse(
                    id=uuid4(),
                    kit_id=kit_id,
                    channel="instagram_feed",
                    headline="Sync Approved Replacement 2",
                    body="Body",
                    call_to_action="CTA",
                    hashtags=["#tag"],
                    grounding_verified=True,
                    verification_notes="",
                    status="draft",
                    version=2,
                    source="deterministic_template",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                barrier_approved.wait(timeout=3.0)
                store_2.replace_content_items_for_channels(kit_id, ["instagram_feed"], [item])
            except Exception as e:
                errors_approved.append(("worker_2", e))

        t3 = threading.Thread(target=worker_approved_1)
        t4 = threading.Thread(target=worker_approved_2)
        t3.start()
        t4.start()
        t3.join(timeout=5.0)
        t4.join(timeout=5.0)

        # Exactly ONE active item must exist for instagram_feed and initial item MUST be superseded
        with conn_setup.transaction():
            active_ig = conn_setup.execute(
                "SELECT id, status FROM soloestate.growth_content_items WHERE kit_id = %s AND channel = 'instagram_feed' AND status != 'superseded'",
                (kit_id,),
            ).fetchall()
            assert len(active_ig) == 1, f"Expected exactly 1 active item for instagram_feed, got {len(active_ig)}"

            initial_row = conn_setup.execute(
                "SELECT status FROM soloestate.growth_content_items WHERE id = %s",
                (initial_approved_id,),
            ).fetchone()
            assert initial_row[0] == "superseded", "Pre-existing approved item must be marked superseded."

    finally:
        try:
            with conn_setup.transaction():
                conn_setup.execute("DELETE FROM soloestate.growth_content_items WHERE kit_id = %s", (kit_id,))
                conn_setup.execute("DELETE FROM soloestate.growth_kits WHERE id = %s", (kit_id,))
                conn_setup.execute("DELETE FROM soloestate.growth_briefs WHERE id = %s", (brief_id,))
                conn_setup.execute("DELETE FROM soloestate.properties WHERE id = %s", (prop_id,))
        except Exception:
            pass
        conn1.close()
        conn2.close()
        conn_setup.close()


def test_growth_database_content_replacement_rollback_preserves_old(db_conn):
    """Codex 4/5 Priority 2: Production rollback test through replace_content_items_for_channels.
    An insert error (violating database check constraint) occurring AFTER supersession
    rolls back the entire transaction, preserving previous approved content items and their status.
    """
    prop_id, _ = make_test_property(db_conn)
    brief_id = uuid4()
    kit_id = uuid4()
    item_v1_id = uuid4()

    db_conn.execute(
        """
        INSERT INTO soloestate.growth_briefs (id, property_id, input_mode, raw_input, status)
        VALUES (%s, %s, 'text', 'Test brief', 'active')
        """,
        (brief_id, prop_id),
    )
    db_conn.execute(
        """
        INSERT INTO soloestate.growth_kits (id, property_id, brief_id, grounding_snapshot, grounding_version, grounding_hash, objective, status, version)
        VALUES (%s, %s, %s, '{}', 1, %s, 'lead_generation', 'draft', 1)
        """,
        (kit_id, prop_id, brief_id, "m" * 64),
    )
    db_conn.execute(
        """
        INSERT INTO soloestate.growth_content_items
            (id, kit_id, channel, headline, body, call_to_action, hashtags, source, status, grounding_verified, version)
        VALUES (%s, %s, 'instagram_feed', 'Original Active V1', 'Body', 'CTA', %s, 'muse_spark', 'approved', true, 1)
        """,
        (item_v1_id, kit_id, ["#tag"]),
    )

    @contextmanager
    def factory():
        yield db_conn

    store = GrowthEngineStore(connection_factory=factory)

    # Construct an invalid item that violates database check constraint: source not in allowed check constraint list
    invalid_item = ContentItemResponse.model_construct(
        id=uuid4(),
        kit_id=kit_id,
        channel="instagram_feed",
        headline="Will fail database check constraint",
        body="Body",
        call_to_action="CTA",
        hashtags=["#tag"],
        creative_asset_id=None,
        grounding_verified=True,
        verification_notes="",
        status="draft",
        version=1,
        source="illegal_source_violates_constraint",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    # Executing replace_content_items_for_channels MUST fail on insert and trigger transaction rollback
    with pytest.raises(GrowthStoreError):
        store.replace_content_items_for_channels(kit_id, ["instagram_feed"], [invalid_item])

    # After rollback, original active item V1 must still be approved and NOT superseded
    row = db_conn.execute(
        "SELECT status FROM soloestate.growth_content_items WHERE id = %s",
        (item_v1_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "approved"




def test_authenticated_growth_lifecycle_survives_process_restart(request):
    import base64
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from io import BytesIO
    import socket
    import subprocess
    import sys
    import time
    from urllib.parse import urlsplit
    import httpx
    from PIL import Image

    url = os.environ.get("TEST_DATABASE_URL", "")
    if not request.config.getoption("--run-integrity-db-tests") or not url:
        pytest.skip("Requires an explicitly supplied disposable PostgreSQL database.")
    if urlsplit(url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("The process lifecycle test requires loopback PostgreSQL.")

    owner_token = uuid4().hex + uuid4().hex
    environment = {
        **os.environ,
        "DATABASE_URL": url,
        "MIGRATION_DATABASE_URL": url,
        "DATABASE_SSLMODE": "disable",
        "MODEL_API_KEY": "local-test-fixture",
        "OWNER_API_TOKEN": owner_token,
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    @contextmanager
    def running_app():
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, "-B", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
             "--port", str(port), "--log-level", "warning"],
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5,
                              headers={"Authorization": f"Bearer {owner_token}"}) as client:
                deadline = time.monotonic() + 20
                while True:
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    if process.poll() is not None or time.monotonic() >= deadline:
                        pytest.fail("Disposable Growth application did not start.")
                    time.sleep(0.1)
                yield client
        finally:
            process.terminate()
            process.wait(timeout=10)

    def success(response):
        assert response.is_success, response.text
        return response.json()

    brief_text = "Fixture brief retained across the restart."
    image_id = uuid4()
    with psycopg.connect(url) as conn:
        property_id, _ = make_test_property(conn)
        conn.execute(
            "INSERT INTO soloestate.growth_property_images (id, property_id, image_url) VALUES (%s, %s, %s)",
            (image_id, property_id, "https://example.test/fictional-photo.png"),
        )
    raw_image = BytesIO()
    Image.new("RGB", (10, 10), "red").save(raw_image, format="PNG")
    provider_image = base64.b64encode(raw_image.getvalue()).decode("ascii")

    class ProviderFixture(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.path == "/v1/images/generations":
                value = {"data": [{"b64_json": provider_image}]}
            elif self.path == "/v1/chat/completions":
                value = {"choices": [{"message": {"content": '{"instagram_feed":"standard_residence"}'}}]}
            elif self.path == "/v1/responses":
                value = {"objects": []}
            else:
                self.send_error(404)
                return
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    provider = ThreadingHTTPServer(("127.0.0.1", 0), ProviderFixture)
    provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
    provider_thread.start()
    environment["META_BASE_URL"] = f"http://127.0.0.1:{provider.server_port}/v1"
    try:
        prefix = "/owner/growth-engine"
        with running_app() as client:
            assert client.get(f"{prefix}/kits", headers={"Authorization": ""}).status_code == 401
            routes = success(client.get("/openapi.json"))["paths"]
            assert {"/owner/crm/booking-requests", "/owner/crm/outbox", "/whatsapp/webhook"} <= set(routes)
            created = success(client.post(f"{prefix}/orchestrate-kit", json={
                "property_id": str(property_id),
                "brief": {"property_id": str(property_id), "raw_input": brief_text},
                "selected_image_ids": [str(image_id)],
                "channels": ["instagram_feed"],
                "generate_creative": True,
                "generate_plan": True,
            }))["kit"]
            kit_id = created["id"]
            detail = f"{prefix}/kits/{kit_id}"
            item = created["content_items"][0]
            decision = f"{detail}/content-items/{item['id']}/decision"
            assert client.patch(decision, json={
                "status": "approved", "expected_version": 1, "body": "The residence has a wine cellar.",
            }).status_code == 422
            preview = success(client.patch(decision, json={
                "status": "draft", "expected_version": 1, "template_id": "compact_story",
            }))
            assert preview["structured_copy"]["template_id"] == "compact_story"
            success(client.patch(decision, json={"status": "approved", "expected_version": preview["version"]}))
            asset = created["creative_assets"][0]
            assert asset["source_image_id"] == str(image_id)
            assert asset["source"] == "muse_image"
            assert asset["image_url"].startswith("data:image/png;base64,")
            success(client.patch(f"{detail}/creative-assets/{asset['id']}/decision", json={
                "status": "approved", "expected_version": asset["version"],
            }))
            plan = created["campaign_plan"]
            success(client.patch(f"{detail}/campaign-plan/decision", json={
                "plan_id": plan["id"], "status": "approved", "expected_version": plan["version"],
            }))
            replacement = success(client.post(f"{prefix}/generate-content", json={
                "kit_id": kit_id, "channels": ["instagram_feed"],
            }))[0]
            assert [i["id"] for i in success(client.get(detail))["content_items"]] == [replacement["id"]]
            assert success(client.get(f"{detail}/export"))["approved_content"] == []
            success(client.patch(f"{detail}/content-items/{replacement['id']}/decision", json={
                "status": "approved", "expected_version": replacement["version"],
            }))

        with running_app() as client:
            reloaded = success(client.get(detail))
            assert reloaded["content_items"][0]["structured_copy"]["fact_references"]
            package = success(client.get(f"{detail}/export"))
            assert [item["id"] for item in package["approved_content"]] == [replacement["id"]]
            image_url = package["approved_creatives"][0]["image_url"]
            assert image_url == asset["image_url"]
            with Image.open(BytesIO(base64.b64decode(image_url.split(",", 1)[1]))) as image:
                assert image.size == (480, 62)
                assert (255, 255, 255) in image.crop((0, 10, 480, 62)).get_flattened_data()
            assert package["campaign_plan"]["staged_launch_payload"]["launch_authorized"] is False

        with psycopg.connect(url) as conn:
            assert conn.execute("SELECT raw_input FROM soloestate.growth_briefs WHERE id = %s",
                                (created["brief_id"],)).fetchone() == (brief_text,)
            assert conn.execute("SELECT status FROM soloestate.growth_content_items WHERE id = %s",
                                (item["id"],)).fetchone() == ("superseded",)
    finally:
        provider.shutdown()
        provider.server_close()
        provider_thread.join(timeout=2)
        with psycopg.connect(url) as conn:
            conn.execute("DELETE FROM soloestate.properties WHERE id = %s", (property_id,))


def test_migration_0005_upgrade_reconciles_legacy_duplicates(db_conn):
    from app.growth_engine.migrator import migrate_growth_engine

    property_id, _ = make_test_property(db_conn)
    kit_id = uuid4()
    older, current = uuid4(), uuid4()
    db_conn.execute(
        """INSERT INTO soloestate.growth_kits
           (id, property_id, grounding_snapshot, grounding_version, grounding_hash, objective)
           VALUES (%s, %s, '{}', 1, %s, 'lead_generation')""",
        (kit_id, property_id, "a" * 64),
    )
    original = (
        "-- Growth Engine Migration 0005: Single Active Content Item Constraint Per Kit/Channel\n"
        "-- Enforces that at most one active (non-superseded) content item can exist per kit and channel at the database level.\n\n"
        "CREATE UNIQUE INDEX IF NOT EXISTS growth_content_items_single_active_idx\n"
        "ON soloestate.growth_content_items(kit_id, channel)\n"
        "WHERE status != 'superseded';\n"
    )
    from hashlib import sha256
    original_checksum = sha256(original.encode()).hexdigest()
    db_conn.execute("UPDATE soloestate.growth_migrations SET checksum = %s WHERE version LIKE '0005_%%'",
                    (original_checksum,))
    assert migrate_growth_engine(db_conn) == []
    assert db_conn.execute("SELECT checksum FROM soloestate.growth_migrations WHERE version LIKE '0005_%'").fetchone() == (
        original_checksum,
    )
    db_conn.execute("DROP INDEX soloestate.growth_content_items_single_active_idx")
    db_conn.execute("DELETE FROM soloestate.growth_migrations WHERE version LIKE '0005_%'")
    for item_id, version, status in [(older, 1, "approved"), (current, 2, "draft")]:
        db_conn.execute(
            """INSERT INTO soloestate.growth_content_items
               (id, kit_id, channel, headline, body, call_to_action, version, status)
               VALUES (%s, %s, 'instagram_feed', 'Headline', 'Body', 'Contact owner', %s, %s)""",
            (item_id, kit_id, version, status),
        )
    assert migrate_growth_engine(db_conn) == ["0005_growth_engine_channel_concurrency.sql"]
    assert dict(db_conn.execute(
        "SELECT id, status FROM soloestate.growth_content_items WHERE kit_id = %s", (kit_id,)
    ).fetchall()) == {older: "superseded", current: "draft"}
    assert migrate_growth_engine(db_conn) == []
