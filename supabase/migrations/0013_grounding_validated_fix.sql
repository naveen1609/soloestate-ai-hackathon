-- 0013_grounding_validated_fix.sql (D2 corrective): finish the grounding widen.
--
-- 0012 intended to widen conversation_messages.grounding with 'validated',
-- but its guard matched the constraint definition against a literal IN while
-- PostgreSQL stores it as "= ANY (ARRAY[...])", so the old three-value CHECK
-- survived. 0012 is checksummed and applied, so it stays immutable; this
-- additive migration performs the replacement it intended. Existing rows
-- satisfy the new CHECK (a superset); nothing else changes.

DO $$
DECLARE
    cname TEXT;
BEGIN
    SELECT conname INTO cname FROM pg_constraint
        WHERE conrelid = 'soloestate.conversation_messages'::regclass AND contype = 'c'
          AND conname = 'conversation_messages_grounding_check' LIMIT 1;
    IF cname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE soloestate.conversation_messages DROP CONSTRAINT %I', cname);
    END IF;
    ALTER TABLE soloestate.conversation_messages
        ADD CONSTRAINT conversation_messages_grounding_check
        CHECK (grounding IN ('grounded', 'unsupported', 'ungrounded', 'validated'));
END $$;
