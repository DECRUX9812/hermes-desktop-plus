CREATE TABLE plus_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE approvals (
    request_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    tool_call_id TEXT NOT NULL DEFAULT '',
    tool_name TEXT NOT NULL DEFAULT '',
    command TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'allowed', 'denied', 'expired', 'withdrawn')),
    decided_at REAL,
    choice TEXT,
    ran_at REAL,
    reason TEXT
);
CREATE INDEX approvals_session ON approvals (session_id, created_at);

CREATE TABLE bot_identities (
    profile TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    instructions TEXT NOT NULL DEFAULT '',
    model_provider TEXT NOT NULL DEFAULT '',
    model_default TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);

CREATE TABLE memory_notes (
    profile TEXT NOT NULL,
    id TEXT NOT NULL,
    target TEXT NOT NULL CHECK (target IN ('memory', 'user')),
    text TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (profile, id),
    UNIQUE (profile, target, text)
);

CREATE TABLE onboarding_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    intro TEXT NOT NULL DEFAULT 'unseen' CHECK (intro IN ('unseen', 'seen')),
    failed_starts INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT
);

CREATE TABLE bot_teams (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    mission TEXT NOT NULL DEFAULT '',
    lead_decides INTEGER NOT NULL DEFAULT 0,
    channels_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE team_members (
    team_id TEXT NOT NULL REFERENCES bot_teams (id) ON DELETE CASCADE,
    slot TEXT NOT NULL,
    position INTEGER NOT NULL,
    profile TEXT,
    role TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    skills_json TEXT NOT NULL DEFAULT '[]',
    plugins_json TEXT NOT NULL DEFAULT '[]',
    credentials_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused')),
    reports_to TEXT,
    is_lead INTEGER NOT NULL DEFAULT 0,
    monthly_usd REAL,
    hard_stop INTEGER NOT NULL DEFAULT 1,
    recorded_usd REAL NOT NULL DEFAULT 0,
    measured_usd REAL NOT NULL DEFAULT 0,
    spent_usd REAL NOT NULL DEFAULT 0,
    synced_at INTEGER NOT NULL DEFAULT 0,
    period TEXT NOT NULL,
    joined_at INTEGER NOT NULL,
    PRIMARY KEY (team_id, slot),
    UNIQUE (team_id, profile)
);

CREATE TABLE team_goals (
    team_id TEXT NOT NULL REFERENCES bot_teams (id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    position INTEGER NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    parent_id TEXT,
    owner_slot TEXT,
    status TEXT NOT NULL CHECK (status IN ('open', 'active', 'blocked', 'done', 'cancelled')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (team_id, id)
);

CREATE TABLE team_goal_tasks (
    team_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    task_id TEXT NOT NULL,
    PRIMARY KEY (team_id, goal_id, task_id),
    FOREIGN KEY (team_id, goal_id) REFERENCES team_goals (team_id, id) ON DELETE CASCADE
);

CREATE TABLE team_approvals (
    team_id TEXT NOT NULL REFERENCES bot_teams (id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    position INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('hire', 'spend', 'credential', 'action')),
    subject TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    payload_json TEXT,
    requested_by TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    decided_by TEXT,
    decided_at INTEGER,
    note TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    PRIMARY KEY (team_id, id)
);

CREATE TABLE team_learnings (
    team_id TEXT NOT NULL REFERENCES bot_teams (id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    position INTEGER NOT NULL,
    text TEXT NOT NULL,
    norm_key TEXT NOT NULL,
    by_name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    count INTEGER NOT NULL DEFAULT 1,
    first_at INTEGER NOT NULL,
    last_at INTEGER NOT NULL,
    PRIMARY KEY (team_id, id)
);

CREATE TABLE team_budget_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id TEXT NOT NULL REFERENCES bot_teams (id) ON DELETE CASCADE,
    slot TEXT NOT NULL,
    usd REAL NOT NULL,
    actor TEXT NOT NULL,
    at INTEGER NOT NULL
);

CREATE TABLE team_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id TEXT NOT NULL,
    at INTEGER NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX team_audit_team ON team_audit (team_id, id);

CREATE TABLE mailbox_notes (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    to_json TEXT NOT NULL,
    sender_json TEXT NOT NULL,
    to_handle TEXT NOT NULL DEFAULT '',
    sender_handle TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    payload_json TEXT,
    status TEXT NOT NULL CHECK (status IN ('open', 'accepted', 'declined', 'done')),
    reply TEXT NOT NULL DEFAULT '',
    room TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX mailbox_notes_created ON mailbox_notes (created_at DESC, id);
