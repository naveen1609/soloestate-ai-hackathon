-- Additive B3 foundation: versioned FAQs and lead-linked conversations. No delivery.
CREATE TABLE soloestate.faqs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 scope text NOT NULL CHECK (scope IN ('business','property')),
 property_id uuid NULL REFERENCES soloestate.properties(id),
 status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
 current_version integer NOT NULL DEFAULT 1 CHECK (current_version >= 1),
 version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK ((scope = 'property' AND property_id IS NOT NULL) OR (scope = 'business' AND property_id IS NULL))
);
CREATE TABLE soloestate.faq_versions (
 faq_id uuid NOT NULL REFERENCES soloestate.faqs(id),
 version integer NOT NULL CHECK (version >= 1),
 question text NOT NULL CHECK (length(btrim(question)) BETWEEN 1 AND 500),
 answer text NOT NULL CHECK (length(btrim(answer)) BETWEEN 1 AND 5000),
 request_key text NOT NULL UNIQUE REFERENCES soloestate.crm_operations(request_key) DEFERRABLE INITIALLY DEFERRED,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY (faq_id, version)
);
CREATE TABLE soloestate.conversations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 lead_id uuid NOT NULL REFERENCES soloestate.leads(id),
 property_id uuid NULL REFERENCES soloestate.properties(id),
 status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','pending_owner','resolved')),
 handoff_state text NOT NULL DEFAULT 'none' CHECK (handoff_state IN ('none','requested','resolved')),
 version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX conversations_lead_time ON soloestate.conversations(lead_id,created_at DESC,id DESC);
CREATE TABLE soloestate.conversation_messages (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 conversation_id uuid NOT NULL REFERENCES soloestate.conversations(id),
 role text NOT NULL CHECK (role IN ('customer','assistant','owner','system')),
 body text NOT NULL CHECK (length(btrim(body)) BETWEEN 1 AND 5000),
 grounding text NOT NULL DEFAULT 'ungrounded' CHECK (grounding IN ('grounded','unsupported','ungrounded')),
 unsupported_reason text NULL CHECK (unsupported_reason IN ('missing_citations','invalid_citation','quote_mismatch','unauthorized_faq','inference_failed','inference_timeout','invalid_response')),
 citations jsonb NOT NULL DEFAULT '[]',
 confidence text NULL CHECK (confidence IN ('high','medium','low')),
 model_name text NULL,
 response_id text NULL,
 supersedes_message_id uuid NULL REFERENCES soloestate.conversation_messages(id),
 request_key text NOT NULL UNIQUE REFERENCES soloestate.crm_operations(request_key) DEFERRABLE INITIALLY DEFERRED,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK (grounding != 'unsupported' OR unsupported_reason IS NOT NULL),
 CHECK (grounding != 'grounded' OR jsonb_array_length(citations) > 0),
 CHECK (grounding != 'ungrounded' OR citations = '[]'::jsonb)
);
CREATE INDEX conversation_messages_conversation_time ON soloestate.conversation_messages(conversation_id,created_at,id);
CREATE TRIGGER crm_version BEFORE UPDATE ON soloestate.faqs FOR EACH ROW EXECUTE FUNCTION soloestate.crm_version();
CREATE TRIGGER crm_version BEFORE UPDATE ON soloestate.conversations FOR EACH ROW EXECUTE FUNCTION soloestate.crm_version();
ALTER TABLE soloestate.faqs ENABLE ROW LEVEL SECURITY;
ALTER TABLE soloestate.faq_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE soloestate.conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE soloestate.conversation_messages ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON soloestate.faqs, soloestate.faq_versions, soloestate.conversations, soloestate.conversation_messages FROM PUBLIC;
