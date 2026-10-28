-- Additive CRM metadata. Existing identifiers, consent snapshots and slots are preserved.
ALTER TABLE soloestate.leads ADD COLUMN archived_at timestamptz;
ALTER TABLE soloestate.runs ADD COLUMN origin text NOT NULL DEFAULT 'agent' CHECK(origin IN ('agent','owner'));
ALTER TABLE soloestate.bookings ADD COLUMN replaces_booking_id uuid UNIQUE REFERENCES soloestate.bookings(id),
    ADD COLUMN cancellation_reason text;
ALTER TABLE soloestate.leads ADD COLUMN version integer NOT NULL DEFAULT 1 CHECK(version>0), ADD COLUMN updated_at timestamptz NOT NULL DEFAULT clock_timestamp();
ALTER TABLE soloestate.properties ADD COLUMN version integer NOT NULL DEFAULT 1 CHECK(version>0), ADD COLUMN updated_at timestamptz NOT NULL DEFAULT clock_timestamp();
ALTER TABLE soloestate.bookings ADD COLUMN version integer NOT NULL DEFAULT 1 CHECK(version>0), ADD COLUMN updated_at timestamptz NOT NULL DEFAULT clock_timestamp();
ALTER TABLE soloestate.visit_slots ADD COLUMN version integer NOT NULL DEFAULT 1 CHECK(version>0), ADD COLUMN updated_at timestamptz NOT NULL DEFAULT clock_timestamp();
CREATE FUNCTION soloestate.crm_version() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,soloestate AS $$
BEGIN NEW.version=OLD.version+1; NEW.updated_at=clock_timestamp(); RETURN NEW; END $$;
CREATE TRIGGER crm_version BEFORE UPDATE ON soloestate.leads FOR EACH ROW EXECUTE FUNCTION soloestate.crm_version();
CREATE TRIGGER crm_version BEFORE UPDATE ON soloestate.properties FOR EACH ROW EXECUTE FUNCTION soloestate.crm_version();
CREATE TRIGGER crm_version BEFORE UPDATE ON soloestate.bookings FOR EACH ROW EXECUTE FUNCTION soloestate.crm_version();
CREATE TRIGGER crm_version BEFORE UPDATE ON soloestate.visit_slots FOR EACH ROW EXECUTE FUNCTION soloestate.crm_version();
CREATE FUNCTION soloestate.crm_booking_link() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,soloestate AS $$
BEGIN
 IF TG_OP='INSERT' AND NEW.replaces_booking_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM soloestate.bookings WHERE id=NEW.replaces_booking_id AND status='cancelled' AND lead_id=NEW.lead_id
 ) THEN RAISE EXCEPTION 'Replacement requires cancelled original for same customer' USING ERRCODE='23514'; END IF;
 IF TG_OP='UPDATE' AND NEW.replaces_booking_id IS DISTINCT FROM OLD.replaces_booking_id THEN
  RAISE EXCEPTION 'Replacement identity is immutable' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER crm_booking_link BEFORE INSERT OR UPDATE ON soloestate.bookings FOR EACH ROW EXECUTE FUNCTION soloestate.crm_booking_link();
CREATE TABLE soloestate.crm_operations(
 request_key text PRIMARY KEY CHECK(length(request_key) BETWEEN 1 AND 128),
 action text NOT NULL, fingerprint text NOT NULL, actor text NOT NULL CHECK(actor='owner'),
 entity_id uuid, response jsonb NOT NULL CHECK(jsonb_typeof(response)='object'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE soloestate.crm_operations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON soloestate.crm_operations FROM PUBLIC;
REVOKE ALL ON FUNCTION soloestate.crm_version(),soloestate.crm_booking_link() FROM PUBLIC;
CREATE INDEX crm_leads_status ON soloestate.leads(status,created_at) WHERE archived_at IS NULL;
CREATE INDEX crm_bookings_lead ON soloestate.bookings(lead_id);
CREATE INDEX crm_bookings_property ON soloestate.bookings(property_id);
