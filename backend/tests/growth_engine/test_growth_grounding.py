"""Adversarial Grounding Regression Tests for SoloEstate AI Growth Engine-lite.
Verifies all Codex P1 test cases fail closed:
1. Two-bedroom property promoted as four-bedroom (body & hashtags).
2. Unsupported private pool or helipad.
3. Incorrect locality.
4. Incorrect price.
5. Unsupported legal-title claim.
6. Inactive property.
7. Missing property UUID.
8. Stale property data.
9. Unsupported claims hidden in CTA or hashtags.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4
import pytest

from app.growth_engine.schemas import (
    CampaignPlanResponse,
    ContentItemEditDecision,
    ContentItemResponse,
    KitSummaryResponse,
    PropertyGrounding,
)
from app.growth_engine.services.approval_service import (
    ApprovalServiceError,
    GrowthApprovalService,
)
from app.growth_engine.services.grounding_service import (
    GroundingError,
    GroundingService,
    PropertyInactiveError,
    PropertyNotFoundError,
    StaleGroundingError,
)


def sample_record() -> dict:
    return {
        "id": str(uuid4()),
        "title": "Cedar Luxury Apartment",
        "locality": "Indiranagar",
        "price": "10500000",
        "bedrooms": 2,
        "description": "2 BHK modern apartment in prime Indiranagar with modular kitchen and spacious balcony.",
        "active": True,
        "version": 1,
    }


def test_grounding_snapshot_and_hash():
    service = GroundingService()
    record = sample_record()
    grounding = service.snapshot_from_dict(record)
    assert grounding.price_inr == Decimal("10500000")
    assert grounding.bedrooms == 2
    assert len(grounding.grounding_hash) == 64

    # Hash changes when price changes
    record_altered = {**record, "price": "12000000"}
    grounding2 = service.snapshot_from_dict(record_altered)
    assert grounding.grounding_hash != grounding2.grounding_hash


def test_claim_validation_pass():
    service = GroundingService()
    record = sample_record()
    grounding = service.snapshot_from_dict(record)

    valid_copy = (
        "Discover this stunning 2 BHK home in prime Indiranagar for ₹1.05 Cr! "
        "Featuring a spacious balcony and modular kitchen."
    )
    result = service.validate_claims(
        grounding,
        "",
        headline="Modern 2 BHK in Indiranagar",
        body=valid_copy,
        call_to_action="Schedule your walkthrough today",
        hashtags=["#IndiranagarRealEstate", "#2BHK", "#BangaloreHomes"],
    )
    assert result.is_valid is True
    assert len(result.discrepancies) == 0


# --- Adversarial Case 1: Two-bedroom promoted as four-bedroom ---
def test_adversarial_two_bed_promoted_as_four_bed():
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())

    # In body
    res1 = service.validate_claims(grounding, "Spacious 4 BHK apartment with luxury layout.")
    assert res1.is_valid is False
    assert any("4 BHK" in d or "bedrooms" in d for d in res1.discrepancies)

    # In hashtags
    res2 = service.validate_claims(
        grounding,
        "",
        headline="Modern Home",
        body="Comfortable residence in Indiranagar.",
        call_to_action="Book now",
        hashtags=["#4BHK", "#Indiranagar"],
    )
    assert res2.is_valid is False
    assert any("4 BHK" in d or "bedrooms" in d for d in res2.discrepancies)


# --- Adversarial Case 2: Unsupported private pool or helipad ---
def test_adversarial_unsupported_pool_or_helipad():
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())

    # Helipad in body
    res1 = service.validate_claims(grounding, "Includes private rooftop helipad for executive transit.")
    assert res1.is_valid is False
    assert any("Helipad" in d for d in res1.discrepancies)

    # Olympic pool in body
    res2 = service.validate_claims(grounding, "Enjoy private swimming pool and jacuzzi.")
    assert res2.is_valid is False
    assert any("Pool" in d or "Swimming Pool" in d for d in res2.discrepancies)


# --- Adversarial Case 3: Incorrect locality ---
def test_adversarial_incorrect_locality():
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())

    # Promoted as Whitefield instead of Indiranagar
    res = service.validate_claims(grounding, "Prime residence in Whitefield near IT corridor.")
    assert res.is_valid is False
    assert any("Locality mismatch" in d for d in res.discrepancies)


# --- Adversarial Case 4: Incorrect price ---
def test_adversarial_incorrect_price():
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())  # price is 1.05 Cr

    # Claiming 50 Lakhs
    res1 = service.validate_claims(grounding, "Priced attractively at just 50 Lakhs!")
    assert res1.is_valid is False
    assert any("Price discrepancy" in d for d in res1.discrepancies)

    # Claiming 2.5 Cr
    res2 = service.validate_claims(grounding, "Listed at ₹2.5 Cr in Indiranagar.")
    assert res2.is_valid is False
    assert any("Price discrepancy" in d for d in res2.discrepancies)


# --- Adversarial Case 5: Unsupported legal-title and yield claims ---
def test_adversarial_unsupported_legal_title_and_yield():
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())

    res1 = service.validate_claims(grounding, "Guaranteed 100% legal title and verified documentation.")
    assert res1.is_valid is False
    assert any("legal title" in d.lower() for d in res1.discrepancies)

    res2 = service.validate_claims(grounding, "Assuring high rental yield of 12% annually.")
    assert res2.is_valid is False
    assert any("rental yield" in d.lower() for d in res2.discrepancies)


# --- Adversarial Case 6: Inactive CRM property fails closed ---
def test_adversarial_inactive_property_fails_closed():
    service = GroundingService()
    inactive_record = {**sample_record(), "active": False}

    with pytest.raises(PropertyInactiveError) as exc_info:
        service.snapshot_from_dict(inactive_record)
    assert exc_info.value.code == "property_inactive"
    assert exc_info.value.status_code == 422


# --- Adversarial Case 7: Missing or invalid property UUID fails closed ---
def test_adversarial_missing_property_uuid():
    service = GroundingService()

    with pytest.raises(GroundingError) as exc1:
        service.snapshot_from_dict({})
    assert exc1.value.code == "missing_record"

    with pytest.raises(GroundingError) as exc2:
        service.snapshot_from_dict({"title": "No ID"})
    assert exc2.value.code == "missing_property_id"

    with pytest.raises(GroundingError) as exc3:
        service.snapshot_from_dict({"id": "not-a-valid-uuid", "title": "Bad ID"})
    assert exc3.value.code == "invalid_property_id"


# --- Adversarial Case 8: Stale property data fails closed ---
def test_adversarial_stale_property_data():
    service = GroundingService()
    record = sample_record()
    grounding = service.snapshot_from_dict(record)

    # CRM record price changed
    modified_crm = {**record, "price": "14000000"}

    class MockCRM:
        def detail(self, entity, prop_id):
            return modified_crm

    with pytest.raises(StaleGroundingError) as exc_info:
        service.validate_crm_property(
            grounding.property_id,
            expected_hash=grounding.grounding_hash,
            crm=MockCRM(),
        )
    assert exc_info.value.code == "stale_grounding"
    assert exc_info.value.status_code == 409


# --- Adversarial Case 9: Unsupported claims hidden in CTA or hashtags ---
def test_adversarial_claims_hidden_in_cta_or_hashtags():
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())

    # Hidden in CTA
    res_cta = service.validate_claims(
        grounding,
        "",
        headline="Modern 2 BHK in Indiranagar",
        body="Listed at ₹1.05 Cr.",
        call_to_action="DM now to land on your private rooftop helipad!",
        hashtags=["#Indiranagar"],
    )
    assert res_cta.is_valid is False
    assert any("Helipad" in d for d in res_cta.discrepancies)

    # Hidden in hashtags
    res_tags = service.validate_claims(
        grounding,
        "",
        headline="Modern 2 BHK in Indiranagar",
        body="Listed at ₹1.05 Cr with spacious layout.",
        call_to_action="Message us today",
        hashtags=["#4BHK", "#PrivatePool", "#Whitefield"],
    )
    assert res_tags.is_valid is False
    assert len(res_tags.discrepancies) >= 3


# --- Codex Review 2 Specific Adversarial Regressions ---

def test_adversarial_codex2_word_numbers_bedrooms():
    """Codex 2: 'four bedrooms' written out as English words must fail grounding."""
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())  # 2 BHK

    res = service.validate_claims(grounding, "Stunning home featuring four bedrooms and modern interiors.")
    assert res.is_valid is False
    assert any("Bedroom count mismatch" in d for d in res.discrepancies)


def test_adversarial_codex2_cross_metro_locality_powai():
    """Codex 2: Locality like 'Powai' or 'Bandra' or 'Mumbai' must fail when property is in Bangalore."""
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())  # Indiranagar

    res = service.validate_claims(grounding, "Exclusive luxury penthouse located in scenic Powai.")
    assert res.is_valid is False
    assert any("Locality mismatch" in d for d in res.discrepancies)


def test_adversarial_codex2_strict_price_tolerance():
    """Codex 2: 'Only 1.00 Cr' on a 1.05 Cr property (5% drift) must fail closed under 1% tolerance."""
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())  # 1.05 Cr (10,500,000)

    res = service.validate_claims(grounding, "Available now for only 1.00 Cr in Indiranagar.")
    assert res.is_valid is False
    assert any("Price discrepancy" in d for d in res.discrepancies)


def test_adversarial_codex2_raw_numeric_currency_drift():
    """Codex 2: 'INR 1,000,000' (10 Lakhs vs 1.05 Cr) must fail closed."""
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())  # 1.05 Cr

    res = service.validate_claims(grounding, "Steal deal at INR 1,000,000 in Indiranagar.")
    assert res.is_valid is False
    assert any("Price discrepancy" in d for d in res.discrepancies)


def test_adversarial_codex2_unverified_possession_ready_to_move():
    """Codex 2: 'Ready to move' or '#ReadyToMove' without CRM verification must fail closed."""
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())

    res1 = service.validate_claims(grounding, "Premium residence ready to move immediately.")
    assert res1.is_valid is False
    assert any("possession" in d.lower() for d in res1.discrepancies)

    res2 = service.validate_claims(
        grounding,
        "",
        headline="2 BHK in Indiranagar",
        body="Listed at ₹1.05 Cr.",
        call_to_action="Inquire now",
        hashtags=["#ReadyToMove", "#Indiranagar"],
    )
    assert res2.is_valid is False
    assert any("possession" in d.lower() for d in res2.discrepancies)


def test_adversarial_codex2_unverified_promotional_incentives():
    """Codex 2: 'Free gold on booking' or '#FreeGold' without CRM grounding must fail closed."""
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())

    res1 = service.validate_claims(grounding, "Book today and get free gold coin on spot booking!")
    assert res1.is_valid is False
    assert any("promotional incentive" in d.lower() for d in res1.discrepancies)

    res2 = service.validate_claims(
        grounding,
        "",
        headline="Festive Offer",
        body="2 BHK in Indiranagar at ₹1.05 Cr.",
        call_to_action="Call now",
        hashtags=["#FreeGold", "#ZeroBrokerage"],
    )
    assert res2.is_valid is False
    assert any("promotional incentive" in d.lower() for d in res2.discrepancies)


def test_adversarial_codex2_compound_hashtag_pool():
    """Codex 2: '#PrivatePoolHome' compound hashtag must fail closed."""
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())

    res = service.validate_claims(
        grounding,
        "",
        headline="2 BHK Home",
        body="Listed at ₹1.05 Cr.",
        call_to_action="Visit today",
        hashtags=["#PrivatePoolHome", "#Indiranagar"],
    )
    assert res.is_valid is False
    assert any("Swimming pool" in d or "Pool" in d for d in res.discrepancies)


# --- Codex Review 3 Regressions ---

def test_adversarial_codex3_two_bed_five_spacious_bedrooms_immediate_movein_yield_regression():
    """Codex 3 Regression: Attempt to approve and export:
    'Five spacious bedrooms. Immediate move-in available. 12% annual yield.'
    for a 2-bedroom property whose CRM description records a historical 2% yield.
    Must fail closed on bedroom count, possession claim, AND yield guarantee!
    Free-text descriptions in CRM cannot establish unsupported facts.
    """
    service = GroundingService()
    approval_service = GrowthApprovalService()

    # 2-bedroom property with description mentioning historical 2% yield
    record = {
        "id": str(uuid4()),
        "title": "Cedar Residence",
        "locality": "Indiranagar",
        "price": "10500000",
        "bedrooms": 2,
        "description": "2 BHK modern apartment in prime Indiranagar. Note: Recorded a historical 2% yield in 2021.",
        "active": True,
        "version": 1,
    }
    grounding = service.snapshot_from_dict(record)

    adversarial_copy = "Five spacious bedrooms. Immediate move-in available. 12% annual yield."

    # 1. Direct claim validation check
    val = service.validate_claims(grounding, adversarial_copy)
    assert val.is_valid is False

    # Check that all 3 claims are flagged as discrepancies
    disc_text = " ".join(val.discrepancies).lower()
    assert "bedroom count mismatch" in disc_text or "5 bhk" in disc_text or "claims 5" in disc_text
    assert "possession" in disc_text or "immediate move-in" in disc_text
    assert "yield" in disc_text or "financial claim" in disc_text

    # 2. Attempting to approve this item via approval service MUST fail with 422
    item = ContentItemResponse(
        id=uuid4(),
        kit_id=uuid4(),
        channel="instagram_feed",
        headline="Exclusive Residence",
        body=adversarial_copy,
        call_to_action="Inquire now",
        hashtags=["#Indiranagar"],
        status="draft",
        version=1,
        source="model",
        grounding_verified=False,
        verification_notes="",
        claim_checks={},
        created_at=record.get("created_at") or "2026-11-04T00:00:00Z", # type: ignore
        updated_at=record.get("created_at") or "2026-11-04T00:00:00Z", # type: ignore
    )

    with pytest.raises(ApprovalServiceError) as exc_info:
        approval_service.decide_content_item(
            item=item,
            decision=ContentItemEditDecision(
                status="approved",
                expected_version=1,
            ),
            grounding=grounding,
            current_property_record=record,
        )
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "grounding_violation"


def test_adversarial_codex3_priced_at_80_lac():
    """Codex 3: 'Priced at 80 lac' or '80 lacs' on a 1.05 Cr (105 Lakhs) property must fail closed."""
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())  # 1.05 Cr = 105 Lakhs

    res1 = service.validate_claims(grounding, "Unbelievable deal! Priced at 80 lac in Indiranagar.")
    assert res1.is_valid is False
    assert any("Price discrepancy" in d for d in res1.discrepancies)

    res2 = service.validate_claims(
        grounding,
        "",
        headline="80 Lacs Home",
        body="Luxury living for only 80 lacs.",
        call_to_action="Buy today",
        hashtags=["#80Lacs", "#Indiranagar"],
    )
    assert res2.is_valid is False
    assert any("Price discrepancy" in d for d in res2.discrepancies)


def test_adversarial_codex3_unsupported_complimentary_iphone():
    """Codex 3: 'complimentary iPhone' or '#ComplimentaryIphone' without CRM grounding must fail closed."""
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())

    res1 = service.validate_claims(grounding, "Book your home this month and receive a complimentary iPhone on spot registration!")
    assert res1.is_valid is False
    assert any("promotional incentive" in d.lower() for d in res1.discrepancies)

    res2 = service.validate_claims(
        grounding,
        "",
        headline="Diwali Special",
        body="2 BHK home in Indiranagar.",
        call_to_action="Get your free iPhone",
        hashtags=["#ComplimentaryIphone", "#Indiranagar"],
    )
    assert res2.is_valid is False
    assert any("promotional incentive" in d.lower() for d in res2.discrepancies)


def test_adversarial_codex3_occupancy_certificate_claim():
    """Codex 3: 'Occupancy certificate' / 'OC received' / 'CC received' without CRM facts must fail closed."""
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())

    res1 = service.validate_claims(grounding, "Ready for possession with occupancy certificate received.")
    assert res1.is_valid is False
    assert any("legal/regulatory claim" in d.lower() or "occupancy certificate" in d.lower() for d in res1.discrepancies)

    res2 = service.validate_claims(
        grounding,
        "",
        headline="OC Approved Home",
        body="2 BHK home in Indiranagar.",
        call_to_action="Move in now",
        hashtags=["#OCReceived", "#ClearTitle"],
    )
    assert res2.is_valid is False
    assert any("legal/regulatory claim" in d.lower() for d in res2.discrepancies)


def test_adversarial_codex3_private_cinema_and_tennis_courts():
    """Codex 3: Unsupported luxury amenities 'private cinema' and 'tennis courts' must fail closed."""
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())

    res1 = service.validate_claims(grounding, "Exclusive residency featuring a private cinema and floodlit tennis courts.")
    assert res1.is_valid is False
    assert any("Private Cinema" in d for d in res1.discrepancies)
    assert any("Tennis Courts" in d for d in res1.discrepancies)

    res2 = service.validate_claims(
        grounding,
        "",
        headline="Clubhouse Amenities",
        body="2 BHK apartment with balcony.",
        call_to_action="Play tennis today",
        hashtags=["#PrivateCinema", "#TennisCourts"],
    )
    assert res2.is_valid is False
    assert any("Private Cinema" in d for d in res2.discrepancies)


def test_adversarial_codex3_negation_and_paraphrase_handling():
    """Codex 3: Negated claims ('no private pool', 'not a 4 bhk') must NOT trigger false positives on valid copy,
    while negating actual verified facts ('no 2 bhk') must fail closed.
    """
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())  # 2 BHK, 1.05 Cr, Indiranagar

    # Valid copy with negation of prohibited amenities
    valid_copy = (
        "No private pool or helipad needed when you are 5 minutes from Indiranagar metro! "
        "Unlike exaggerated listings with no 4 BHK here, this is an honest 2 BHK residence listed at ₹1.05 Cr."
    )
    res_valid = service.validate_claims(grounding, valid_copy)
    assert res_valid.is_valid is True
    assert len(res_valid.discrepancies) == 0

    # Contradiction: text falsely negates the actual property fact
    deceptive_copy = "Note: No 2 BHK available here, this has been converted into a massive single suite."
    res_deceptive = service.validate_claims(grounding, deceptive_copy)
    assert res_deceptive.is_valid is False
    assert any("negates property's verified 2 BHK" in d for d in res_deceptive.discrepancies)


def test_adversarial_codex3_crm_changes_invalidate_approval_and_export():
    """Codex 3: Changes to live CRM property invalidate approval eligibility and export."""
    service = GroundingService()
    approval_service = GrowthApprovalService()
    rec = sample_record()
    grounding = service.snapshot_from_dict(rec)
    kit_id = uuid4()

    valid_item = ContentItemResponse(
        id=uuid4(),
        kit_id=kit_id,
        channel="instagram_feed",
        headline="2 BHK in Indiranagar",
        body="Stunning 2 BHK home in Indiranagar at ₹1.05 Cr.",
        call_to_action="Schedule visit",
        hashtags=["#Indiranagar", "#2BHK"],
        status="approved",
        version=2,
        source="owner_edit",
        grounding_verified=True,
        verification_notes="",
        claim_checks={},
        created_at=rec.get("created_at") or "2026-11-04T00:00:00Z", # type: ignore
        updated_at=rec.get("created_at") or "2026-11-04T00:00:00Z", # type: ignore
    )

    kit = KitSummaryResponse(
        id=kit_id,
        property_id=UUID(rec["id"]),
        brief_id=uuid4(),
        grounding=grounding,
        objective="lead_generation",
        status="approved",
        content_items=[valid_item],
        creative_assets=[],
        campaign_plan=None,
        version=1,
        created_at=rec.get("created_at") or "2026-11-04T00:00:00Z", # type: ignore
        updated_at=rec.get("created_at") or "2026-11-04T00:00:00Z", # type: ignore
    )

    # 1. Property facts modified in CRM (price changed from 1.05 Cr to 1.30 Cr)
    altered_rec = {**rec, "price": "13000000"}
    with pytest.raises(StaleGroundingError) as exc_stale:
        approval_service.export_kit_package(kit, current_property_record=altered_rec)
    assert exc_stale.value.status_code == 409

    # 2. Property deactivated in CRM
    inactive_rec = {**rec, "active": False}
    with pytest.raises(PropertyInactiveError) as exc_inactive:
        approval_service.export_kit_package(kit, current_property_record=inactive_rec)
    assert exc_inactive.value.status_code == 422

    # 3. Property deleted from CRM
    with pytest.raises(PropertyNotFoundError) as exc_missing:
        approval_service.export_kit_package(kit, current_property_record=None, validate_live_crm=True)
    assert exc_missing.value.status_code == 404


def test_adversarial_codex3_export_excludes_draft_rejected_superseded_items():
    """Codex 3: Export packages must strictly exclude draft, rejected, and superseded items.
    launch_authorized must be explicitly False.
    """
    service = GroundingService()
    approval_service = GrowthApprovalService()
    rec = sample_record()
    grounding = service.snapshot_from_dict(rec)
    kit_id = uuid4()

    from app.growth_engine.config import GrowthEngineSettings
    from app.growth_engine.schemas import CampaignPlanDecision, CampaignPlanRequest
    from app.growth_engine.services.marketing_intelligence_service import MetaMuseSparkService

    marketing = MetaMuseSparkService(GrowthEngineSettings(model_api_key=None))
    item_approved = marketing.generate_content_items(kit_id, grounding, channels=["instagram_feed"])[0]
    item_approved = approval_service.decide_content_item(
        item_approved, ContentItemEditDecision(status="approved", expected_version=1), grounding, current_property_record=rec
    )
    item_draft = ContentItemResponse(
        id=uuid4(),
        kit_id=kit_id,
        channel="facebook_feed",
        headline="Draft headline",
        body="Draft body at ₹1.05 Cr.",
        call_to_action="Call",
        hashtags=["#Indiranagar"],
        status="draft",
        version=1,
        source="model",
        grounding_verified=True,
        verification_notes="",
        claim_checks={},
        created_at=rec.get("created_at") or "2026-11-04T00:00:00Z", # type: ignore
        updated_at=rec.get("created_at") or "2026-11-04T00:00:00Z", # type: ignore
    )
    item_rejected = ContentItemResponse(
        id=uuid4(),
        kit_id=kit_id,
        channel="festival_offer",
        headline="Rejected headline",
        body="Rejected body at ₹1.05 Cr.",
        call_to_action="Call",
        hashtags=["#Indiranagar"],
        status="rejected",
        version=1,
        source="model",
        grounding_verified=True,
        verification_notes="",
        claim_checks={},
        created_at=rec.get("created_at") or "2026-11-04T00:00:00Z", # type: ignore
        updated_at=rec.get("created_at") or "2026-11-04T00:00:00Z", # type: ignore
    )
    item_superseded = ContentItemResponse(
        id=uuid4(),
        kit_id=kit_id,
        channel="instagram_feed",
        headline="Old superseded headline",
        body="Old superseded body at ₹1.05 Cr.",
        call_to_action="Call",
        hashtags=["#Indiranagar"],
        status="superseded",
        version=1,
        source="model",
        grounding_verified=True,
        verification_notes="",
        claim_checks={},
        created_at=rec.get("created_at") or "2026-11-04T00:00:00Z", # type: ignore
        updated_at=rec.get("created_at") or "2026-11-04T00:00:00Z", # type: ignore
    )

    plan = marketing.generate_campaign_plan(kit_id, grounding, CampaignPlanRequest(kit_id=kit_id))
    plan = approval_service.decide_campaign_plan(
        plan, CampaignPlanDecision(plan_id=plan.id, expected_version=1, status="approved"),
        current_property_record=rec, expected_hash=grounding.grounding_hash,
    )

    kit = KitSummaryResponse(
        id=kit_id,
        property_id=UUID(rec["id"]),
        brief_id=uuid4(),
        grounding=grounding,
        objective="lead_generation",
        status="approved",
        content_items=[item_approved, item_draft, item_rejected, item_superseded],
        creative_assets=[],
        campaign_plan=plan,
        version=1,
        created_at=rec.get("created_at") or "2026-11-04T00:00:00Z", # type: ignore
        updated_at=rec.get("created_at") or "2026-11-04T00:00:00Z", # type: ignore
    )

    pkg = approval_service.export_kit_package(kit, current_property_record=rec)

    # Only 1 item must be exported
    assert len(pkg.approved_content) == 1
    assert pkg.approved_content[0]["id"] == str(item_approved.id)
    assert pkg.approved_content[0]["status"] == "approved"

    # Plan exported with launch_authorized = False
    assert pkg.campaign_plan is not None
    assert pkg.campaign_plan["staged_launch_payload"]["launch_authorized"] is False


# --- Codex Review 4: Exact Audit Examples & Structured Factual-Content Tests ---

def test_adversarial_codex4_audit_exact_example_bedrooms_5():
    """Codex 4: 2-bedroom property claiming 'Bedrooms: 5' must fail closed."""
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())  # 2 BHK, ₹1.05 Cr

    res = service.validate_claims(grounding, "Stunning luxury flat. Bedrooms: 5 in prime Indiranagar.")
    assert res.is_valid is False
    assert any("Bedroom count mismatch" in d for d in res.discrepancies)


def test_adversarial_codex4_audit_exact_example_only_inr_9999():
    """Codex 4: ₹1.05 Cr property claiming 'Only INR 9,999' must fail closed."""
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())  # ₹1.05 Cr (10,500,000)

    res = service.validate_claims(grounding, "Book today for Only INR 9,999 in Indiranagar.")
    assert res.is_valid is False
    assert any("Price discrepancy" in d for d in res.discrepancies)


def test_adversarial_codex4_audit_exact_example_500_sq_ft_rooftop_terrace():
    """Codex 4: Property without CRM area measurement claiming '500 sq ft rooftop terrace' must fail closed."""
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())  # No area_sqft

    res = service.validate_claims(grounding, "Beautiful home with a 500 sq ft rooftop terrace.")
    assert res.is_valid is False
    assert any("measurement" in d.lower() or "terrace" in d.lower() for d in res.discrepancies)


def test_adversarial_codex4_audit_exact_example_24_7_municipal_water():
    """Codex 4: Property claiming '24/7 municipal water' without CRM water supply must fail closed."""
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())  # No water_supply

    res = service.validate_claims(grounding, "Enjoy uninterrupted living with 24/7 municipal water.")
    assert res.is_valid is False
    assert any("utility" in d.lower() or "water" in d.lower() for d in res.discrepancies)


def test_adversarial_codex4_audit_exact_example_private_sauna():
    """Codex 4: Property claiming 'Private sauna' without CRM amenity must fail closed."""
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())  # No sauna

    res = service.validate_claims(grounding, "Unwind in your exclusive Private sauna after a long day.")
    assert res.is_valid is False
    assert any("Sauna" in d for d in res.discrepancies)


def test_adversarial_codex4_audit_examples_in_cta_and_hashtags():
    """Codex 4: The 5 audit examples hidden in CTA or hashtags must fail closed."""
    service = GroundingService()
    grounding = service.snapshot_from_dict(sample_record())

    # CTA with private sauna
    res_cta_sauna = service.validate_claims(
        grounding,
        "",
        headline="Modern 2 BHK Residence",
        body="Located in prime Indiranagar at ₹1.05 Cr.",
        call_to_action="Schedule a tour and try our private sauna!",
        hashtags=["#Indiranagar"],
    )
    assert res_cta_sauna.is_valid is False
    assert any("Sauna" in d for d in res_cta_sauna.discrepancies)

    # Hashtags with 500 sqft and 24/7 water
    res_tags = service.validate_claims(
        grounding,
        "",
        headline="Modern 2 BHK Residence",
        body="Located in prime Indiranagar at ₹1.05 Cr.",
        call_to_action="Message us today",
        hashtags=["#500sqft", "#247water", "#PrivateSauna"],
    )
    assert res_tags.is_valid is False
    assert len(res_tags.discrepancies) >= 2


def test_adversarial_codex4_audit_examples_in_owner_edited_copy():
    """Codex 4: Owner editing copy to inject ungrounded claims must fail approval."""
    from app.growth_engine.services.approval_service import GrowthApprovalService, ApprovalServiceError
    from app.growth_engine.schemas import ContentItemEditDecision

    service = GrowthApprovalService()
    grounding = GroundingService().snapshot_from_dict(sample_record())

    item = ContentItemResponse(
        id=uuid4(),
        kit_id=uuid4(),
        channel="instagram_feed",
        headline="2 BHK in Indiranagar",
        body="Listed at ₹1.05 Cr.",
        call_to_action="DM for info",
        hashtags=["#Indiranagar"],
        grounding_verified=True,
        verification_notes="",
        status="draft",
        version=1,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    # Owner edits to claim 500 sq ft terrace and private sauna
    bad_edit = ContentItemEditDecision(
        expected_version=1,
        status="approved",
        headline="Luxury Home with 500 sq ft rooftop terrace",
        body="Includes Private sauna and 24/7 municipal water.",
    )

    with pytest.raises(ApprovalServiceError) as exc_info:
        service.decide_content_item(item, bad_edit, grounding=grounding)
    assert exc_info.value.code == "grounding_violation"
    assert exc_info.value.status_code == 422


def test_adversarial_codex4_audit_examples_in_campaign_plan_and_export():
    """Codex 4: Plan rationale with audit claims fails approval and export."""
    from app.growth_engine.services.approval_service import GrowthApprovalService, ApprovalServiceError
    from app.growth_engine.schemas import CampaignPlanDecision

    service = GrowthApprovalService()
    rec = sample_record()
    grounding = GroundingService().snapshot_from_dict(rec)

    bad_plan = CampaignPlanResponse(
        id=uuid4(),
        kit_id=uuid4(),
        property_id=grounding.property_id,
        objective="lead_generation",
        target_audience_spec={"city": "Bengaluru"},
        recommended_placements=["Instagram Feed"],
        suggested_daily_budget=Decimal("1500.00"),
        suggested_duration_days=14,
        rationale="Promote 500 sq ft rooftop terrace and private sauna to affluent buyers.",
        staged_launch_payload={"launch_authorized": False},
        status="draft",
        version=1,
        source="muse_spark_planner",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    plan_decision = CampaignPlanDecision(
        plan_id=bad_plan.id,
        expected_version=1,
        status="approved",
    )

    with pytest.raises(ApprovalServiceError) as exc_plan:
        service.decide_campaign_plan(bad_plan, plan_decision, current_property_record=rec, expected_hash=grounding.grounding_hash)
    assert exc_plan.value.code == "grounding_violation"
    assert exc_plan.value.status_code == 422


def test_codex4_positive_structured_facts_render_and_validate():
    """Codex 4 Positive Test: Legitimate CRM-backed facts render and verify cleanly."""
    from app.growth_engine.schemas import FactReference
    from app.growth_engine.services.grounding_service import render_fact_value, AuthoritativePropertyFacts

    rec = sample_record()  # 2 BHK, Indiranagar, 10,500,000, active=True, version=1
    rec["area_sqft"] = 1250
    rec["amenities"] = ["Covered Parking", "Clubhouse"]
    rec["water_supply"] = "Cauvery Water Supply"

    facts = GroundingService().extract_authoritative_facts(rec)
    assert facts.bedrooms == 2
    assert facts.locality == "Indiranagar"
    assert facts.price_inr == Decimal("10500000")
    assert facts.area_sqft == Decimal("1250")

    # Verify server-side rendering
    bhk_str = render_fact_value("bedroom_count", facts)
    price_str = render_fact_value("property_price", facts)
    loc_str = render_fact_value("locality", facts)
    area_str = render_fact_value("area", facts)
    water_str = render_fact_value("water_supply", facts)

    assert bhk_str == "2 BHK"
    assert price_str == "₹1.05 Cr"
    assert loc_str == "Indiranagar"
    assert area_str == "1,250 sq ft"
    assert water_str == "Cauvery Water Supply"

    # Create content with explicit fact references
    fact_refs = [
        FactReference(fact_key="bedroom_count", crm_version=1, rendered_value=bhk_str),
        FactReference(fact_key="property_price", crm_version=1, rendered_value=price_str),
        FactReference(fact_key="locality", crm_version=1, rendered_value=loc_str),
        FactReference(fact_key="area", crm_version=1, rendered_value=area_str),
        FactReference(fact_key="water_supply", crm_version=1, rendered_value=water_str),
    ]

    val = GroundingService().validate_claims(
        facts,
        "",
        headline=f"Contemporary {bhk_str} Residence in {loc_str}",
        body=f"Spacious {area_str} living with {water_str}. Listed at {price_str}.",
        call_to_action="Contact our team to book your walkthrough.",
        hashtags=["#Indiranagar", "#2BHK", "#BangaloreRealEstate"],
        fact_references=fact_refs,
    )

    assert val.is_valid is True
    assert len(val.discrepancies) == 0
    assert val.verified_claims["crm_version"] == 1
    assert "rendered_facts" in val.verified_claims


# --- Codex Audit 5 Required Regressions ---

def test_codex5_audit_regressions_five_core_claims_on_2bhk():
    """Codex 5 Priority 1: Rigorous regression covering all five audit claims plus new audit cases:
    On a 2 BHK ₹1.05 Cr property in Indiranagar:
    1. 'Bedrooms: 5'
    2. 'Only INR 9,999'
    3. '500 sq ft rooftop terrace'
    4. '24/7 municipal water'
    5. 'Private sauna'
    6. '#Bedrooms5' (in hashtags)
    7. '#500SqFtRooftopTerrace' (in hashtags)
    8. 'Elevator serves all floors'
    9. 'Includes a private dock'
    Every single one MUST fail closed.
    """
    service = GroundingService()
    record = sample_record()  # 2 BHK, Indiranagar, 10,500,000 INR
    grounding = service.snapshot_from_dict(record)

    # 1. 'Bedrooms: 5'
    r1 = service.validate_claims(grounding, "Beautiful home with Bedrooms: 5 in prime location.")
    assert r1.is_valid is False
    assert any("5" in d and ("bedroom" in d.lower() or "bhk" in d.lower()) for d in r1.discrepancies)

    # 2. 'Only INR 9,999'
    r2 = service.validate_claims(grounding, "Unbelievable deal! Book now for Only INR 9,999.")
    assert r2.is_valid is False
    assert any("price discrepancy" in d.lower() and "9,999" in d for d in r2.discrepancies)

    # 3. '500 sq ft rooftop terrace'
    r3 = service.validate_claims(grounding, "Includes a stunning 500 sq ft rooftop terrace with city views.")
    assert r3.is_valid is False
    assert any("500 sq ft" in d.lower() or "terrace" in d.lower() for d in r3.discrepancies)

    # 4. '24/7 municipal water'
    r4 = service.validate_claims(grounding, "Equipped with 24/7 municipal water and prime infrastructure.")
    assert r4.is_valid is False
    assert any("water" in d.lower() for d in r4.discrepancies)

    # 5. 'Private sauna'
    r5 = service.validate_claims(grounding, "Relax in your Private sauna after a long day.")
    assert r5.is_valid is False
    assert any("sauna" in d.lower() for d in r5.discrepancies)

    # 6. '#Bedrooms5' (in hashtags)
    r6 = service.validate_claims(
        grounding,
        "Explore this prime Bengaluru home.",
        hashtags=["#Indiranagar", "#Bedrooms5", "#BangaloreRealEstate"],
    )
    assert r6.is_valid is False
    assert any("5" in d and ("bedroom" in d.lower() or "bhk" in d.lower()) for d in r6.discrepancies)

    # 7. '#500SqFtRooftopTerrace' (in hashtags)
    r7 = service.validate_claims(
        grounding,
        "Explore this prime Bengaluru home.",
        hashtags=["#Indiranagar", "#500SqFtRooftopTerrace", "#BangaloreRealEstate"],
    )
    assert r7.is_valid is False
    assert any("500" in d or "measurement" in d.lower() or "terrace" in d.lower() for d in r7.discrepancies)

    # 8. 'Elevator serves all floors'
    r8 = service.validate_claims(grounding, "Convenient luxury: Elevator serves all floors.")
    assert r8.is_valid is False
    assert any("elevator" in d.lower() for d in r8.discrepancies)

    # 9. 'Includes a private dock'
    r9 = service.validate_claims(grounding, "Waterfront luxury living: Includes a private dock.")
    assert r9.is_valid is False
    assert any("dock" in d.lower() for d in r9.discrepancies)


def test_codex5_audit_claims_in_nested_campaign_plan_fields():
    """Codex 5 Priority 1: Claims hidden in nested campaign plan fields (target_audience_spec, staged_launch_payload).
    validate_campaign_plan MUST catch them, and approval/export MUST reject them.
    """
    service = GroundingService()
    approval = GrowthApprovalService()
    record = sample_record()
    grounding = service.snapshot_from_dict(record)

    # Case A: Claim in nested target_audience_spec dictionary
    nested_spec = {
        "demographics": {"income": "High"},
        "targeting_notes": {
            "interest_group": "Buyers wanting private sauna and elevator serves all floors",
        },
    }
    bad_plan_a = CampaignPlanResponse(
        id=uuid4(),
        kit_id=uuid4(),
        property_id=grounding.property_id,
        objective="Lead Generation",
        target_audience_spec=nested_spec,
        recommended_placements=["Instagram Feed"],
        suggested_daily_budget=Decimal("1500.00"),
        suggested_duration_days=14,
        rationale="Standard budget recommendation for prime property.",
        staged_launch_payload={"launch_authorized": False},
        status="draft",
        version=1,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    val_a = service.validate_campaign_plan(grounding, bad_plan_a)
    assert val_a.is_valid is False
    assert any("sauna" in d.lower() or "elevator" in d.lower() for d in val_a.discrepancies)

    # Approval MUST fail
    from app.growth_engine.schemas import CampaignPlanDecision
    with pytest.raises(ApprovalServiceError) as exc_a:
        approval.decide_campaign_plan(
            bad_plan_a,
            CampaignPlanDecision(plan_id=bad_plan_a.id, expected_version=1, status="approved"),
            current_property_record=record,
            expected_hash=grounding.grounding_hash,
        )
    assert exc_a.value.code == "grounding_violation"

    # Case B: Claim in staged_launch_payload nested dictionary
    staged_payload = {
        "launch_authorized": False,
        "ad_creative_draft": {
            "headline": "Includes a private dock and Bedrooms: 5",
            "price_tag": "Only INR 9,999",
        },
    }
    bad_plan_b = CampaignPlanResponse(
        id=uuid4(),
        kit_id=uuid4(),
        property_id=grounding.property_id,
        objective="Lead Generation",
        target_audience_spec={"city": "Bengaluru"},
        recommended_placements=["Instagram Feed"],
        suggested_daily_budget=Decimal("1500.00"),
        suggested_duration_days=14,
        rationale="Balanced campaign plan.",
        staged_launch_payload=staged_payload,
        status="draft",
        version=1,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    val_b = service.validate_campaign_plan(grounding, bad_plan_b)
    assert val_b.is_valid is False
    assert any("dock" in d.lower() or "bedroom" in d.lower() or "9,999" in d for d in val_b.discrepancies)

    # Export of kit containing this approved plan MUST fail
    from app.growth_engine.schemas import KitSummaryResponse
    bad_plan_b.status = "approved"
    kit = KitSummaryResponse(
        id=uuid4(),
        property_id=grounding.property_id,
        brief_id=uuid4(),
        grounding=grounding,
        objective="Lead Generation",
        status="approved",
        campaign_plan=bad_plan_b,
        version=1,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    with pytest.raises(ApprovalServiceError) as exc_export:
        approval.export_kit_package(kit, current_property_record=record)
    assert exc_export.value.code == "export_grounding_violation"


def test_codex5_structured_copy_server_rendering_and_stale_rejection():
    """Codex 5 Priority 1: Structured copy model rendering and stale version rejection."""
    from app.growth_engine.schemas import FactReference, StructuredCopy
    from app.growth_engine.services.grounding_service import render_structured_copy, StaleFactReferenceError

    rec = sample_record()
    facts = GroundingService().extract_authoritative_facts(rec)

    from app.growth_engine.services.grounding_service import make_structured_copy
    struct_copy = make_structured_copy("instagram_feed", "standard_residence", GroundingService().snapshot_from_dict(rec))

    # Valid rendering matches facts
    hl, body, cta, tags = render_structured_copy(struct_copy, facts)
    assert "2 BHK" in hl
    assert "Indiranagar" in hl
    assert "₹1.05 Cr" in body
    assert any("#2BHK" in t for t in tags)

    # Stale reference (e.g. template rendered against CRM version 1, but current CRM version is 2)
    facts.crm_version = 2
    with pytest.raises(StaleFactReferenceError):
        render_structured_copy(struct_copy, facts)
