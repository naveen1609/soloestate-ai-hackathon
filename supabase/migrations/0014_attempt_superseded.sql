-- 0014_attempt_superseded.sql (D2): owner resolution terminal state for attempts.
--
-- Contract §10 requires both the single-open-attempt partial unique index
-- and owner-authorized resend as a NEW attempt row on the same outbox row.
-- Resolving an unknown attempt (resend/abandon) must therefore close the old
-- open attempt without rewriting its history: 'superseded' is that terminal
-- marker. Additive only: the status CHECK gains one value; no rows change.
DO $$
DECLARE
    cname TEXT;
BEGIN
    SELECT conname INTO cname FROM pg_constraint
        WHERE conrelid = 'soloestate.delivery_attempts'::regclass AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%status%staged%' LIMIT 1;
    IF cname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE soloestate.delivery_attempts DROP CONSTRAINT %I', cname);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'delivery_attempts_status_check') THEN
        ALTER TABLE soloestate.delivery_attempts
            ADD CONSTRAINT delivery_attempts_status_check CHECK (status IN
            ('staged', 'submitting', 'acknowledged', 'failed', 'unknown', 'blocked', 'superseded'));
    END IF;
END $$;
