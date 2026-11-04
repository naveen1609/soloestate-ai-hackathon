-- Growth Engine Migration 0003: Provenance Vocabulary, Atomic Concurrency, Supersession & Photo Isolation

-- 1. Update source constraints on growth_content_items
ALTER TABLE soloestate.growth_content_items
    DROP CONSTRAINT IF EXISTS growth_content_items_source_check;

ALTER TABLE soloestate.growth_content_items
    ADD CONSTRAINT growth_content_items_source_check
    CHECK (source IN ('muse_spark', 'deterministic_template', 'owner_edit', 'model'));

-- 2. Update status constraint on growth_content_items to support supersession
ALTER TABLE soloestate.growth_content_items
    DROP CONSTRAINT IF EXISTS growth_content_items_status_check;

ALTER TABLE soloestate.growth_content_items
    ADD CONSTRAINT growth_content_items_status_check
    CHECK (status IN ('draft', 'approved', 'rejected', 'superseded'));

-- 3. Update source constraints on growth_creative_assets
ALTER TABLE soloestate.growth_creative_assets
    DROP CONSTRAINT IF EXISTS growth_creative_assets_source_check;

ALTER TABLE soloestate.growth_creative_assets
    ADD CONSTRAINT growth_creative_assets_source_check
    CHECK (source IN ('muse_image', 'svg_concept_fallback', 'sam_3_1', 'uploaded', 'model'));

-- 4. Update status constraint on growth_creative_assets to support supersession
ALTER TABLE soloestate.growth_creative_assets
    DROP CONSTRAINT IF EXISTS growth_creative_assets_status_check;

ALTER TABLE soloestate.growth_creative_assets
    ADD CONSTRAINT growth_creative_assets_status_check
    CHECK (status IN ('draft', 'approved', 'rejected', 'superseded'));

-- 5. Update source and status constraints on growth_campaign_plans
ALTER TABLE soloestate.growth_campaign_plans
    DROP CONSTRAINT IF EXISTS growth_campaign_plans_source_check;

ALTER TABLE soloestate.growth_campaign_plans
    ADD CONSTRAINT growth_campaign_plans_source_check
    CHECK (source IN ('muse_spark_planner', 'muse_spark', 'deterministic_template', 'owner_edit', 'model'));

ALTER TABLE soloestate.growth_campaign_plans
    DROP CONSTRAINT IF EXISTS growth_campaign_plans_status_check;

ALTER TABLE soloestate.growth_campaign_plans
    ADD CONSTRAINT growth_campaign_plans_status_check
    CHECK (status IN ('draft', 'approved', 'rejected', 'superseded'));

ALTER TABLE soloestate.growth_campaign_plans
    ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;

-- 6. Enforce cross-property photo isolation:
-- Compound unique constraint on growth_property_images (id, property_id)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'growth_property_images_id_property_uniq'
    ) THEN
        ALTER TABLE soloestate.growth_property_images
            ADD CONSTRAINT growth_property_images_id_property_uniq UNIQUE (id, property_id);
    END IF;
END $$;

-- Compound foreign key on growth_creative_assets (source_image_id, property_id)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'growth_creative_assets_source_image_property_fk'
    ) THEN
        ALTER TABLE soloestate.growth_creative_assets
            ADD CONSTRAINT growth_creative_assets_source_image_property_fk
            FOREIGN KEY (source_image_id, property_id)
            REFERENCES soloestate.growth_property_images(id, property_id)
            ON DELETE SET NULL;
    END IF;
END $$;
