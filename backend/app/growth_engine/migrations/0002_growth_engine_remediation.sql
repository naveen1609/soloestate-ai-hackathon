-- Growth Engine remediation: Add versioning, source tracking, and claim audit fields

ALTER TABLE soloestate.growth_kits
    ADD COLUMN IF NOT EXISTS version int NOT NULL DEFAULT 1;

ALTER TABLE soloestate.growth_content_items
    ADD COLUMN IF NOT EXISTS version int NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'model' CHECK (source IN ('model', 'deterministic_template', 'owner_edit')),
    ADD COLUMN IF NOT EXISTS claim_checks jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS rejection_reason text NOT NULL DEFAULT '';

ALTER TABLE soloestate.growth_creative_assets
    ADD COLUMN IF NOT EXISTS version int NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'model' CHECK (source IN ('model', 'svg_concept_fallback', 'uploaded')),
    ADD COLUMN IF NOT EXISTS rejection_reason text NOT NULL DEFAULT '';

ALTER TABLE soloestate.growth_campaign_plans
    ADD COLUMN IF NOT EXISTS version int NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'model' CHECK (source IN ('model', 'deterministic_template', 'owner_edit')),
    ADD COLUMN IF NOT EXISTS rejection_reason text NOT NULL DEFAULT '';
