CREATE TABLE share_grants (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    shared_at REAL NOT NULL,
    revoked_at REAL,
    created_by TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT ''
);
CREATE INDEX share_grants_session ON share_grants (session_id, shared_at DESC);
CREATE INDEX share_grants_revoked ON share_grants (revoked_at) WHERE revoked_at IS NULL;
