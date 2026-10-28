-- Single-owner hackathon schema. No customer data is exposed through the Data API.
CREATE SCHEMA IF NOT EXISTS soloestate;
REVOKE ALL ON SCHEMA soloestate FROM PUBLIC;

CREATE TABLE soloestate.leads (
    id uuid PRIMARY KEY,
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    email text NOT NULL UNIQUE,
    preferred_localities text[] NOT NULL CHECK (cardinality(preferred_localities) > 0),
    budget_min numeric(14,2) NOT NULL CHECK (budget_min >= 0),
    budget_max numeric(14,2) NOT NULL CHECK (budget_max >= budget_min),
    bedrooms smallint NOT NULL CHECK (bedrooms > 0),
    status text NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'contacted', 'qualified', 'closed')),
    notes text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE soloestate.properties (
    id uuid PRIMARY KEY,
    title text NOT NULL CHECK (length(btrim(title)) > 0),
    locality text NOT NULL CHECK (length(btrim(locality)) > 0),
    price numeric(14,2) NOT NULL CHECK (price > 0),
    bedrooms smallint NOT NULL CHECK (bedrooms > 0),
    active boolean NOT NULL DEFAULT true,
    description text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Exact appointment windows for one owner, independent of property choice.
-- Adjacent [start,end) windows are allowed; overlapping windows are not.
CREATE TABLE soloestate.visit_slots (
    id uuid PRIMARY KEY,
    starts_at timestamptz NOT NULL,
    ends_at timestamptz NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    CHECK (ends_at > starts_at),
    EXCLUDE USING gist (tstzrange(starts_at, ends_at, '[)') WITH &&)
);

CREATE TABLE soloestate.runs (
    id uuid PRIMARY KEY,
    request_key text NOT NULL UNIQUE CHECK (length(btrim(request_key)) > 0),
    goal text NOT NULL CHECK (length(btrim(goal)) BETWEEN 1 AND 20000),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN
        ('pending', 'running', 'awaiting_approval', 'completed', 'failed', 'cancelled')),
    plan jsonb CHECK (jsonb_typeof(plan) = 'object'),
    outcome jsonb CHECK (jsonb_typeof(outcome) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE soloestate.run_steps (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES soloestate.runs(id),
    sequence integer NOT NULL CHECK (sequence > 0),
    tool_name text NOT NULL CHECK (tool_name IN ('get_leads', 'get_property_matches',
        'get_available_slots', 'create_followup_draft', 'request_approval',
        'book_site_visit', 'get_run_status')),
    idempotency_key text NOT NULL CHECK (length(btrim(idempotency_key)) > 0),
    arguments jsonb NOT NULL CHECK (jsonb_typeof(arguments) = 'object'),
    status text NOT NULL CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'awaiting_approval')),
    result jsonb CHECK (jsonb_typeof(result) = 'object'),
    error jsonb CHECK (jsonb_typeof(error) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    UNIQUE (run_id, sequence),
    UNIQUE (run_id, idempotency_key),
    CHECK (status <> 'succeeded' OR (result IS NOT NULL AND error IS NULL AND finished_at IS NOT NULL)),
    CHECK (status <> 'failed' OR (error IS NOT NULL AND finished_at IS NOT NULL))
);

CREATE TABLE soloestate.followup_drafts (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES soloestate.runs(id),
    lead_id uuid NOT NULL REFERENCES soloestate.leads(id),
    idempotency_key text NOT NULL CHECK (length(btrim(idempotency_key)) > 0),
    channel text NOT NULL DEFAULT 'email' CHECK (channel = 'email'),
    subject text NOT NULL CHECK (length(btrim(subject)) > 0),
    body text NOT NULL CHECK (length(btrim(body)) > 0),
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'rejected', 'sent')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, idempotency_key),
    UNIQUE (id, run_id, lead_id)
);

CREATE TABLE soloestate.approvals (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES soloestate.runs(id),
    kind text NOT NULL CHECK (kind IN ('followup', 'booking')),
    lead_id uuid NOT NULL REFERENCES soloestate.leads(id),
    draft_id uuid,
    slot_id uuid REFERENCES soloestate.visit_slots(id),
    property_id uuid REFERENCES soloestate.properties(id),
    idempotency_key text NOT NULL CHECK (length(btrim(idempotency_key)) > 0),
    -- Immutable payload/version binding and owner identity checks are added in Phase 7.
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'consumed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    decided_at timestamptz,
    UNIQUE (run_id, idempotency_key),
    UNIQUE (id, run_id, kind, lead_id, slot_id, property_id),
    FOREIGN KEY (draft_id, run_id, lead_id) REFERENCES soloestate.followup_drafts(id, run_id, lead_id),
    CHECK ((kind = 'followup' AND draft_id IS NOT NULL AND slot_id IS NULL AND property_id IS NULL)
        OR (kind = 'booking' AND draft_id IS NULL AND slot_id IS NOT NULL AND property_id IS NOT NULL)),
    CHECK ((status = 'pending' AND decided_at IS NULL) OR (status <> 'pending' AND decided_at IS NOT NULL))
);

CREATE TABLE soloestate.bookings (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES soloestate.runs(id),
    approval_id uuid NOT NULL UNIQUE,
    approval_kind text NOT NULL DEFAULT 'booking' CHECK (approval_kind = 'booking'),
    lead_id uuid NOT NULL REFERENCES soloestate.leads(id),
    slot_id uuid NOT NULL REFERENCES soloestate.visit_slots(id),
    property_id uuid NOT NULL REFERENCES soloestate.properties(id),
    idempotency_key text NOT NULL CHECK (length(btrim(idempotency_key)) > 0),
    status text NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed', 'cancelled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, idempotency_key),
    FOREIGN KEY (approval_id, run_id, approval_kind, lead_id, slot_id, property_id)
        REFERENCES soloestate.approvals(id, run_id, kind, lead_id, slot_id, property_id)
);
CREATE UNIQUE INDEX one_active_booking_per_slot ON soloestate.bookings(slot_id) WHERE status = 'confirmed';
CREATE INDEX leads_created_at_idx ON soloestate.leads(created_at);
CREATE INDEX approvals_pending_idx ON soloestate.approvals(run_id) WHERE status = 'pending';

-- No browser policies: the backend connects directly with a trusted database role.
ALTER TABLE soloestate.leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE soloestate.properties ENABLE ROW LEVEL SECURITY;
ALTER TABLE soloestate.visit_slots ENABLE ROW LEVEL SECURITY;
ALTER TABLE soloestate.runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE soloestate.run_steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE soloestate.followup_drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE soloestate.approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE soloestate.bookings ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ALL TABLES IN SCHEMA soloestate FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA soloestate REVOKE ALL ON TABLES FROM PUBLIC;
