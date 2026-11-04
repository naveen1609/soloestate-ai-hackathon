-- 0016: webhook claim fencing token (D2 failure/retry ownership).
-- Owner identity alone cannot distinguish successive claims held under the
-- same worker name, so every claim increments a generation. Worker-originated
-- transitions (release, park-dead, completion) are conditional on the claimed
-- state plus matching (lease_owner, claim_generation); sweeper transitions
-- remain status/lease-based. Existing rows default to generation 0.
ALTER TABLE soloestate.webhook_events
    ADD COLUMN IF NOT EXISTS claim_generation bigint NOT NULL DEFAULT 0;
