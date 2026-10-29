-- Additive B1 foundation; no inferred consent or fabricated historical events.
ALTER TABLE soloestate.leads
 ADD COLUMN journey_stage text NOT NULL DEFAULT 'new' CHECK (journey_stage IN ('new','qualifying','qualified','disqualified','closed')),
 ADD COLUMN contact_preference text NOT NULL DEFAULT 'unspecified' CHECK (contact_preference IN ('unspecified','email','phone','none')),
 ADD COLUMN contact_permission text NOT NULL DEFAULT 'unknown' CHECK (contact_permission IN ('unknown','opted_in','opted_out')),
 ADD COLUMN handoff_state text NOT NULL DEFAULT 'none' CHECK (handoff_state IN ('none','requested','in_progress','resolved'));

CREATE TABLE soloestate.lead_activities (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 lead_id uuid NOT NULL REFERENCES soloestate.leads(id),
 actor text NOT NULL CHECK (actor='owner'),
 reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 1000),
 before_state jsonb NOT NULL,
 after_state jsonb NOT NULL,
 request_key text NOT NULL UNIQUE REFERENCES soloestate.crm_operations(request_key) DEFERRABLE INITIALLY DEFERRED,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX lead_activities_lead_time ON soloestate.lead_activities(lead_id,created_at DESC,id DESC);
ALTER TABLE soloestate.lead_activities ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON soloestate.lead_activities FROM PUBLIC;
