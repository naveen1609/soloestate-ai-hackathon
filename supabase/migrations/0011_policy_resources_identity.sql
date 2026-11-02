-- Additive D1 foundation: business policies, resources, booking requests, identity links,
-- consent/suppression, delivery caps, message sequencing. Existing rows keep working on
-- owner-resource defaults; no rewrites, no seed changes, versions preserved.
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE soloestate.resources(
    id uuid PRIMARY KEY,
    name text NOT NULL CHECK(length(btrim(name))>0),
    kind text NOT NULL DEFAULT 'person' CHECK(kind='person'),
    active boolean NOT NULL DEFAULT true,
    version int NOT NULL DEFAULT 1 CHECK(version>0),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE soloestate.resources ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON soloestate.resources FROM PUBLIC;
CREATE TRIGGER crm_version BEFORE UPDATE ON soloestate.resources FOR EACH ROW EXECUTE FUNCTION soloestate.crm_version();
INSERT INTO soloestate.resources(id,name) VALUES ('00000000-0000-4000-8000-000000000001','Owner');

CREATE TABLE soloestate.business_policies(
    id text PRIMARY KEY CHECK(id='default'),
    timezone text NOT NULL DEFAULT 'Asia/Kolkata' CHECK(length(btrim(timezone))>0),
    working_days int[] NOT NULL DEFAULT '{0,1,3,4,5,6}' CHECK(working_days <@ ARRAY[0,1,2,3,4,5,6]),
    weekly_holiday int NOT NULL DEFAULT 2 CHECK(weekly_holiday BETWEEN 0 AND 6),
    hours_start time NOT NULL DEFAULT '10:00',
    hours_end time NOT NULL DEFAULT '17:00',
    holiday_dates date[] NOT NULL DEFAULT '{}',
    notice_hours int NOT NULL DEFAULT 16 CHECK(notice_hours>0),
    default_duration_min int NOT NULL DEFAULT 30 CHECK(default_duration_min>0),
    min_duration_min int NOT NULL DEFAULT 15 CHECK(min_duration_min>0),
    max_duration_min int NOT NULL DEFAULT 120 CHECK(max_duration_min>=min_duration_min),
    buffer_minutes int NOT NULL DEFAULT 0 CHECK(buffer_minutes>=0),
    version int NOT NULL DEFAULT 1 CHECK(version>0),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK(hours_end>hours_start)
);
ALTER TABLE soloestate.business_policies ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON soloestate.business_policies FROM PUBLIC;
CREATE TRIGGER crm_version BEFORE UPDATE ON soloestate.business_policies FOR EACH ROW EXECUTE FUNCTION soloestate.crm_version();
INSERT INTO soloestate.business_policies(id) VALUES ('default');

CREATE TABLE soloestate.automation_controls(
    id text PRIMARY KEY CHECK(id='default'),
    emergency_stop boolean NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE soloestate.automation_controls ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON soloestate.automation_controls FROM PUBLIC;
INSERT INTO soloestate.automation_controls(id) VALUES ('default');

CREATE TABLE soloestate.automation_policies(
    id uuid PRIMARY KEY,
    kind text NOT NULL CHECK(kind IN ('booking_reminder','followup_nudge')),
    hours_before int NULL CHECK(hours_before IS NULL OR hours_before>0),
    days_after int NULL CHECK(days_after IS NULL OR days_after>=0),
    after_event text NULL CHECK(after_event IS NULL OR length(btrim(after_event))>0),
    frequency text NOT NULL DEFAULT 'once' CHECK(frequency IN ('once','daily','weekly')),
    categories text[] NOT NULL DEFAULT '{}',
    lead_statuses text[] NOT NULL DEFAULT '{}',
    max_attempts int NOT NULL DEFAULT 1 CHECK(max_attempts>0),
    per_lead_cooldown_hours int NOT NULL DEFAULT 24 CHECK(per_lead_cooldown_hours>=0),
    wording text NOT NULL DEFAULT 'template' CHECK(wording IN ('template','assisted')),
    enabled boolean NOT NULL DEFAULT false,
    version int NOT NULL DEFAULT 1 CHECK(version>0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK((kind='booking_reminder')=(hours_before IS NOT NULL)),
    CHECK((kind='followup_nudge')=(days_after IS NOT NULL AND after_event IS NOT NULL))
);
ALTER TABLE soloestate.automation_policies ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON soloestate.automation_policies FROM PUBLIC;
CREATE TRIGGER crm_version BEFORE UPDATE ON soloestate.automation_policies FOR EACH ROW EXECUTE FUNCTION soloestate.crm_version();

CREATE TABLE soloestate.booking_requests(
    id uuid PRIMARY KEY,
    lead_id uuid NOT NULL REFERENCES soloestate.leads(id),
    property_id uuid NULL REFERENCES soloestate.properties(id),
    conversation_id uuid NULL REFERENCES soloestate.conversations(id),
    channel text NOT NULL CHECK(channel IN ('owner','synthetic','website','whatsapp')),
    requested_start timestamptz NOT NULL,
    requested_end timestamptz NOT NULL,
    requested_resource_id uuid NULL REFERENCES soloestate.resources(id),
    requested_duration_min int NOT NULL CHECK(requested_duration_min>0),
    customer_confirmed_start timestamptz NULL,
    customer_confirmed_end timestamptz NULL,
    customer_confirmed_resource_id uuid NULL REFERENCES soloestate.resources(id),
    proposal_expires_at timestamptz NULL,
    owner_approved_start timestamptz NULL,
    owner_approved_end timestamptz NULL,
    owner_approved_resource_id uuid NULL REFERENCES soloestate.resources(id),
    needs_owner_approval boolean NOT NULL,
    exception_reasons text[] NOT NULL DEFAULT '{}',
    status text NOT NULL CHECK(status IN ('proposed','awaiting_owner','approved','denied','expired','booked','cancelled')),
    approval_id uuid NULL UNIQUE REFERENCES soloestate.approvals(id),
    booking_id uuid NULL UNIQUE REFERENCES soloestate.bookings(id),
    policy_version int NOT NULL,
    request_hash text NOT NULL CHECK(length(request_hash)=64),
    expires_at timestamptz NOT NULL,
    version int NOT NULL DEFAULT 1 CHECK(version>0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK(requested_end>requested_start),
    CHECK((customer_confirmed_start IS NULL)=(customer_confirmed_end IS NULL)),
    CHECK(customer_confirmed_end IS NULL OR customer_confirmed_end>customer_confirmed_start),
    CHECK((owner_approved_start IS NULL)=(owner_approved_end IS NULL)),
    CHECK(owner_approved_end IS NULL OR owner_approved_end>owner_approved_start)
);
ALTER TABLE soloestate.booking_requests ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON soloestate.booking_requests FROM PUBLIC;
CREATE TRIGGER crm_version BEFORE UPDATE ON soloestate.booking_requests FOR EACH ROW EXECUTE FUNCTION soloestate.crm_version();
CREATE INDEX booking_requests_lead ON soloestate.booking_requests(lead_id);
CREATE INDEX booking_requests_status ON soloestate.booking_requests(status);

CREATE TABLE soloestate.identity_links(
    id uuid PRIMARY KEY,
    channel text NOT NULL CHECK(channel IN ('website','whatsapp')),
    provider_ref text NOT NULL CHECK(length(btrim(provider_ref))>0),
    lead_id uuid NOT NULL REFERENCES soloestate.leads(id),
    verified_destination text NULL,
    method text NULL CHECK(method IS NULL OR method='owner_verified'),
    evidence jsonb NULL CHECK(evidence IS NULL OR jsonb_typeof(evidence)='object'),
    status text NOT NULL CHECK(status IN ('observed','active','revoked','superseded')),
    version int NOT NULL DEFAULT 1 CHECK(version>0),
    created_by_actor text NOT NULL CHECK(created_by_actor IN ('owner','visitor','channel','policy')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    decided_at timestamptz NULL,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK(status<>'active' OR (method='owner_verified' AND verified_destination IS NOT NULL)),
    CHECK(status<>'observed' OR method IS NULL)
);
ALTER TABLE soloestate.identity_links ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON soloestate.identity_links FROM PUBLIC;
CREATE TRIGGER crm_version BEFORE UPDATE ON soloestate.identity_links FOR EACH ROW EXECUTE FUNCTION soloestate.crm_version();
CREATE UNIQUE INDEX identity_links_live ON soloestate.identity_links(channel,provider_ref) WHERE status IN ('observed','active');
CREATE INDEX identity_links_lead ON soloestate.identity_links(lead_id);

CREATE TABLE soloestate.suppression_list(
    channel text NOT NULL CHECK(channel IN ('website','whatsapp')),
    destination text NOT NULL CHECK(length(btrim(destination))>0),
    source text NOT NULL CHECK(source IN ('inbound_keyword','owner_action')),
    evidence_ref text NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(channel,destination)
);
ALTER TABLE soloestate.suppression_list ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON soloestate.suppression_list FROM PUBLIC;

CREATE TABLE soloestate.delivery_caps(
    day date NOT NULL,
    channel text NOT NULL CHECK(channel IN ('website','whatsapp')),
    used int NOT NULL DEFAULT 0 CHECK(used>=0),
    cap int NOT NULL CHECK(cap>0),
    PRIMARY KEY(day,channel)
);
ALTER TABLE soloestate.delivery_caps ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON soloestate.delivery_caps FROM PUBLIC;

ALTER TABLE soloestate.visit_slots ADD COLUMN resource_id uuid NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001' REFERENCES soloestate.resources(id);
ALTER TABLE soloestate.visit_slots DROP CONSTRAINT visit_slots_tstzrange_excl;
ALTER TABLE soloestate.visit_slots ADD CONSTRAINT visit_slots_resource_window_excl EXCLUDE USING gist (resource_id WITH =, tstzrange(starts_at,ends_at) WITH &&);
ALTER TABLE soloestate.visit_slots ADD CONSTRAINT visit_slots_identity UNIQUE (id,resource_id);

ALTER TABLE soloestate.bookings ADD COLUMN resource_id uuid NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001' REFERENCES soloestate.resources(id);
DROP INDEX soloestate.one_active_booking_per_slot;
CREATE UNIQUE INDEX one_active_booking_per_slot_resource ON soloestate.bookings(slot_id,resource_id) WHERE status='confirmed';
ALTER TABLE soloestate.bookings ADD CONSTRAINT bookings_slot_resource_fkey FOREIGN KEY (slot_id,resource_id) REFERENCES soloestate.visit_slots(id,resource_id);
CREATE INDEX bookings_resource_confirmed ON soloestate.bookings(resource_id) WHERE status='confirmed';

CREATE OR REPLACE FUNCTION soloestate.guard_approval() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, soloestate AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.status<>'pending' OR NEW.decided_at IS NOT NULL THEN
            RAISE EXCEPTION 'Approvals must start pending' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF ROW(NEW.id,NEW.run_id,NEW.kind,NEW.lead_id,NEW.draft_id,NEW.slot_id,NEW.property_id,
           NEW.idempotency_key,NEW.payload,NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.id,OLD.run_id,OLD.kind,OLD.lead_id,OLD.draft_id,OLD.slot_id,OLD.property_id,
           OLD.idempotency_key,OLD.payload,OLD.created_at) THEN
        RAISE EXCEPTION 'Approval snapshot is immutable' USING ERRCODE='23514';
    END IF;
    IF NEW.status=OLD.status THEN
        IF NEW.decided_at IS DISTINCT FROM OLD.decided_at THEN
            RAISE EXCEPTION 'Decision timestamp is immutable' USING ERRCODE='23514';
        END IF;
    ELSIF OLD.status='pending' AND NEW.status IN ('approved','rejected') THEN
        IF NOT EXISTS (SELECT 1 FROM soloestate.approval_decisions d
            WHERE d.approval_id=OLD.id AND d.decision=NEW.status AND d.decided_at=NEW.decided_at) THEN
            RAISE EXCEPTION 'Matching approval decision evidence is required' USING ERRCODE='23514';
        END IF;
    ELSIF OLD.status='approved' AND NEW.status='consumed' AND OLD.kind='booking' THEN
        IF NEW.decided_at IS DISTINCT FROM OLD.decided_at OR NOT EXISTS
            (SELECT 1 FROM soloestate.bookings b WHERE b.approval_id=OLD.id AND b.status='confirmed') THEN
            RAISE EXCEPTION 'Confirmed booking evidence is required' USING ERRCODE='23514';
        END IF;
    ELSE
        RAISE EXCEPTION 'Invalid approval transition' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION soloestate.guard_booking() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, soloestate AS $$
DECLARE a soloestate.approvals; s soloestate.visit_slots; p soloestate.properties; l soloestate.leads;
    r soloestate.resources; v text; newbuf int; defbuf int;
BEGIN
    IF TG_OP='UPDATE' THEN
        IF ROW(NEW.id,NEW.run_id,NEW.approval_id,NEW.approval_kind,NEW.lead_id,NEW.slot_id,
               NEW.property_id,NEW.resource_id,NEW.idempotency_key,NEW.created_at)
           IS DISTINCT FROM ROW(OLD.id,OLD.run_id,OLD.approval_id,OLD.approval_kind,OLD.lead_id,OLD.slot_id,
               OLD.property_id,OLD.resource_id,OLD.idempotency_key,OLD.created_at)
           OR (NEW.status<>OLD.status AND NOT (OLD.status='confirmed' AND NEW.status='cancelled')) THEN
            RAISE EXCEPTION 'Booking identity/transition is immutable' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    SELECT * INTO a FROM soloestate.approvals WHERE id=NEW.approval_id FOR UPDATE;
    IF NOT FOUND OR a.kind<>'booking' OR a.status<>'approved' OR NEW.status<>'confirmed'
        OR ROW(a.run_id,a.lead_id,a.property_id,a.slot_id) IS DISTINCT FROM ROW(NEW.run_id,NEW.lead_id,NEW.property_id,NEW.slot_id)
        OR NOT EXISTS (SELECT 1 FROM soloestate.approval_decisions WHERE approval_id=a.id AND decision='approved') THEN
        RAISE EXCEPTION 'Matching approved booking is required' USING ERRCODE='23514';
    END IF;
    SELECT * INTO r FROM soloestate.resources WHERE id=NEW.resource_id FOR UPDATE;
    SELECT * INTO s FROM soloestate.visit_slots WHERE id=NEW.slot_id FOR UPDATE;
    SELECT * INTO p FROM soloestate.properties WHERE id=NEW.property_id FOR UPDATE;
    SELECT * INTO l FROM soloestate.leads WHERE id=NEW.lead_id FOR SHARE;
    v := COALESCE(a.payload->>'snapshot_version','1');
    IF r.id IS NULL OR NOT r.active OR s.id IS NULL OR p.id IS NULL OR l.id IS NULL OR NOT s.enabled OR NOT p.active OR s.starts_at<=clock_timestamp()
        OR NEW.resource_id IS DISTINCT FROM s.resource_id
        OR (a.payload->>'starts_at')::timestamptz IS DISTINCT FROM s.starts_at
        OR (a.payload->>'ends_at')::timestamptz IS DISTINCT FROM s.ends_at
        OR (a.payload->>'price')::numeric IS DISTINCT FROM p.price
        OR a.payload->>'property_title' IS DISTINCT FROM p.title
        OR a.payload->>'recipient' IS DISTINCT FROM l.email
        OR (v<>'1' AND (a.payload->>'resource_id')::uuid IS DISTINCT FROM NEW.resource_id) THEN
        RAISE EXCEPTION 'Approved booking details are stale or unavailable' USING ERRCODE='23514';
    END IF;
    SELECT buffer_minutes INTO defbuf FROM soloestate.business_policies WHERE id='default';
    newbuf := COALESCE((a.payload->>'buffer_min')::int,COALESCE(defbuf,0));
    IF EXISTS (
        SELECT 1 FROM soloestate.bookings b
        JOIN soloestate.visit_slots bs ON bs.id=b.slot_id
        JOIN soloestate.approvals ba ON ba.id=b.approval_id
        WHERE b.status='confirmed' AND b.resource_id=NEW.resource_id AND b.id<>NEW.id
          AND (bs.starts_at < s.ends_at + make_interval(mins=>newbuf)
               AND s.starts_at - make_interval(mins=>COALESCE((ba.payload->>'buffer_min')::int,COALESCE(defbuf,0))) < bs.ends_at
           OR s.starts_at < bs.ends_at + make_interval(mins=>COALESCE((ba.payload->>'buffer_min')::int,COALESCE(defbuf,0)))
               AND bs.starts_at - make_interval(mins=>newbuf) < s.ends_at)
    ) THEN
        RAISE EXCEPTION 'Resource is already booked for that window' USING ERRCODE='23514';
    END IF;
    IF p.concurrent_visit_capacity IS NOT NULL AND (
        SELECT count(*) FROM soloestate.bookings b JOIN soloestate.visit_slots bs ON bs.id=b.slot_id
        WHERE b.status='confirmed' AND b.property_id=NEW.property_id AND b.id<>NEW.id
          AND bs.starts_at < s.ends_at AND s.starts_at < bs.ends_at
    ) >= p.concurrent_visit_capacity THEN
        RAISE EXCEPTION 'Site capacity is full for that window' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION soloestate.guard_slot() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, soloestate AS $$
BEGIN
    IF TG_OP='DELETE' OR ROW(NEW.starts_at,NEW.ends_at,NEW.resource_id) IS DISTINCT FROM ROW(OLD.starts_at,OLD.ends_at,OLD.resource_id) THEN
        IF EXISTS (SELECT 1 FROM soloestate.bookings WHERE slot_id=OLD.id AND status='confirmed') THEN
            RAISE EXCEPTION 'Cannot change a confirmed appointment window' USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION soloestate.guard_approval(),soloestate.guard_booking(),soloestate.guard_slot() FROM PUBLIC;

ALTER TABLE soloestate.runs DROP CONSTRAINT runs_origin_check;
ALTER TABLE soloestate.runs ADD CONSTRAINT runs_origin_check CHECK (origin IN ('agent','owner','autonomy'));
ALTER TABLE soloestate.approval_decisions DROP CONSTRAINT approval_decisions_actor_check;
ALTER TABLE soloestate.approval_decisions ADD CONSTRAINT approval_decisions_actor_check CHECK (actor IN ('owner','policy'));
ALTER TABLE soloestate.crm_operations DROP CONSTRAINT crm_operations_actor_check;
ALTER TABLE soloestate.crm_operations ADD CONSTRAINT crm_operations_actor_check CHECK (actor IN ('owner','visitor','channel','policy'));
ALTER TABLE soloestate.lead_activities DROP CONSTRAINT lead_activities_actor_check;
ALTER TABLE soloestate.lead_activities ADD CONSTRAINT lead_activities_actor_check CHECK (actor IN ('owner','visitor','channel','policy'));

ALTER TABLE soloestate.leads ADD COLUMN phone text NULL CHECK(phone IS NULL OR phone ~ '^\+[1-9][0-9]{7,14}$');
ALTER TABLE soloestate.leads ADD COLUMN whatsapp_permission text NOT NULL DEFAULT 'unknown' CHECK(whatsapp_permission IN ('unknown','opted_in','opted_out'));
ALTER TABLE soloestate.properties ADD COLUMN concurrent_visit_capacity int NULL CHECK(concurrent_visit_capacity IS NULL OR concurrent_visit_capacity>0);

ALTER TABLE soloestate.conversation_messages ADD COLUMN seq bigint NULL;
UPDATE soloestate.conversation_messages m SET seq = ranked.rn FROM
    (SELECT id, row_number() OVER (PARTITION BY conversation_id ORDER BY created_at, id) AS rn
     FROM soloestate.conversation_messages) ranked WHERE ranked.id = m.id;
ALTER TABLE soloestate.conversation_messages ALTER COLUMN seq SET NOT NULL;
ALTER TABLE soloestate.conversation_messages ADD CONSTRAINT conversation_messages_conversation_seq_key UNIQUE (conversation_id, seq);
