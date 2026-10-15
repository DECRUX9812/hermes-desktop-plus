CREATE TABLE routine_meta (
    profile TEXT NOT NULL,
    job_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('schedule', 'watch')),
    condition TEXT,
    PRIMARY KEY (profile, job_id)
);

CREATE TABLE setup_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL,
    created_at REAL NOT NULL
);
