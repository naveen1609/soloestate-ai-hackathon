-- Additive C foundation: truthful visitor provenance on operation/activity evidence. No new tables.
ALTER TABLE soloestate.crm_operations DROP CONSTRAINT crm_operations_actor_check;
ALTER TABLE soloestate.crm_operations ADD CONSTRAINT crm_operations_actor_check CHECK (actor IN ('owner','visitor'));
ALTER TABLE soloestate.lead_activities DROP CONSTRAINT lead_activities_actor_check;
ALTER TABLE soloestate.lead_activities ADD CONSTRAINT lead_activities_actor_check CHECK (actor IN ('owner','visitor'));
