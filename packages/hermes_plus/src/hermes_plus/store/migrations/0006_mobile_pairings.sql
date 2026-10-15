-- Mobile companion pairing codes (fork web_routers/mobile.py pairing flow).
-- Persisted so a minted code survives a sidecar restart; only the code's
-- SHA-256 is stored, and a code is deleted when spent (single use).
CREATE TABLE mobile_pairings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code_hash TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    paired_at REAL
);

CREATE INDEX idx_mobile_pairings_expires ON mobile_pairings(expires_at);
