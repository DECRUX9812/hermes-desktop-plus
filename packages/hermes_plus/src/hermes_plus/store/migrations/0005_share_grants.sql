-- Revocable read-only share grants (port of fork hermes_cli/share_grants.py).
-- Only the token's SHA-256 and a display suffix are stored, so a leaked
-- plus.db is not a session oracle. The raw token exists only at mint time.
CREATE TABLE share_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    token_preview TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    expires_at REAL,
    revoked INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_share_grants_session ON share_grants(session_id);
