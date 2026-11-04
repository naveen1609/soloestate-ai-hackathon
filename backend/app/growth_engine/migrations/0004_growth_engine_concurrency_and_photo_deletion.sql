-- Growth Engine Migration 0004: Single Active Campaign Plan Constraint & Compound Photo FK Deletion Behavior

-- 1. Ensure single active campaign plan per kit
CREATE UNIQUE INDEX IF NOT EXISTS growth_campaign_plans_single_active_idx
ON soloestate.growth_campaign_plans(kit_id)
WHERE is_active = true;

-- 2. Repair compound photo foreign key deletion behavior
-- Drop previous FK if exists
ALTER TABLE soloestate.growth_creative_assets
    DROP CONSTRAINT IF EXISTS growth_creative_assets_source_image_property_fk;

-- Trigger to clear source_image_id on growth_creative_assets before a photo is deleted from growth_property_images
-- This preserves the creative asset's non-null property_id while safely removing the deleted photo reference
CREATE OR REPLACE FUNCTION soloestate.growth_property_image_before_delete()
RETURNS trigger AS $$
BEGIN
    UPDATE soloestate.growth_creative_assets
    SET source_image_id = NULL
    WHERE source_image_id = OLD.id AND property_id = OLD.property_id;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_growth_property_image_delete ON soloestate.growth_property_images;
CREATE TRIGGER trg_growth_property_image_delete
    BEFORE DELETE ON soloestate.growth_property_images
    FOR EACH ROW
    EXECUTE FUNCTION soloestate.growth_property_image_before_delete();

-- Re-add compound foreign key with RESTRICT to ensure consistency
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'growth_creative_assets_source_image_property_fk'
    ) THEN
        ALTER TABLE soloestate.growth_creative_assets
            ADD CONSTRAINT growth_creative_assets_source_image_property_fk
            FOREIGN KEY (source_image_id, property_id)
            REFERENCES soloestate.growth_property_images(id, property_id)
            ON DELETE RESTRICT;
    END IF;
END $$;
