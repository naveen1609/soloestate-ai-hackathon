-- Additive B5 foundation: visit outcomes and visit follow-up state. No delivery or automation.
ALTER TABLE soloestate.bookings
    ADD COLUMN visit_outcome text NULL CHECK (visit_outcome IN ('completed','no_show')),
    ADD COLUMN outcome_note text NULL CHECK (outcome_note IS NULL OR length(btrim(outcome_note)) BETWEEN 1 AND 2000),
    ADD COLUMN followup_state text NOT NULL DEFAULT 'none' CHECK (followup_state IN ('none','pending','done')),
    ADD COLUMN followup_note text NULL CHECK (followup_note IS NULL OR length(btrim(followup_note)) BETWEEN 1 AND 2000);
ALTER TABLE soloestate.bookings ADD CONSTRAINT bookings_outcome_requires_confirmed CHECK (visit_outcome IS NULL OR status='confirmed');
ALTER TABLE soloestate.bookings ADD CONSTRAINT bookings_followup_note_shape CHECK ((followup_state='none') = (followup_note IS NULL));
