CREATE TABLE mobile_pairings (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    pairing_code TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'paired', 'expired', 'revoked')),
    device_name TEXT NOT NULL DEFAULT '',
    device_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    paired_at REAL,
    revoked_at REAL
);
CREATE INDEX mobile_pairings_code ON mobile_pairings (code);
CREATE INDEX mobile_pairings_device ON mobile_pairings (device_id);
CREATE INDEX mobile_pairings_session ON mobile_pairings (session_id);
