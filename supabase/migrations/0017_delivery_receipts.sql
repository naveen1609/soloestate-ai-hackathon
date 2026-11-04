-- 0017: durable pending delivery receipts (D2 early-receipt reconciliation).
-- A receipt can arrive before the send-response provider ID is persisted, so
-- unmatched receipts are retained as explicit pending evidence here and
-- drained the moment the ID becomes known. (channel, provider_msg_id,
-- status_key) is unique so duplicate deliveries stay idempotent.
CREATE TABLE soloestate.delivery_receipts(
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    channel text NOT NULL DEFAULT 'whatsapp',
    provider_msg_id text NOT NULL,
    status_key text NOT NULL,
    event_time timestamptz NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    provider_event_id text NULL,
    applied_outcome jsonb NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (channel, provider_msg_id, status_key));
CREATE INDEX delivery_receipts_msg_idx ON soloestate.delivery_receipts(provider_msg_id);
ALTER TABLE soloestate.delivery_receipts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON soloestate.delivery_receipts FROM PUBLIC;
