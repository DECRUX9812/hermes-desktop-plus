CREATE TABLE tool_activity (
    session_id TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    event_name TEXT NOT NULL CHECK (event_name IN ('tool.start', 'tool.generating', 'tool.complete')),
    tool_name TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (session_id, tool_call_id, event_name)
);

CREATE INDEX tool_activity_session
ON tool_activity (session_id, created_at);
