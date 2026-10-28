-- Store only hashes of one-time login tickets and browser sessions.
CREATE TABLE soloestate.dashboard_sessions (
    token_hash text PRIMARY KEY CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    kind text NOT NULL CHECK (kind IN ('ticket','session')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    revoked_at timestamptz,
    CHECK (expires_at > created_at),
    CHECK (kind='ticket' OR consumed_at IS NULL),
    CHECK (kind='session' OR revoked_at IS NULL)
);
ALTER TABLE soloestate.dashboard_sessions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON soloestate.dashboard_sessions FROM PUBLIC;
CREATE INDEX dashboard_sessions_expiry ON soloestate.dashboard_sessions(expires_at);
