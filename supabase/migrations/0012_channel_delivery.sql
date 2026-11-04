-- 0012_channel_delivery.sql (D2): channel invocation + durable delivery state.
--
-- Adds conversations.channel, conversation_summaries, delivery_outbox,
-- delivery_attempts, webhook_events, webhook_conflicts; widens
-- followup_drafts.channel ('email'|'whatsapp') and conversation_messages
-- grounding (+ 'validated', citations-empty). Additive only; existing rows
-- satisfy every constraint (new columns nullable or defaulted; CHECK
-- replacements are supersets of the old ones).
--
-- Conflict-status note (§14 reconciliation): webhook_events.status keeps the
-- contracted enum including 'conflict', but arrivals resolving as conflicts
-- are recorded in webhook_conflicts while the original event row keeps its
-- exact state; application code never writes 'conflict' onto an event row.

-- conversations.channel (nullable; existing rows stay NULL).
ALTER TABLE soloestate.conversations ADD COLUMN IF NOT EXISTS channel TEXT NULL;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'conversations_channel_check') THEN
        ALTER TABLE soloestate.conversations
            ADD CONSTRAINT conversations_channel_check CHECK (channel IS NULL OR channel IN ('website', 'whatsapp'));
    END IF;
END $$;

-- followup_drafts.channel widened from email-only.
DO $$
DECLARE
    cname TEXT;
BEGIN
    SELECT conname INTO cname FROM pg_constraint
        WHERE conrelid = 'soloestate.followup_drafts'::regclass AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%channel =%' LIMIT 1;
    IF cname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE soloestate.followup_drafts DROP CONSTRAINT %I', cname);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'followup_drafts_channel_check') THEN
        ALTER TABLE soloestate.followup_drafts
            ADD CONSTRAINT followup_drafts_channel_check CHECK (channel IN ('email', 'whatsapp'));
    END IF;
END $$;

-- conversation_messages.grounding gains 'validated' (citations-empty).
DO $$
DECLARE
    cname TEXT;
BEGIN
    SELECT conname INTO cname FROM pg_constraint
        WHERE conrelid = 'soloestate.conversation_messages'::regclass AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%grounding%IN%' LIMIT 1;
    IF cname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE soloestate.conversation_messages DROP CONSTRAINT %I', cname);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'conversation_messages_grounding_check') THEN
        ALTER TABLE soloestate.conversation_messages
            ADD CONSTRAINT conversation_messages_grounding_check
            CHECK (grounding IN ('grounded', 'unsupported', 'ungrounded', 'validated'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'conversation_messages_validated_empty_check') THEN
        ALTER TABLE soloestate.conversation_messages
            ADD CONSTRAINT conversation_messages_validated_empty_check
            CHECK (grounding != 'validated' OR citations = '[]'::jsonb);
    END IF;
END $$;

-- Append-only per-conversation summaries with a commit-ordered boundary.
CREATE TABLE IF NOT EXISTS soloestate.conversation_summaries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL REFERENCES soloestate.conversations(id) ON DELETE CASCADE,
    through_seq bigint NOT NULL CHECK (through_seq > 0),
    body text NOT NULL CHECK (char_length(body) BETWEEN 1 AND 2000),
    facts jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(facts) = 'object'),
    request_key text NOT NULL UNIQUE CHECK (length(request_key) BETWEEN 1 AND 128),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT conversation_summaries_conversation_seq_key UNIQUE (conversation_id, through_seq),
    CONSTRAINT conversation_summaries_facts_size_check CHECK (octet_length(facts::text) <= 4096)
);
CREATE INDEX IF NOT EXISTS conversation_summaries_conversation_idx
    ON soloestate.conversation_summaries(conversation_id, through_seq DESC);

-- Durable outbound outbox: one row per logical message.
CREATE TABLE IF NOT EXISTS soloestate.delivery_outbox (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_key text NOT NULL UNIQUE CHECK (length(request_key) BETWEEN 1 AND 128),
    channel text NOT NULL CHECK (channel IN ('whatsapp', 'website')),
    purpose text NOT NULL CHECK (length(btrim(purpose)) > 0),
    source_id text NOT NULL CHECK (length(btrim(source_id)) > 0),
    lead_id uuid NOT NULL REFERENCES soloestate.leads(id) ON DELETE CASCADE,
    conversation_id uuid NULL REFERENCES soloestate.conversations(id) ON DELETE SET NULL,
    identity_link_id uuid NULL REFERENCES soloestate.identity_links(id) ON DELETE SET NULL,
    destination text NOT NULL CHECK (length(btrim(destination)) > 0),
    verified_destination text NULL,
    destination_basis text NOT NULL CHECK (destination_basis IN ('link', 'in_thread')),
    category text NOT NULL CHECK (length(btrim(category)) > 0),
    content_ref text NOT NULL CHECK (length(btrim(content_ref)) > 0),
    policy_version int NOT NULL,
    consent jsonb NOT NULL CHECK (jsonb_typeof(consent) = 'object'),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    catalog_version text NOT NULL,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN
        ('queued', 'staged', 'submitting', 'sent', 'delivered', 'read', 'failed', 'unknown', 'dead', 'skipped')),
    skip_reason text NULL,
    provider_msg_id text NULL,
    reserved_day date NULL,
    reserved boolean NOT NULL DEFAULT false,
    resend_count int NOT NULL DEFAULT 0 CHECK (resend_count >= 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS delivery_outbox_status_idx ON soloestate.delivery_outbox(status);
CREATE INDEX IF NOT EXISTS delivery_outbox_lead_idx ON soloestate.delivery_outbox(lead_id);

-- Attempts: at most one open (staged/submitting/unknown) row per outbox row.
CREATE TABLE IF NOT EXISTS soloestate.delivery_attempts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    outbox_id uuid NOT NULL REFERENCES soloestate.delivery_outbox(id) ON DELETE CASCADE,
    attempt_no int NOT NULL CHECK (attempt_no > 0),
    status text NOT NULL DEFAULT 'staged' CHECK (status IN
        ('staged', 'submitting', 'acknowledged', 'failed', 'unknown', 'blocked')),
    lease_owner text NULL,
    lease_until timestamptz NULL,
    fencing bigint NOT NULL DEFAULT 0,
    provider_msg_id text NULL,
    submitted_at timestamptz NULL,
    outcome text NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT delivery_attempts_outbox_no_key UNIQUE (outbox_id, attempt_no)
);
CREATE UNIQUE INDEX IF NOT EXISTS delivery_attempts_single_open_idx ON soloestate.delivery_attempts(outbox_id)
    WHERE status IN ('staged', 'submitting', 'unknown');
CREATE INDEX IF NOT EXISTS delivery_attempts_provider_msg_idx ON soloestate.delivery_attempts(provider_msg_id)
    WHERE provider_msg_id IS NOT NULL;

-- Validated webhook events: the stored payload IS the replay input.
CREATE TABLE IF NOT EXISTS soloestate.webhook_events (
    provider_event_id text PRIMARY KEY CHECK (length(btrim(provider_event_id)) > 0),
    channel text NOT NULL CHECK (channel IN ('whatsapp')),
    event_kind text NOT NULL CHECK (event_kind IN ('message', 'receipt')),
    provider_msg_id text NOT NULL CHECK (length(btrim(provider_msg_id)) > 0),
    status_key text NULL CHECK (status_key IS NULL OR status_key IN ('sent', 'delivered', 'read', 'failed')),
    received_at timestamptz NOT NULL,
    event_time timestamptz NULL,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    payload_hash text NOT NULL,
    status text NOT NULL DEFAULT 'received' CHECK (status IN
        ('received', 'claimed', 'processed', 'dead', 'rejected', 'conflict')),
    lease_owner text NULL,
    lease_until timestamptz NULL,
    attempts int NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    conflict_count int NOT NULL DEFAULT 0 CHECK (conflict_count >= 0),
    reject_reason text NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS webhook_events_status_idx ON soloestate.webhook_events(status);
CREATE INDEX IF NOT EXISTS webhook_events_provider_msg_idx ON soloestate.webhook_events(provider_msg_id);

-- Quarantined conflicting arrivals: first 10 kept per event, the rest counted.
CREATE TABLE IF NOT EXISTS soloestate.webhook_conflicts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_event_id text NOT NULL REFERENCES soloestate.webhook_events(provider_event_id) ON DELETE CASCADE,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    payload_hash text NOT NULL,
    received_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS webhook_conflicts_event_idx ON soloestate.webhook_conflicts(provider_event_id);

-- RLS posture matches earlier migrations: enabled, nothing for PUBLIC.
ALTER TABLE soloestate.conversation_summaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE soloestate.delivery_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE soloestate.delivery_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE soloestate.webhook_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE soloestate.webhook_conflicts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON soloestate.conversation_summaries, soloestate.delivery_outbox, soloestate.delivery_attempts,
    soloestate.webhook_events, soloestate.webhook_conflicts FROM PUBLIC;
