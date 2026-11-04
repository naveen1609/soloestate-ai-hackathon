-- Growth Engine Migration 0005: Single Active Content Item Constraint Per Kit/Channel
-- Enforces that at most one active (non-superseded) content item can exist per kit and channel at the database level.

-- Safe reconciliation rollout: For any existing (kit_id, channel) with multiple active/non-superseded rows,
-- mark older duplicates as 'superseded', preserving the latest row (by version DESC, updated_at DESC, created_at DESC).
WITH ranked_duplicates AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY kit_id, channel
               ORDER BY version DESC, updated_at DESC, created_at DESC
           ) as rn
    FROM soloestate.growth_content_items
    WHERE status != 'superseded'
)
UPDATE soloestate.growth_content_items
SET status = 'superseded',
    updated_at = NOW()
WHERE id IN (
    SELECT id FROM ranked_duplicates WHERE rn > 1
);

CREATE UNIQUE INDEX IF NOT EXISTS growth_content_items_single_active_idx
ON soloestate.growth_content_items(kit_id, channel)
WHERE status != 'superseded';
