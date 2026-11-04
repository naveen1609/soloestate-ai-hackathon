-- SoloEstate AI Growth Engine-lite isolated migrations
-- Does not modify core migrations or schema history table.

CREATE SCHEMA IF NOT EXISTS soloestate;

-- Growth Engine migration history
CREATE TABLE IF NOT EXISTS soloestate.growth_migrations (
    version text PRIMARY KEY,
    checksum text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);

-- Authentic property photography & architectural assets
CREATE TABLE IF NOT EXISTS soloestate.growth_property_images (
    id uuid PRIMARY KEY,
    property_id uuid NOT NULL REFERENCES soloestate.properties(id) ON DELETE CASCADE,
    image_url text NOT NULL CHECK (length(btrim(image_url)) > 0),
    caption text NOT NULL DEFAULT '',
    is_authentic boolean NOT NULL DEFAULT true,
    features text[] NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Owner marketing briefs (Text or Meta Muse Voice Transcribe)
CREATE TABLE IF NOT EXISTS soloestate.growth_briefs (
    id uuid PRIMARY KEY,
    property_id uuid NOT NULL REFERENCES soloestate.properties(id) ON DELETE CASCADE,
    input_mode text NOT NULL CHECK (input_mode IN ('text', 'voice')),
    raw_input text NOT NULL CHECK (length(btrim(raw_input)) > 0),
    target_audience text NOT NULL DEFAULT '',
    key_selling_points text[] NOT NULL DEFAULT '{}',
    tone text NOT NULL DEFAULT 'professional' CHECK (tone IN ('professional', 'luxury', 'warm', 'urgent', 'festive')),
    campaign_objective text NOT NULL DEFAULT 'lead_generation' CHECK (campaign_objective IN ('lead_generation', 'brand_awareness', 'open_house', 'festive_offer', 'price_drop')),
    special_offers text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Marketing content kits bound to a property grounding snapshot
CREATE TABLE IF NOT EXISTS soloestate.growth_kits (
    id uuid PRIMARY KEY,
    property_id uuid NOT NULL REFERENCES soloestate.properties(id) ON DELETE CASCADE,
    brief_id uuid REFERENCES soloestate.growth_briefs(id) ON DELETE SET NULL,
    grounding_snapshot jsonb NOT NULL,
    grounding_version int NOT NULL CHECK (grounding_version >= 1),
    grounding_hash text NOT NULL CHECK (length(grounding_hash) = 64),
    objective text NOT NULL CHECK (length(btrim(objective)) > 0),
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'partially_approved', 'approved', 'archived')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Creative assets (SAM 3.1 segmentations and Muse Image concepts)
-- Authenticity is explicitly tracked; concepts are never marked as authentic photographs.
CREATE TABLE IF NOT EXISTS soloestate.growth_creative_assets (
    id uuid PRIMARY KEY,
    kit_id uuid NOT NULL REFERENCES soloestate.growth_kits(id) ON DELETE CASCADE,
    property_id uuid NOT NULL REFERENCES soloestate.properties(id) ON DELETE CASCADE,
    source_image_id uuid REFERENCES soloestate.growth_property_images(id) ON DELETE SET NULL,
    asset_type text NOT NULL CHECK (asset_type IN ('segmented_feature', 'concept_graphic', 'ad_banner', 'story_visual')),
    is_authentic boolean NOT NULL DEFAULT false,
    concept_watermark text NOT NULL DEFAULT 'AI Creative Concept - Not Actual Property Photography',
    image_url text NOT NULL CHECK (length(btrim(image_url)) > 0),
    segmentation_data jsonb NOT NULL DEFAULT '{}'::jsonb,
    prompt_text text NOT NULL DEFAULT '',
    prompt_hash text NOT NULL DEFAULT '',
    model_id text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'rejected')),
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Platform-specific content items (Instagram, Facebook, Festive drafts)
CREATE TABLE IF NOT EXISTS soloestate.growth_content_items (
    id uuid PRIMARY KEY,
    kit_id uuid NOT NULL REFERENCES soloestate.growth_kits(id) ON DELETE CASCADE,
    channel text NOT NULL CHECK (channel IN ('instagram_feed', 'instagram_story', 'facebook_feed', 'festival_offer')),
    headline text NOT NULL CHECK (length(btrim(headline)) > 0),
    body text NOT NULL CHECK (length(btrim(body)) > 0),
    call_to_action text NOT NULL CHECK (length(btrim(call_to_action)) > 0),
    hashtags text[] NOT NULL DEFAULT '{}',
    creative_asset_id uuid REFERENCES soloestate.growth_creative_assets(id) ON DELETE SET NULL,
    grounding_verified boolean NOT NULL DEFAULT true,
    verification_notes text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'rejected')),
    approval_notes text NOT NULL DEFAULT '',
    approved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Campaign plans (objective, audience specs, placement recommendations, budget bands)
CREATE TABLE IF NOT EXISTS soloestate.growth_campaign_plans (
    id uuid PRIMARY KEY,
    kit_id uuid NOT NULL REFERENCES soloestate.growth_kits(id) ON DELETE CASCADE,
    property_id uuid NOT NULL REFERENCES soloestate.properties(id) ON DELETE CASCADE,
    objective text NOT NULL CHECK (length(btrim(objective)) > 0),
    target_audience_spec jsonb NOT NULL,
    recommended_placements text[] NOT NULL CHECK (cardinality(recommended_placements) > 0),
    suggested_daily_budget numeric(10,2) NOT NULL CHECK (suggested_daily_budget > 0),
    suggested_duration_days int NOT NULL CHECK (suggested_duration_days >= 1),
    rationale text NOT NULL DEFAULT '',
    staged_launch_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'rejected')),
    approved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Enable Row Level Security and revoke public access
ALTER TABLE soloestate.growth_migrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE soloestate.growth_property_images ENABLE ROW LEVEL SECURITY;
ALTER TABLE soloestate.growth_briefs ENABLE ROW LEVEL SECURITY;
ALTER TABLE soloestate.growth_kits ENABLE ROW LEVEL SECURITY;
ALTER TABLE soloestate.growth_creative_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE soloestate.growth_content_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE soloestate.growth_campaign_plans ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON soloestate.growth_migrations FROM PUBLIC;
REVOKE ALL ON soloestate.growth_property_images FROM PUBLIC;
REVOKE ALL ON soloestate.growth_briefs FROM PUBLIC;
REVOKE ALL ON soloestate.growth_kits FROM PUBLIC;
REVOKE ALL ON soloestate.growth_creative_assets FROM PUBLIC;
REVOKE ALL ON soloestate.growth_content_items FROM PUBLIC;
REVOKE ALL ON soloestate.growth_campaign_plans FROM PUBLIC;

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_growth_images_property ON soloestate.growth_property_images(property_id);
CREATE INDEX IF NOT EXISTS idx_growth_briefs_property ON soloestate.growth_briefs(property_id);
CREATE INDEX IF NOT EXISTS idx_growth_kits_property ON soloestate.growth_kits(property_id);
CREATE INDEX IF NOT EXISTS idx_growth_creative_kit ON soloestate.growth_creative_assets(kit_id);
CREATE INDEX IF NOT EXISTS idx_growth_content_kit ON soloestate.growth_content_items(kit_id);
CREATE INDEX IF NOT EXISTS idx_growth_plans_kit ON soloestate.growth_campaign_plans(kit_id);
