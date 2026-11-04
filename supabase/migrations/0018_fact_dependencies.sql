-- 0018: submission-time business-fact revalidation dependencies (D2).
-- Staging persists the pinned business facts a staged body was rendered
-- from (property values, FAQ pins, slot windows, booking state). The
-- staged -> submitting transition revalidates them under the delivery
-- lock protocol and refuses to submit a stale body: the attempt closes
-- as blocked and the outbox is owner-visibly skipped with the reason.
-- Rows staged before this column carry no dependencies and keep the
-- pre-existing behavior (nothing recorded, nothing revalidated).
ALTER TABLE soloestate.delivery_outbox
    ADD COLUMN fact_deps jsonb NOT NULL DEFAULT '[]'::jsonb;
