ALTER TABLE soloestate.runs
    ADD COLUMN execution_state jsonb CHECK (jsonb_typeof(execution_state)='object'),
    ADD COLUMN lease_token uuid,
    ADD COLUMN lease_until timestamptz,
    ADD COLUMN resume_count integer NOT NULL DEFAULT 0 CHECK (resume_count BETWEEN 0 AND 3);

CREATE TABLE soloestate.approval_decisions (
    id uuid PRIMARY KEY,
    approval_id uuid NOT NULL UNIQUE REFERENCES soloestate.approvals(id),
    idempotency_key text NOT NULL UNIQUE CHECK (length(btrim(idempotency_key)) BETWEEN 1 AND 128),
    decision text NOT NULL CHECK (decision IN ('approved','rejected')),
    payload_hash text NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    actor text NOT NULL CHECK (actor='owner'),
    decided_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE soloestate.approval_decisions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON soloestate.approval_decisions FROM PUBLIC;

CREATE FUNCTION soloestate.guard_approval() RETURNS trigger LANGUAGE plpgsql
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
            RAISE EXCEPTION 'Owner decision evidence is required' USING ERRCODE='23514';
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
CREATE TRIGGER approval_integrity BEFORE INSERT OR UPDATE ON soloestate.approvals
    FOR EACH ROW EXECUTE FUNCTION soloestate.guard_approval();

CREATE FUNCTION soloestate.guard_draft() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, soloestate AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.status<>'draft' OR NEW.version<>1 THEN
            RAISE EXCEPTION 'New messages must be version-one drafts' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF ROW(NEW.id,NEW.run_id,NEW.lead_id,NEW.channel,NEW.idempotency_key,NEW.created_at)
       IS DISTINCT FROM ROW(OLD.id,OLD.run_id,OLD.lead_id,OLD.channel,OLD.idempotency_key,OLD.created_at) THEN
        RAISE EXCEPTION 'Draft identity is immutable' USING ERRCODE='23514';
    END IF;
    IF ROW(NEW.subject,NEW.body,NEW.version) IS DISTINCT FROM ROW(OLD.subject,OLD.body,OLD.version) THEN
        IF OLD.status<>'draft' OR NEW.status<>'draft' OR NEW.version<>OLD.version+1
            OR EXISTS (SELECT 1 FROM soloestate.approvals WHERE draft_id=OLD.id) THEN
            RAISE EXCEPTION 'Reviewed draft content is immutable; create a fresh draft' USING ERRCODE='23514';
        END IF;
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF OLD.status<>'draft' OR NEW.status NOT IN ('approved','rejected') OR NOT EXISTS (
            SELECT 1 FROM soloestate.approvals a JOIN soloestate.approval_decisions d ON d.approval_id=a.id
            WHERE a.draft_id=OLD.id AND a.status=NEW.status AND d.decision=NEW.status) THEN
            RAISE EXCEPTION 'Message state requires owner evidence; delivery is not implemented' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER draft_integrity BEFORE INSERT OR UPDATE ON soloestate.followup_drafts
    FOR EACH ROW EXECUTE FUNCTION soloestate.guard_draft();

CREATE FUNCTION soloestate.guard_booking() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, soloestate AS $$
DECLARE a soloestate.approvals; s soloestate.visit_slots; p soloestate.properties; l soloestate.leads;
BEGIN
    IF TG_OP='UPDATE' THEN
        IF ROW(NEW.id,NEW.run_id,NEW.approval_id,NEW.approval_kind,NEW.lead_id,NEW.slot_id,
               NEW.property_id,NEW.idempotency_key,NEW.created_at)
           IS DISTINCT FROM ROW(OLD.id,OLD.run_id,OLD.approval_id,OLD.approval_kind,OLD.lead_id,OLD.slot_id,
               OLD.property_id,OLD.idempotency_key,OLD.created_at)
           OR (NEW.status<>OLD.status AND NOT (OLD.status='confirmed' AND NEW.status='cancelled')) THEN
            RAISE EXCEPTION 'Booking identity/transition is immutable' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    SELECT * INTO a FROM soloestate.approvals WHERE id=NEW.approval_id FOR UPDATE;
    IF NOT FOUND OR a.kind<>'booking' OR a.status<>'approved' OR NEW.status<>'confirmed'
        OR ROW(a.run_id,a.lead_id,a.property_id,a.slot_id) IS DISTINCT FROM ROW(NEW.run_id,NEW.lead_id,NEW.property_id,NEW.slot_id)
        OR NOT EXISTS (SELECT 1 FROM soloestate.approval_decisions WHERE approval_id=a.id AND decision='approved') THEN
        RAISE EXCEPTION 'Matching owner-approved booking is required' USING ERRCODE='23514';
    END IF;
    SELECT * INTO s FROM soloestate.visit_slots WHERE id=NEW.slot_id FOR UPDATE;
    SELECT * INTO p FROM soloestate.properties WHERE id=NEW.property_id FOR SHARE;
    SELECT * INTO l FROM soloestate.leads WHERE id=NEW.lead_id FOR SHARE;
    IF s.id IS NULL OR p.id IS NULL OR l.id IS NULL OR NOT s.enabled OR NOT p.active OR s.starts_at<=clock_timestamp()
        OR (a.payload->>'starts_at')::timestamptz IS DISTINCT FROM s.starts_at
        OR (a.payload->>'ends_at')::timestamptz IS DISTINCT FROM s.ends_at
        OR (a.payload->>'price')::numeric IS DISTINCT FROM p.price
        OR a.payload->>'property_title' IS DISTINCT FROM p.title
        OR a.payload->>'recipient' IS DISTINCT FROM l.email THEN
        RAISE EXCEPTION 'Approved booking details are stale or unavailable' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER booking_integrity BEFORE INSERT OR UPDATE ON soloestate.bookings
    FOR EACH ROW EXECUTE FUNCTION soloestate.guard_booking();

CREATE FUNCTION soloestate.guard_slot() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, soloestate AS $$
BEGIN
    IF TG_OP='DELETE' OR ROW(NEW.starts_at,NEW.ends_at) IS DISTINCT FROM ROW(OLD.starts_at,OLD.ends_at) THEN
        IF EXISTS (SELECT 1 FROM soloestate.bookings WHERE slot_id=OLD.id AND status='confirmed') THEN
            RAISE EXCEPTION 'Cannot change a confirmed appointment window' USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER slot_integrity BEFORE UPDATE OR DELETE ON soloestate.visit_slots
    FOR EACH ROW EXECUTE FUNCTION soloestate.guard_slot();
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA soloestate FROM PUBLIC;
