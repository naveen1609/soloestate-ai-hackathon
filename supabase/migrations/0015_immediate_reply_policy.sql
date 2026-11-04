-- 0015: owner-managed immediate-reply messaging policy (D2 finding-3 follow-up).
-- Distinct from D3 automation_policies (booking_reminder/followup_nudge), which
-- this table never repurposes. One row per channel, keyed by channel name;
-- only 'whatsapp' is seeded because website replies are inline, not outbox
-- deliveries. Default disabled: no automatic authority exists until the owner
-- enables explicit routine categories. Missing row fails closed in dispatch.
CREATE TABLE soloestate.immediate_reply_policies(
    id text PRIMARY KEY,
    enabled boolean NOT NULL DEFAULT false,
    categories text[] NOT NULL DEFAULT '{}',
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp());
INSERT INTO soloestate.immediate_reply_policies(id,enabled,categories)
    VALUES ('whatsapp',false,'{}');

-- Outbox evidence binding. Nullable so pre-migration rows stay readable;
-- NULL-bound (legacy) rows fail closed at the transition and are rebound to
-- the current policy only by a fresh restage/requeue/resend authorization.
ALTER TABLE soloestate.delivery_outbox ADD COLUMN reply_policy_id text NULL;
ALTER TABLE soloestate.delivery_outbox ADD COLUMN reply_policy_version int NULL;

-- RLS posture matches earlier migrations: enabled, nothing for PUBLIC.
ALTER TABLE soloestate.immediate_reply_policies ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON soloestate.immediate_reply_policies FROM PUBLIC;
