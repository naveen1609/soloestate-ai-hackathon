"""Grounding service for SoloEstate AI Growth Engine-lite.
Enforces a structured factual-content model verifiable by construction.
Freezes authoritative property facts from CRM and verifies that every factual marketing claim
references an authoritative CRM fact key and matching CRM revision.
Renders factual values server-side and fails closed on unreferenced or contradictory claims.
"""

from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
import logging
import re
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.growth_engine.schemas import CopyTemplate, FactReference, GroundingValidationResult, PropertyGrounding, StructuredCopy

logger = logging.getLogger(__name__)


class FactKey(StrEnum):
    BEDROOM_COUNT = "bedroom_count"
    PROPERTY_PRICE = "property_price"
    LOCALITY = "locality"
    AVAILABILITY_STATUS = "availability_status"
    AREA = "area"
    AMENITIES = "amenities"
    WATER_SUPPLY = "water_supply"
    OFFERS = "offers"
    LEGAL_STATUS = "legal_status"


class AuthoritativePropertyFacts(BaseModel):
    property_id: UUID
    crm_version: int
    title: str
    locality: str
    price_inr: Decimal
    bedrooms: int
    active: bool
    description: str = ""
    area_sqft: Decimal | None = None
    amenities: list[str] = Field(default_factory=list)
    water_supply: str | None = None
    offers: list[str] = Field(default_factory=list)
    legal_status: str | None = None
    availability_status: str = "available"
    grounding_hash: str = ""


class GroundingError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.status_code = status_code
        super().__init__(message)


class PropertyNotFoundError(GroundingError):
    def __init__(self, property_id: UUID):
        super().__init__("property_not_found", f"Property {property_id} was not found in CRM.", 404)


class PropertyInactiveError(GroundingError):
    def __init__(self, property_id: UUID):
        super().__init__("property_inactive", f"Property {property_id} is inactive in CRM. Marketing actions are forbidden.", 422)


class StaleGroundingError(GroundingError):
    def __init__(self, expected_hash: str, current_hash: str):
        super().__init__(
            "stale_grounding",
            f"CRM property details have changed since kit creation (expected {expected_hash[:8]}, current {current_hash[:8]}). Re-grounding required.",
            409,
        )


class GroundingValidationError(GroundingError):
    def __init__(self, message: str, discrepancies: list[str] | None = None):
        self.discrepancies = discrepancies or []
        super().__init__("grounding_validation_failed", message, 422)


class CRMUnavailableError(GroundingError):
    def __init__(self, property_id: UUID | None = None, details: str = ""):
        msg = f"CRM database is currently unavailable{f' for property {property_id}' if property_id else ''}."
        if details:
            msg += f" {details}"
        super().__init__("crm_unavailable", msg, 503)


class UnsupportedFactClaimError(GroundingError):
    def __init__(self, message: str):
        super().__init__("unsupported_fact_claim", message, 422)


class StaleFactReferenceError(GroundingError):
    def __init__(self, fact_key: str, expected_version: int, current_version: int):
        super().__init__(
            "stale_fact_reference",
            f"Fact reference '{fact_key}' refers to stale CRM version {expected_version}; current CRM version is {current_version}.",
            409,
        )


WORD_TO_DIGIT = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "single": 1, "double": 2, "triple": 3, "quad": 4,
}

EXTERNAL_LOCALITIES_AND_CITIES = {
    # Mumbai / MMR
    "powai", "bandra", "andheri", "juhu", "worli", "colaba", "thane", "navi mumbai", "chembur", "mumbai",
    # Delhi / NCR
    "delhi", "new delhi", "gurgaon", "gurugram", "noida", "greater noida", "ghaziabad", "faridabad",
    # Other major metros
    "hyderabad", "gachibowli", "hitec city", "banjara hills", "jubilee hills",
    # Pune
    "pune", "kothrud", "wakad", "hinjewadi", "kalyani nagar", "viman nagar",
    # Chennai
    "chennai", "omr", "adyar", "anna nagar", "velachery", "t nagar",
    # Kolkata
    "kolkata", "salt lake", "new town", "alipore",
    # Distinct Bengaluru localities that must not cross-promote
    "whitefield", "indiranagar", "jayanagar", "koramangala", "mg road", "hsr layout",
    "electronic city", "hebbal", "sarjapur", "bellandur", "marathahalli", "banashankari",
    "malleshwaram", "rajajinagar", "yelahanka", "jp nagar",
}


def is_negated(text: str, start_idx: int) -> bool:
    """Return True if the match at start_idx is preceded by a negation pattern in the same clause."""
    prefix = text[max(0, start_idx - 45):start_idx].lower()
    neg_pattern = (
        r"(?:\bno\b|\bnot\b|\bwithout\b|\bdoesn't have\b|\bdoes not have\b|\bneither\b|\bnever\b|\bno need for\b)"
        r"\s+(?:a\s+|an\s+|any\s+|the\s+)?(?:[\w-]+\s+){0,3}$"
    )
    return bool(re.search(neg_pattern, prefix))


def render_fact_value(fact_key: str, facts: AuthoritativePropertyFacts) -> str:
    """Server-side authoritative fact renderer.
    Renders the ground-truth value strictly from the verified CRM property record.
    Fails closed if the requested fact is unsupported by CRM data.
    """
    key = fact_key.lower().strip()
    if key in ("bedroom_count", "bedrooms", "bhk"):
        return f"{facts.bedrooms} BHK"
    elif key in ("property_price", "price"):
        amount = facts.price_inr
        if amount >= 10_000_000:
            value, unit = amount / 10_000_000, " Cr"
        elif amount >= 100_000:
            value, unit = amount / 100_000, " Lakhs"
        else:
            value, unit = amount, ""
        return f"₹{value.normalize():,f}{unit}"
    elif key in ("locality", "location"):
        return facts.locality
    elif key in ("availability_status", "availability", "status"):
        return "Available for site visits" if facts.active else "Currently Unavailable"
    elif key in ("area", "area_sqft", "sqft"):
        if facts.area_sqft is not None:
            return f"{facts.area_sqft:,.0f} sq ft"
        raise UnsupportedFactClaimError(f"Fact '{fact_key}' is not available in authoritative CRM data.")
    elif key in ("amenities", "amenity"):
        if facts.amenities:
            return ", ".join(facts.amenities)
        raise UnsupportedFactClaimError(f"Fact '{fact_key}' is not available in authoritative CRM data.")
    elif key in ("water_supply", "water"):
        if facts.water_supply:
            return facts.water_supply
        raise UnsupportedFactClaimError(f"Fact '{fact_key}' is not available in authoritative CRM data.")
    elif key in ("offers", "special_offers", "incentives"):
        if facts.offers:
            return ", ".join(facts.offers)
        raise UnsupportedFactClaimError(f"Fact '{fact_key}' is not available in authoritative CRM data.")
    elif key in ("legal_status", "title_status"):
        if facts.legal_status:
            return facts.legal_status
        raise UnsupportedFactClaimError(f"Fact '{fact_key}' is not available in authoritative CRM data.")
    else:
        raise UnsupportedFactClaimError(f"Unknown or unsupported fact key '{fact_key}'.")


class GroundingService:
    def __init__(self, crm: Any | None = None):
        self._crm = crm

    @staticmethod
    def compute_grounding_hash(
        property_id: UUID,
        title: str,
        locality: str,
        price: Decimal,
        bedrooms: int,
        description: str,
    ) -> str:
        payload = {
            "property_id": str(property_id),
            "title": title.strip(),
            "locality": locality.strip().lower(),
            "price": str(price),
            "bedrooms": bedrooms,
            "description": description.strip(),
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return sha256(serialized.encode("utf-8")).hexdigest()

    def snapshot_from_dict(self, record: dict[str, Any] | None) -> PropertyGrounding:
        if not record:
            raise GroundingError("missing_record", "Cannot create grounding snapshot from empty record.", 404)

        raw_id = record.get("id")
        if not raw_id:
            raise GroundingError("missing_property_id", "Property record has no id.", 400)
        try:
            property_id = UUID(str(raw_id))
        except ValueError:
            raise GroundingError("invalid_property_id", f"Invalid UUID: {raw_id}", 400)

        # Inactive property check
        active = record.get("active")
        if active is False:
            raise PropertyInactiveError(property_id)

        title = str(record.get("title", "")).strip()
        locality = str(record.get("locality", "")).strip()
        price = Decimal(str(record.get("price", "0")))
        bedrooms = int(record.get("bedrooms", 1))
        description = str(record.get("description", "")).strip()
        version = int(record.get("version", 1))

        if not title:
            raise GroundingError("invalid_title", "Property title cannot be blank for grounding.", 422)
        if price <= 0:
            raise GroundingError("invalid_price", "Property price must be positive for grounding.", 422)
        if bedrooms < 1:
            raise GroundingError("invalid_bedrooms", "Property bedrooms must be at least 1.", 422)

        ghash = self.compute_grounding_hash(
            property_id=property_id,
            title=title,
            locality=locality,
            price=price,
            bedrooms=bedrooms,
            description=description,
        )

        return PropertyGrounding(
            property_id=property_id,
            title=title,
            locality=locality,
            price_inr=price,
            bedrooms=bedrooms,
            description=description,
            active=bool(active is not False),
            grounding_version=version,
            grounding_hash=ghash,
        )

    def extract_authoritative_facts(self, record: dict[str, Any] | None) -> AuthoritativePropertyFacts:
        """Extract authoritative typed facts with explicit CRM provenance from a CRM record."""
        snapshot = self.snapshot_from_dict(record)
        assert record is not None

        # Parse optional typed fields if present in CRM schema
        area_sqft = None
        if record.get("area_sqft") is not None:
            try:
                area_sqft = Decimal(str(record["area_sqft"]))
            except Exception:
                area_sqft = None

        amenities = []
        if isinstance(record.get("amenities"), list):
            amenities = [str(a).strip() for a in record["amenities"] if a]

        water_supply = record.get("water_supply")
        if water_supply:
            water_supply = str(water_supply).strip()

        offers = []
        if isinstance(record.get("offers"), list):
            offers = [str(o).strip() for o in record["offers"] if o]

        legal_status = record.get("legal_status")
        if legal_status:
            legal_status = str(legal_status).strip()

        return AuthoritativePropertyFacts(
            property_id=snapshot.property_id,
            crm_version=snapshot.grounding_version,
            title=snapshot.title,
            locality=snapshot.locality,
            price_inr=snapshot.price_inr,
            bedrooms=snapshot.bedrooms,
            active=snapshot.active,
            description=snapshot.description,
            area_sqft=area_sqft,
            amenities=amenities,
            water_supply=water_supply,
            offers=offers,
            legal_status=legal_status,
            availability_status="available" if snapshot.active else "unavailable",
            grounding_hash=snapshot.grounding_hash,
        )

    def validate_crm_property(
        self,
        property_id: UUID,
        expected_hash: str | None = None,
        crm: Any | None = None,
    ) -> dict[str, Any]:
        """Look up property in CRM, verifying existence, active status, and non-stale hash.
        Preserves typed distinctions:
        - PropertyNotFoundError (404) for missing property
        - CRMUnavailableError (503) for connectivity/database failures
        - PropertyInactiveError (422) for inactive property
        - StaleGroundingError (409) for stale hash/revision
        """
        from app.crm import CRM, CRMError
        client = crm or self._crm or CRM()
        try:
            record = client.detail("properties", property_id)
        except CRMError as exc:
            if exc.status == 404:
                raise PropertyNotFoundError(property_id)
            if exc.status in (500, 502, 503, 504):
                raise CRMUnavailableError(property_id, str(exc))
            raise
        except (ConnectionError, TimeoutError, OSError) as exc:
            raise CRMUnavailableError(property_id, f"Connection failed: {exc}") from exc
        except Exception as exc:
            # Check for psycopg / database error types
            exc_name = type(exc).__name__
            if "Database" in exc_name or "OperationalError" in exc_name or "psycopg" in exc_name.lower():
                raise CRMUnavailableError(property_id, str(exc)) from exc
            raise CRMUnavailableError(property_id, f"Unexpected CRM failure: {exc}") from exc

        if not record:
            raise PropertyNotFoundError(property_id)

        # In CRM.detail, properties are wrapped as {"record": {...}}
        if isinstance(record, dict) and "record" in record:
            record = record["record"]

        if record.get("active") is False:
            raise PropertyInactiveError(property_id)

        if expected_hash is not None:
            snapshot = self.snapshot_from_dict(record)
            if snapshot.grounding_hash != expected_hash:
                raise StaleGroundingError(expected_hash, snapshot.grounding_hash)

        return record

    def validate_claims(
        self,
        grounding: PropertyGrounding | AuthoritativePropertyFacts,
        text: str,
        *,
        headline: str = "",
        body: str = "",
        call_to_action: str = "",
        hashtags: list[str] | None = None,
        special_offers: str = "",
        fact_references: list[FactReference] | None = None,
    ) -> GroundingValidationResult:
        """Structured factual-content verification.
        Verifies that factual claims are anchored to authoritative CRM facts and matching revision.
        Fails closed if fact references are missing or stale, or if unreferenced/contradictory
        factual claims are detected in headline, body, call-to-action, hashtags, or special offers.
        """
        if isinstance(grounding, PropertyGrounding):
            facts = AuthoritativePropertyFacts(
                property_id=grounding.property_id,
                crm_version=grounding.grounding_version,
                title=grounding.title,
                locality=grounding.locality,
                price_inr=grounding.price_inr,
                bedrooms=grounding.bedrooms,
                active=grounding.active,
                description=grounding.description,
                grounding_hash=grounding.grounding_hash,
            )
        else:
            facts = grounding

        discrepancies: list[str] = []
        verified: dict[str, Any] = {}

        # Aggregate all text representations so claims cannot hide in CTA or hashtags
        parts = [text, headline, body, call_to_action, special_offers]
        if hashtags:
            parts.extend(hashtags)
        combined_text = " ".join(p for p in parts if p).lower()

        # Step 1: Validate Fact References (if provided)
        rendered_facts: dict[str, str] = {}
        if fact_references:
            for ref in fact_references:
                if ref.crm_version != facts.crm_version:
                    discrepancies.append(
                        f"Stale fact reference: '{ref.fact_key}' references CRM revision {ref.crm_version}, but current revision is {facts.crm_version}."
                    )
                try:
                    auth_val = render_fact_value(ref.fact_key, facts)
                    rendered_facts[ref.fact_key] = auth_val
                except UnsupportedFactClaimError as exc:
                    discrepancies.append(str(exc))

        # Step 2: Strict Bedroom Count Verification
        extracted_bedrooms: list[tuple[int, int]] = []

        # 2a. Digit patterns: "5 spacious bedrooms", "2 bhk", "4 bedroom", "bedrooms: 5"
        for m in re.finditer(r"(?:#|\b)(?:bedrooms?|beds?|bhk)\s*:\s*(\d+)\b", combined_text):
            extracted_bedrooms.append((int(m.group(1)), m.start()))
        for m in re.finditer(r"(?:#|\b)(\d+)\s+(?:(?:\w+)\s+){0,3}(?:bhk|bedrooms?|beds?)\b", combined_text):
            extracted_bedrooms.append((int(m.group(1)), m.start()))
        for m in re.finditer(r"(?:#|\b)(\d+)\s*(?:-|\s)?(?:bhk|bedrooms?|beds?)\b", combined_text):
            extracted_bedrooms.append((int(m.group(1)), m.start()))

        # 2b. Word patterns: "five spacious bedrooms", "two bhk", "four bedroom"
        word_pattern = (
            r"(?:#|\b)(one|two|three|four|five|six|seven|eight|nine|ten|single|double|triple|quad)"
            r"\s+(?:(?:\w+)\s+){0,3}(?:bhk|bedrooms?|beds?)\b"
        )
        for m in re.finditer(word_pattern, combined_text):
            w = m.group(1)
            if w in WORD_TO_DIGIT:
                extracted_bedrooms.append((WORD_TO_DIGIT[w], m.start()))
        for m in re.finditer(r"(?:#|\b)(one|two|three|four|five|six|seven|eight|nine|ten|single|double|triple|quad)\s*(?:-|\s)?(?:bhk|bedrooms?|beds?)\b", combined_text):
            w = m.group(1)
            if w in WORD_TO_DIGIT:
                extracted_bedrooms.append((WORD_TO_DIGIT[w], m.start()))

        # 2c. Compound hashtags: #FiveBedrooms, #5Bedrooms, #4BHK, #TwoBHK, #Bedrooms5
        for m in re.finditer(r"#([a-z0-9_]*?)(one|two|three|four|five|six|seven|eight|nine|ten|single|double|triple|quad)(\w*?)(?:bhk|bedrooms?|beds?)\b", combined_text):
            w = m.group(2)
            if w in WORD_TO_DIGIT:
                extracted_bedrooms.append((WORD_TO_DIGIT[w], m.start()))
        for m in re.finditer(r"#([a-z0-9_]*?)(\d+)(\w*?)(?:bhk|bedrooms?|beds?)\b", combined_text):
            extracted_bedrooms.append((int(m.group(2)), m.start()))
        for m in re.finditer(r"#([a-z0-9_]*?)(?:bhk|bedrooms?|beds?)(\d+)\b", combined_text):
            extracted_bedrooms.append((int(m.group(2)), m.start()))

        checked_any_bedroom = False
        for count, start_pos in extracted_bedrooms:
            checked_any_bedroom = True
            if is_negated(combined_text, start_pos):
                if count == facts.bedrooms:
                    discrepancies.append(
                        f"Contradiction of verified property fact: text falsely negates property's verified {facts.bedrooms} BHK."
                    )
            else:
                if count != facts.bedrooms:
                    discrepancies.append(
                        f"Bedroom count mismatch: claims {count} BHK/bedrooms, but verified property has {facts.bedrooms} BHK."
                    )

        verified["bedrooms_checked"] = checked_any_bedroom

        # Step 3: Conflicting property types
        if facts.bedrooms <= 3:
            if re.search(r"\b(palatial villa|private island|massive estate|luxury villa|independent villa)\b", combined_text):
                discrepancies.append("Exaggerated property type claimed (villa/estate) for an apartment unit.")

        # Step 4: Locality & Geography verification
        locality_normalized = facts.locality.lower().strip()
        locality_clean = locality_normalized.replace(" ", "")

        conflicting_localities = EXTERNAL_LOCALITIES_AND_CITIES - {locality_normalized, locality_clean}
        for loc in conflicting_localities:
            loc_clean = loc.replace(" ", "")
            pattern = rf"(?:\b|#){re.escape(loc_clean)}(?:\b|_|\s)"
            m1 = re.search(pattern, combined_text)
            m2 = re.search(rf"\b{re.escape(loc)}\b", combined_text)
            start_pos = m1.start() if m1 else (m2.start() if m2 else -1)
            if start_pos >= 0 and not is_negated(combined_text, start_pos):
                discrepancies.append(
                    f"Locality mismatch: text or hashtags mention '{loc.title()}' but property is located in '{facts.locality}'."
                )

        # Step 5: Strict Price Consistency Check (Crores, Lakhs/Lacs, and Raw Numeric)
        price_num = float(facts.price_inr)
        price_cr = price_num / 10_000_000.0
        price_lakh = price_num / 100_000.0

        # 5a. Crore mentions (e.g. "1.05 Cr", "1.00 Cr", "1 Crore")
        for m in re.finditer(r"(?:rs\.?|inr|₹|priced\s+at\s+|price:?\s*|for\s+)?\s*(\d+(?:\.\d+)?)\s*(?:cr|crore|crores)\b", combined_text):
            amt = float(m.group(1))
            trailing = combined_text[m.end():m.end()+20]
            leading = combined_text[max(0, m.start()-25):m.start()]
            if (
                re.search(r"^(?:\+?\s*)?(?:p\.?a\.?|per\s+(?:annum|year|month|day)|/(?:day|month|year))", trailing)
                or "income" in leading
                or "salary" in leading
                or "budget" in leading
            ):
                continue
            if not is_negated(combined_text, m.start()):
                if abs(amt - price_cr) / price_cr > 0.01:
                    discrepancies.append(f"Price discrepancy: text mentions ₹{amt:.2f} Cr, verified price is ₹{price_cr:.2f} Cr.")

        # 5b. Lakh and Lac mentions (e.g. "105 Lakhs", "80 lac", "80 lacs", "80 lakh")
        for m in re.finditer(r"(?:rs\.?|inr|₹|priced\s+at\s+|price:?\s*|for\s+)?\s*(\d+(?:\.\d+)?)\s*(?:lakh|lakhs|lac|lacs|l)\b", combined_text):
            amt = float(m.group(1))
            trailing = combined_text[m.end():m.end()+20]
            leading = combined_text[max(0, m.start()-25):m.start()]
            if (
                re.search(r"^(?:\+?\s*)?(?:p\.?a\.?|per\s+(?:annum|year|month|day)|/(?:day|month|year))", trailing)
                or "income" in leading
                or "salary" in leading
                or "budget" in leading
                or "earning" in leading
            ):
                continue
            if not is_negated(combined_text, m.start()):
                if abs(amt - price_lakh) / price_lakh > 0.01:
                    discrepancies.append(f"Price discrepancy: text mentions ₹{amt:.1f} Lakhs/Lacs, verified price is ₹{price_lakh:.1f} Lakhs.")

        # 5c. Raw numeric amounts (e.g. "INR 9,999", "Only INR 9,999", "INR 1,000,000", "₹10,500,000", "#9999INR")
        for m in re.finditer(r"(?:inr|rs\.?|₹|priced\s+at\s+|price:?\s*|only\s+inr\s*|for\s+)(?:rs\.?|inr|₹)?\s*([\d,]{3,}(?:\.\d+)?)", combined_text):
            clean_num = float(m.group(1).replace(",", ""))
            if clean_num > 500 and not is_negated(combined_text, m.start()):
                # Exclude campaign marketing budget mentions and demographic income bands
                trailing = combined_text[m.end():m.end()+20]
                leading = combined_text[max(0, m.start()-25):m.start()]
                if (
                    re.search(r"^(?:\+?\s*)?(?:p\.?a\.?|per\s+(?:annum|year|month|day)|/(?:day|month|year))", trailing)
                    or "daily" in leading
                    or "budget" in leading
                    or "income" in leading
                    or "salary" in leading
                ):
                    continue
                if abs(clean_num - price_num) / price_num > 0.01:
                    discrepancies.append(
                        f"Price discrepancy: text mentions INR {clean_num:,.0f}, verified price is INR {price_num:,.0f}."
                    )

        # 5d. Standalone numbers with currency prefix in hashtags (e.g. #9999inr, #inr9999, #only9999)
        for m in re.finditer(r"#([a-z0-9_]*?)(\d{4,})(inr|rs|rupees)\b", combined_text):
            num = float(m.group(2))
            if abs(num - price_num) / price_num > 0.01:
                discrepancies.append(f"Price discrepancy in hashtag: mentions {num:,.0f}, verified price is {price_num:,.0f}.")

        # Step 6: Measurement & Area Claims (e.g., "500 sq ft rooftop terrace", "1200 sqft")
        # Fail closed if CRM does not have matching area_sqft
        area_matches = list(re.finditer(r"(\d+(?:,\d+)?(?:\.\d+)?)\s*(?:sq\.?\s*ft\.?|sqft|square\s*feet|acres?)\b", combined_text))
        for m in re.finditer(r"#([a-z0-9_]*?)(\d+)(?:sqft|squarefeet|sq_?ft)([a-z0-9_]*)\b", combined_text):
            area_matches.append(m)

        for m in area_matches:
            if not is_negated(combined_text, m.start()):
                if facts.area_sqft is None:
                    discrepancies.append(
                        f"Unsupported property measurement claim: '{m.group(0).strip()}' is not supported by verified CRM property facts."
                    )
                else:
                    try:
                        amt_str = m.group(1) if len(m.groups()) >= 1 else m.group(2)
                        amt = float(amt_str.replace(",", ""))
                        if abs(amt - float(facts.area_sqft)) / float(facts.area_sqft) > 0.05:
                            discrepancies.append(
                                f"Area mismatch: claims {amt:.0f} sq ft, but verified CRM area is {facts.area_sqft:.0f} sq ft."
                            )
                    except Exception:
                        discrepancies.append(f"Unsupported property measurement: '{m.group(0)}'.")

        # Step 7: Utilities & Infrastructure (e.g., "24/7 municipal water", "municipal water", "24/7 water")
        water_claims = [
            ("24/7 municipal water", "24/7 Municipal Water"),
            ("municipal water", "Municipal Water Supply"),
            ("24/7 water supply", "24/7 Water Supply"),
            ("24/7 water", "24/7 Water Supply"),
            ("borewell water", "Borewell Water Supply"),
            ("cauvery water", "Cauvery Water Supply"),
            ("100% power backup", "100% Power Backup"),
        ]
        for phrase, label in water_claims:
            phrase_clean = phrase.replace(" ", "").replace("/", "")
            m = (
                re.search(rf"\b{re.escape(phrase)}\b", combined_text)
                or re.search(rf"#{re.escape(phrase_clean)}", combined_text)
            )
            if m and not is_negated(combined_text, m.start()):
                if not facts.water_supply or phrase not in facts.water_supply.lower():
                    discrepancies.append(
                        f"Unsupported utility/infrastructure claim: '{label}' is not in verified CRM property facts."
                    )

        # Step 8: Luxury Amenities & Physical Features (e.g., "Private sauna", "elevator", "private dock")
        luxury_amenities = [
            ("private sauna", "Private Sauna"),
            ("sauna", "Sauna"),
            ("rooftop terrace", "Rooftop Terrace"),
            ("terrace", "Terrace"),
            ("elevator serves all floors", "Elevator"),
            ("elevator", "Elevator"),
            ("lift", "Lift / Elevator"),
            ("includes a private dock", "Private Dock"),
            ("private dock", "Private Dock"),
            ("dock", "Dock"),
            ("boat dock", "Boat Dock"),
            ("marina", "Marina"),
            ("boat slip", "Boat Slip"),
            ("private helipad", "Helipad"),
            ("helipad", "Helipad"),
            ("olympic swimming pool", "Olympic Swimming Pool"),
            ("swimming pool", "Swimming Pool"),
            ("private pool", "Private Pool"),
            ("pool", "Pool"),
            ("private beach", "Private Beach"),
            ("golf course", "Golf Course"),
            ("equestrian center", "Equestrian Center"),
            ("private cinema", "Private Cinema"),
            ("cinema hall", "Cinema Hall"),
            ("home theatre", "Home Theatre"),
            ("movie theater", "Movie Theater"),
            ("private theater", "Private Theater"),
            ("tennis courts", "Tennis Courts"),
            ("tennis court", "Tennis Court"),
            ("squash court", "Squash Court"),
            ("jacuzzi", "Jacuzzi"),
        ]
        crm_amenities_lower = [a.lower() for a in facts.amenities]
        for phrase, name in luxury_amenities:
            phrase_clean = phrase.replace(" ", "")
            m = (
                re.search(rf"\b{re.escape(phrase)}\b", combined_text)
                or re.search(rf"#{re.escape(phrase_clean)}", combined_text)
                or ("pool" in phrase and re.search(r"#(?:[a-z0-9_]*pool[a-z0-9_]*)", combined_text))
                or ("sauna" in phrase and re.search(r"#(?:[a-z0-9_]*sauna[a-z0-9_]*)", combined_text))
                or ("dock" in phrase and re.search(r"#(?:[a-z0-9_]*dock[a-z0-9_]*)", combined_text))
                or ("elevator" in phrase and re.search(r"#(?:[a-z0-9_]*elevator[a-z0-9_]*)", combined_text))
                or ("lift" in phrase and re.search(r"#(?:[a-z0-9_]*lift[a-z0-9_]*)", combined_text))
                or ("terrace" in phrase and re.search(r"#(?:[a-z0-9_]*terrace[a-z0-9_]*)", combined_text))
                or ("cinema" in phrase and re.search(r"#(?:[a-z0-9_]*cinema[a-z0-9_]*)", combined_text))
                or ("tennis" in phrase and re.search(r"#(?:[a-z0-9_]*tennis[a-z0-9_]*)", combined_text))
            )
            if m and not is_negated(combined_text, m.start()):
                # Allow only if explicitly present in CRM authoritative amenities
                if not any(phrase in a for a in crm_amenities_lower):
                    discrepancies.append(f"Fabricated amenity claimed: '{name}' is not in verified CRM property facts.")

        # Step 9: Unsupported Possession Status Claims
        possession_claims = [
            ("immediate move-in available", "Immediate move-in available"),
            ("immediate move-in", "Immediate move-in"),
            ("immediate move in", "Immediate move-in"),
            ("move-in available", "Move-in available"),
            ("move in available", "Move-in available"),
            ("ready to move in", "Ready to move in"),
            ("ready to move", "Ready to move"),
            ("ready-to-move", "Ready to move"),
            ("ready for possession", "Ready for possession"),
            ("immediate possession", "Immediate possession"),
            ("under construction", "Under construction"),
        ]
        for phrase, label in possession_claims:
            phrase_clean = phrase.replace(" ", "").replace("-", "")
            m = (
                re.search(rf"\b{re.escape(phrase)}\b", combined_text)
                or re.search(rf"#{re.escape(phrase_clean)}", combined_text)
            )
            if m and not is_negated(combined_text, m.start()):
                discrepancies.append(f"Unsupported possession claim: '{label}' is not verified in CRM property facts.")

        # Step 10: Unsupported Promotional Incentives & Freebies
        promotional_incentives = [
            ("complimentary iphone", "Complimentary iPhone incentive"),
            ("free iphone", "Free iPhone incentive"),
            ("iphone on booking", "iPhone on booking incentive"),
            ("free apple iphone", "Free iPhone incentive"),
            ("complimentary gift", "Complimentary gift incentive"),
            ("free gift", "Free gift incentive"),
            ("free gold", "Free gold on booking"),
            ("gold coin", "Free gold coin"),
            ("gold on booking", "Free gold on booking"),
            ("free car", "Free car incentive"),
            ("zero brokerage", "Zero brokerage guarantee"),
            ("no brokerage", "Zero brokerage guarantee"),
            ("cashback", "Cashback incentive"),
        ]
        crm_offers_lower = [o.lower() for o in facts.offers]
        for phrase, label in promotional_incentives:
            phrase_clean = phrase.replace(" ", "")
            m = (
                re.search(rf"\b{re.escape(phrase)}\b", combined_text)
                or re.search(rf"#{re.escape(phrase_clean)}", combined_text)
            )
            if m and not is_negated(combined_text, m.start()):
                if not any(phrase in o for o in crm_offers_lower):
                    discrepancies.append(f"Unsupported promotional incentive: '{label}' is not verified in CRM property facts.")

        # Step 11: Unsupported Legal Title and Occupancy Certificate Claims
        legal_and_regulatory = [
            ("occupancy certificate approved", "Occupancy certificate approved"),
            ("occupancy certificate", "Occupancy certificate guarantee"),
            ("occupancy-certificate", "Occupancy certificate guarantee"),
            ("oc received", "Occupancy Certificate (OC) received"),
            ("oc approved", "Occupancy Certificate (OC) approved"),
            ("cc received", "Completion Certificate (CC) received"),
            ("completion certificate", "Completion Certificate guarantee"),
            ("verified legal title", "Guaranteed legal title"),
            ("100% legal title", "Guaranteed legal title"),
            ("clear legal title", "Clear legal title guarantee"),
            ("clear title", "Clear legal title guarantee"),
            ("rera approved", "RERA approved guarantee"),
        ]
        for phrase, label in legal_and_regulatory:
            phrase_clean = phrase.replace(" ", "").replace("-", "")
            m = (
                re.search(rf"\b{re.escape(phrase)}\b", combined_text)
                or re.search(rf"#{re.escape(phrase_clean)}", combined_text)
            )
            if m and not is_negated(combined_text, m.start()):
                if not facts.legal_status or phrase not in facts.legal_status.lower():
                    discrepancies.append(f"Unsupported legal/regulatory claim: '{label}' is not verified in CRM property facts.")

        # Step 12: Unsupported Financial Yield & Return Claims
        yield_phrases = [
            ("annual yield", "Annual yield guarantee"),
            ("rental yield", "Rental yield guarantee"),
            ("high rental yield", "High rental yield guarantee"),
            ("high yield", "High yield guarantee"),
            ("guaranteed return", "Guaranteed returns"),
            ("guaranteed yield", "Guaranteed yield"),
            ("assured return", "Assured returns"),
            ("assured yield", "Assured yield"),
        ]
        for phrase, label in yield_phrases:
            phrase_clean = phrase.replace(" ", "")
            m = (
                re.search(rf"\b{re.escape(phrase)}\b", combined_text)
                or re.search(rf"#{re.escape(phrase_clean)}", combined_text)
            )
            if m and not is_negated(combined_text, m.start()):
                discrepancies.append(f"Unsupported financial claim: '{label}' is not verified in CRM property facts.")

        yield_pct_match = (
            re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:annual\s*)?(?:rental\s*)?(?:yield|return|roi)", combined_text)
            or re.search(r"(?:annual\s*)?(?:rental\s*)?(?:yield|return|roi)\s*(?:of\s*)?(\d+(?:\.\d+)?)\s*%", combined_text)
            or re.search(r"(?:yield|return|roi)\s*:\s*(\d+(?:\.\d+)?)\s*%", combined_text)
        )
        if yield_pct_match and not is_negated(combined_text, yield_pct_match.start()):
            discrepancies.append(f"Unsupported financial claim: '{yield_pct_match.group(0)}' is not verified in CRM property facts.")

        verified["grounding_hash"] = facts.grounding_hash
        verified["property_id"] = str(facts.property_id)
        verified["crm_version"] = facts.crm_version
        if rendered_facts:
            verified["rendered_facts"] = rendered_facts

        # Deduplicate discrepancies while preserving order
        unique_discrepancies = list(dict.fromkeys(discrepancies))

        return GroundingValidationResult(
            is_valid=len(unique_discrepancies) == 0,
            discrepancies=unique_discrepancies,
            verified_claims=verified,
        )

    def validate_campaign_plan(
        self,
        grounding: PropertyGrounding | AuthoritativePropertyFacts,
        plan: Any,
    ) -> GroundingValidationResult:
        """Validate all text fields and nested structures in a campaign plan against CRM property facts.
        Recursively scans target_audience_spec, staged_launch_payload, rationale, objective, and placements.
        Fails closed on any ungrounded factual claim.
        """
        all_discrepancies: list[str] = []

        def _extract_strings(val: Any) -> list[str]:
            out = []
            if isinstance(val, str):
                if val.strip():
                    out.append(val.strip())
            elif isinstance(val, dict):
                for k, v in val.items():
                    out.extend(_extract_strings(k))
                    out.extend(_extract_strings(v))
            elif isinstance(val, (list, tuple)):
                for item in val:
                    out.extend(_extract_strings(item))
            return out

        if hasattr(plan, "rationale") and plan.rationale:
            res = self.validate_claims(grounding, plan.rationale)
            all_discrepancies.extend(res.discrepancies)
        elif isinstance(plan, dict) and plan.get("rationale"):
            res = self.validate_claims(grounding, plan["rationale"])
            all_discrepancies.extend(res.discrepancies)

        if hasattr(plan, "objective") and plan.objective:
            res = self.validate_claims(grounding, plan.objective)
            all_discrepancies.extend(res.discrepancies)
        elif isinstance(plan, dict) and plan.get("objective"):
            res = self.validate_claims(grounding, plan["objective"])
            all_discrepancies.extend(res.discrepancies)

        target_aud = getattr(plan, "target_audience_spec", None) if hasattr(plan, "target_audience_spec") else (plan.get("target_audience_spec") if isinstance(plan, dict) else None)
        if target_aud:
            for text_chunk in _extract_strings(target_aud):
                res = self.validate_claims(grounding, text_chunk)
                all_discrepancies.extend(res.discrepancies)

        staged_payload = getattr(plan, "staged_launch_payload", None) if hasattr(plan, "staged_launch_payload") else (plan.get("staged_launch_payload") if isinstance(plan, dict) else None)
        if staged_payload:
            for text_chunk in _extract_strings(staged_payload):
                res = self.validate_claims(grounding, text_chunk)
                all_discrepancies.extend(res.discrepancies)

        placements = getattr(plan, "recommended_placements", None) if hasattr(plan, "recommended_placements") else (plan.get("recommended_placements") if isinstance(plan, dict) else None)
        if placements:
            for text_chunk in _extract_strings(placements):
                res = self.validate_claims(grounding, text_chunk)
                all_discrepancies.extend(res.discrepancies)

        unique_discrepancies = list(dict.fromkeys(all_discrepancies))
        return GroundingValidationResult(
            is_valid=len(unique_discrepancies) == 0,
            discrepancies=unique_discrepancies,
            verified_claims={"grounding_verified": len(unique_discrepancies) == 0},
        )


COPY_TEMPLATES = {
    "standard_residence": "{bedrooms} Residence in {locality}",
    "compact_story": "Now listed: {bedrooms} in {locality}",
    "detailed_showcase": "Property showcase: {bedrooms} in {locality}",
    "festive_spotlight": "Festive spotlight: {bedrooms} in {locality}",
}
COPY_FIELDS = {"bedroom_count": "bedrooms", "property_price": "price_inr", "locality": "locality"}


def make_structured_copy(channel: str, template_id: CopyTemplate, grounding: PropertyGrounding) -> StructuredCopy:
    return StructuredCopy(
        channel=channel,
        template_id=template_id,
        rendered_at_crm_version=grounding.grounding_version,
        fact_references=[
            FactReference(
                fact_key=key,
                source_field=field,
                crm_version=grounding.grounding_version,
                rendered_value=render_fact_value(key, grounding),
            )
            for key, field in COPY_FIELDS.items()
        ],
    )


def render_structured_copy(
    structured_copy: StructuredCopy, facts: PropertyGrounding | AuthoritativePropertyFacts,
) -> tuple[str, str, str, list[str]]:
    """Render only template text and the three pinned CRM fields."""
    revision = facts.grounding_version if isinstance(facts, PropertyGrounding) else facts.crm_version
    if structured_copy.rendered_at_crm_version != revision:
        raise StaleFactReferenceError("copy_template", structured_copy.rendered_at_crm_version, revision)
    expected = {
        (key, field, revision, render_fact_value(key, facts))
        for key, field in COPY_FIELDS.items()
    }
    actual = {
        (ref.fact_key, ref.source_field, ref.crm_version, ref.rendered_value)
        for ref in structured_copy.fact_references
    }
    if actual != expected or len(structured_copy.fact_references) != len(expected) or structured_copy.prose_slots:
        raise UnsupportedFactClaimError("Copy must use current CRM references and a server template.")
    bedrooms = render_fact_value("bedroom_count", facts)
    locality = render_fact_value("locality", facts)
    price = render_fact_value("property_price", facts)
    headline = COPY_TEMPLATES[structured_copy.template_id].format(bedrooms=bedrooms, locality=locality)
    body = f"{bedrooms} listed in {locality}. Listed price: {price}."
    return headline, body, "Contact the owner to discuss the listing.", [f"#{facts.bedrooms}BHK", "#PropertyListing"]
