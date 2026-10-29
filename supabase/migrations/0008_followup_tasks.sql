-- Additive B4 foundation: owner-managed follow-up tasks. No delivery or scheduler.
CREATE TABLE soloestate.followup_tasks (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 lead_id uuid NOT NULL REFERENCES soloestate.leads(id),
 property_id uuid NULL REFERENCES soloestate.properties(id),
 title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 200),
 notes text NOT NULL DEFAULT '' CHECK (length(notes) <= 5000),
 due_at timestamptz NULL,
 assignee text NOT NULL DEFAULT 'owner' CHECK (length(btrim(assignee)) BETWEEN 1 AND 120),
 status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','in_review','done','cancelled')),
 outcome text NULL CHECK (outcome IS NULL OR length(btrim(outcome)) BETWEEN 1 AND 2000),
 version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK ((status = 'done') = (outcome IS NOT NULL))
);
CREATE INDEX followup_tasks_lead_time ON soloestate.followup_tasks(lead_id,created_at DESC,id DESC);
CREATE TRIGGER crm_version BEFORE UPDATE ON soloestate.followup_tasks FOR EACH ROW EXECUTE FUNCTION soloestate.crm_version();
ALTER TABLE soloestate.followup_tasks ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON soloestate.followup_tasks FROM PUBLIC;
