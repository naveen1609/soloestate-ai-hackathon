-- Expand the existing CRM to represent unanswered requirements, without rewriting records.
ALTER TABLE soloestate.leads
 ALTER COLUMN preferred_localities DROP NOT NULL,
 ALTER COLUMN budget_min DROP NOT NULL,
 ALTER COLUMN budget_max DROP NOT NULL,
 ALTER COLUMN bedrooms DROP NOT NULL,
 ADD COLUMN purchase_timeframe text CHECK (purchase_timeframe IN ('within_3_months','three_to_six_months','later','no_fixed_deadline')),
 ADD COLUMN qualification_fingerprint text CHECK (qualification_fingerprint ~ '^[0-9a-f]{64}$'),
 ADD CONSTRAINT lead_budget_ceiling_nonnegative CHECK (budget_max >= 0);
-- Existing checks still validate present bounds, bedroom counts and nonempty locality arrays.
-- NULL defaults intentionally do not infer timeframe or qualification evidence for existing leads.
