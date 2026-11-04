"""Demo fixtures for SoloEstate AI Growth Engine-lite.
Provides sample authentic property photographs with architectural features
matching the 3 official demo properties in Bengaluru.
"""

from datetime import datetime, timezone
from uuid import UUID

# Demo Properties from supabase/seed.sql
CEDAR_APARTMENT_ID = UUID("20000000-0000-4000-8000-000000000001")
WILLOW_RESIDENCE_ID = UUID("20000000-0000-4000-8000-000000000002")
MAPLE_HOME_ID = UUID("20000000-0000-4000-8000-000000000003")

DEMO_PROPERTY_IMAGES = [
    {
        "id": "40000000-0000-4000-8000-000000000001",
        "property_id": str(CEDAR_APARTMENT_ID),
        "image_url": "https://images.unsplash.com/photo-1545324418-cc1a3fa10c00?auto=format&fit=crop&w=1200&q=80",
        "caption": "Living room with floor-to-ceiling balcony view",
        "is_authentic": True,
        "features": ["Eastern Balcony", "Hardwood Living Flooring", "Double Glazed Windows"],
        "created_at": datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc),
    },
    {
        "id": "40000000-0000-4000-8000-000000000002",
        "property_id": str(CEDAR_APARTMENT_ID),
        "image_url": "https://images.unsplash.com/photo-1556911220-e15b29be8c8f?auto=format&fit=crop&w=1200&q=80",
        "caption": "Modern modular kitchen with premium granite counters",
        "is_authentic": True,
        "features": ["Modular Kitchen Island", "Granite Countertops", "Concealed Lighting"],
        "created_at": datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc),
    },
    {
        "id": "40000000-0000-4000-8000-000000000003",
        "property_id": str(WILLOW_RESIDENCE_ID),
        "image_url": "https://images.unsplash.com/photo-1600585154340-be6161a56a0c?auto=format&fit=crop&w=1200&q=80",
        "caption": "Spacious 3 BHK master suite with walk-in wardrobe area",
        "is_authentic": True,
        "features": ["Master Bedroom", "Walk-in Wardrobe", "Teakwood Finish"],
        "created_at": datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc),
    },
    {
        "id": "40000000-0000-4000-8000-000000000004",
        "property_id": str(MAPLE_HOME_ID),
        "image_url": "https://images.unsplash.com/photo-1600566753376-12c8ab7fb75b?auto=format&fit=crop&w=1200&q=80",
        "caption": "Dining area adjacent to landscaped courtyard",
        "is_authentic": True,
        "features": ["Courtyard View", "Open Plan Dining", "Vitrified Tiles"],
        "created_at": datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc),
    },
]

DEMO_VOICE_BRIEFS = [
    {
        "title": "Family Lifestyle Focus (Voice)",
        "transcript": (
            "We want to market Cedar Apartment to young tech couples and expanding families. "
            "Highlight the morning sunlight on the covered balcony, the quiet residential lane in Indiranagar, "
            "and the ready modular kitchen with granite tops. Offer a complimentary interior consultation for bookings made this week."
        ),
        "tone": "warm",
        "campaign_objective": "lead_generation",
    },
    {
        "title": "Festive Prosperity Campaign (Voice)",
        "transcript": (
            "Prepare a festive celebration campaign for Diwali. Emphasize verified clean legal titles, immediate possession, "
            "and prime Indiranagar location with zero brokerage. Exclusive festival pricing locked for this month."
        ),
        "tone": "festive",
        "campaign_objective": "festive_offer",
    },
]
