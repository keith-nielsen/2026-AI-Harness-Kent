-- =============================================================================
-- Kent — stack.sql
-- Database: /home/enterprise/stacks/<uuid8>/data/stack.db
-- Owner: gent-<uuid8>:gent-<uuid8> (0600)
-- Purpose: Per-Gent project state — kanban, checkpoints, memory, learnings.
--          Gent CEO has R/W. Kent reads shared_learnings only.
-- =============================================================================

-- Kanban: durable task board with heartbeat and zombie detection.
CREATE TABLE IF NOT EXISTS kanban (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'backlog', -- backlog, in_progress, review, done, failed
    priority INTEGER DEFAULT 0,
    assigned_to TEXT,                       -- worker role name
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    heartbeat_at TEXT,                      -- last worker checkin
    retry_count INTEGER DEFAULT 0,
    parent_task_id TEXT,                    -- subtask hierarchy
    routed_tier TEXT,
    output_summary TEXT,
    error_text TEXT
);

-- CrewAI checkpoints: preserved on circuit break for Human resume.
CREATE TABLE IF NOT EXISTS crew_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    checkpoint_data TEXT NOT NULL,          -- JSON blob: flow state, partial outputs
    created_at TEXT NOT NULL,
    resumed INTEGER DEFAULT 0
);

-- Project memory: CEO's accumulated domain expertise.
CREATE TABLE IF NOT EXISTS project_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    source TEXT,                            -- 'observation', 'human_input', 'seed_import'
    created_at TEXT NOT NULL,
    updated_at TEXT
);

-- System change log: modifications made by CEO or workers to project assets.
CREATE TABLE IF NOT EXISTS system_change_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    entity TEXT NOT NULL,                   -- CEO role, worker role
    action TEXT NOT NULL,                   -- file_create, file_edit, git_commit, config_change
    target TEXT NOT NULL,                   -- filepath, repo, config key
    summary TEXT NOT NULL,
    diff_hash TEXT                          -- SHA-256 of the diff
);

-- Shared learnings: CEO publishes, Kent reads and evaluates.
-- This table is the ONLY part of stack.db that Kent accesses.
CREATE TABLE IF NOT EXISTS shared_learnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    category TEXT NOT NULL,                 -- 'technique', 'pattern', 'pitfall',
                                           -- 'escalation_request', 'access_request'
    summary TEXT NOT NULL,
    detail TEXT,
    confidence REAL DEFAULT 0.5,
    applied_locally INTEGER DEFAULT 0,     -- CEO used it in this project
    reviewed INTEGER DEFAULT 0,            -- Kent has evaluated
    adopted INTEGER,                       -- NULL=pending, 1=adopted, 0=rejected
    review_notes TEXT,                     -- Kent's evaluation reasoning
    cross_pollinated_to TEXT               -- comma-separated stack_ids
);

-- Seed context: imported from an archived Gent during seed-from.
CREATE TABLE IF NOT EXISTS seed_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_stack_id TEXT NOT NULL,
    source_display_name TEXT,
    source_archived_at TEXT,
    imported_at TEXT NOT NULL,
    import_type TEXT NOT NULL,             -- 'domain_learning', 'project_memory',
                                          -- 'kanban_summary'
    content TEXT NOT NULL,
    relevance_notes TEXT,                  -- Kent's assessment of why imported
    stale_flag INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_kanban_status ON kanban(status);
CREATE INDEX IF NOT EXISTS idx_kanban_heartbeat ON kanban(heartbeat_at);
CREATE INDEX IF NOT EXISTS idx_learnings_reviewed ON shared_learnings(reviewed, adopted);
CREATE INDEX IF NOT EXISTS idx_seed_source ON seed_context(source_stack_id);
CREATE INDEX IF NOT EXISTS idx_changelog_ts ON system_change_log(timestamp);
