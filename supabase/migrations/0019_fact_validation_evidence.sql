-- 0019: explicit fact-validation evidence for staged outbox rows (D2).
-- Migration 0018 defaulted fact_deps to '[]' for existing rows, but an
-- empty pin list is also the correct state of a newly validated
-- fact-free reply, so emptiness alone cannot distinguish a
-- server-validated fact-free message from a legacy row staged before
-- fact revalidation existed. This column records which case a row is:
-- staging through the validated path sets it true (with or without
-- pins); every pre-existing row keeps false (no evidence) and the
-- staged -> submitting transition fails such rows closed with an
-- owner-visible reason, refunding once and requiring fresh generation.
-- The original payload and all historical evidence are preserved.
ALTER TABLE soloestate.delivery_outbox
    ADD COLUMN fact_validated boolean NOT NULL DEFAULT false;
