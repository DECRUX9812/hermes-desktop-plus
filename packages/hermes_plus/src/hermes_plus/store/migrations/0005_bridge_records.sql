-- Bridge-record tables: everything the bridge plugin spools and the sidecar ingests.

CREATE TABLE tool_calls (
    tool_call_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL DEFAULT '',
    tool_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    error_type TEXT,
    error_message TEXT,
    args_json TEXT,
    result_excerpt TEXT,
    duration_ms INTEGER,
    created_at REAL NOT NULL
);
CREATE INDEX tool_calls_session ON tool_calls (session_id, created_at);

CREATE TABLE delegation_reports (
    delegation_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    summary_source TEXT NOT NULL DEFAULT 'heuristic',
    task_count INTEGER NOT NULL DEFAULT 0,
    completed_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    duration_seconds REAL,
    completed_at REAL,
    delivery_state TEXT NOT NULL DEFAULT '',
    child_session_ids_json TEXT NOT NULL DEFAULT '{}',
    group_name TEXT
);
CREATE INDEX delegation_reports_session ON delegation_reports (session_id, completed_at);

CREATE TABLE browser_thumbnails (
    tool_call_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL DEFAULT '',
    tool_name TEXT NOT NULL DEFAULT '',
    url TEXT,
    title TEXT,
    captured_at REAL,
    image_path TEXT,
    snapshot TEXT
);

CREATE TABLE spool_offsets (
    filename TEXT PRIMARY KEY,
    offset INTEGER NOT NULL DEFAULT 0,
    ingested_at REAL NOT NULL
);
