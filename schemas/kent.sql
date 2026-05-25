-- =============================================================================
-- Kent — kent.sql
-- Database: /home/kent/kent.db (SQLite)
-- Owner: kent:kent (0600)
-- Purpose: All Kent-owned state — agent state, conversations, memory, goals,
--          stack registry, routing log, telemetry, QA audit, egress log.
--          Human + Kent access only. LiteLLM has its own PostgreSQL database.
-- =============================================================================

-- ═══════════════════════════════════════════════════════════════════════════════
-- AGENT STATE
-- ═══════════════════════════════════════════════════════════════════════════════

-- Stack registry: immutable record of every Gent ever spawned.
-- Rows are NEVER deleted — preserved for telemetry/audit correlation.
CREATE TABLE IF NOT EXISTS stack_registry (
    stack_id TEXT PRIMARY KEY,              -- truncated UUID (8 hex chars), immutable
    display_name TEXT NOT NULL,             -- human-readable, editable
    status TEXT NOT NULL DEFAULT 'active',  -- active, paused, archived, destroyed
    created_at TEXT NOT NULL,
    destroyed_at TEXT,
    archived_at TEXT,
    unix_user TEXT NOT NULL,                -- 'gent-<uuid8>'
    unix_uid INTEGER NOT NULL,             -- captured at creation, immutable
    gitea_repo TEXT NOT NULL,               -- 'gent-<uuid8>'
    gateway_key_hash TEXT,                  -- SHA-256 of the Gent's API key
    project_description TEXT,
    template_version TEXT,                  -- git commit hash of template at spawn
    archive_path TEXT,                      -- path to archived DB + artifacts
    seed_source_id TEXT                     -- references another stack_id (lineage)
);

-- Routing decisions made by Kent's Arch Router.
CREATE TABLE IF NOT EXISTS routing_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    task_hash TEXT NOT NULL,                -- SHA-256 of task text (no PII in log)
    task_preview TEXT,                      -- first 200 chars, redacted
    routed_tier TEXT NOT NULL,              -- fast, smart, frontier
    confidence REAL NOT NULL,
    review_required INTEGER NOT NULL,
    escalation_reason TEXT,
    actual_tier_used TEXT,                  -- may differ if escalation occurred
    outcome TEXT                            -- success, retry, escalated, circuit_break
);

-- Kent's curated memory (Hermes agent memory with FTS5 search).
CREATE VIRTUAL TABLE IF NOT EXISTS memory USING fts5(
    category,
    summary,
    content,
    source_stack_id,
    created_at UNINDEXED,
    updated_at UNINDEXED
);

-- Persistent goals (Hermes /goal Ralph loop).
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',  -- active, completed, abandoned
    priority INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    notes TEXT
);

-- Human <-> Kent conversation history.
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,                     -- human, kent
    content TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    token_count INTEGER
);

-- Template evolution log: what changed and why.
CREATE TABLE IF NOT EXISTS template_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    commit_hash TEXT NOT NULL,
    source_stack_id TEXT,
    source_learning_id INTEGER,
    change_summary TEXT NOT NULL,
    auto_applied INTEGER DEFAULT 0,        -- 1 = high-confidence auto-commit
    human_reviewed INTEGER DEFAULT 0
);

-- ═══════════════════════════════════════════════════════════════════════════════
-- TELEMETRY & AUDIT
-- Populated by Kent via LiteLLM callbacks, cron jobs, and egress proxy logs.
-- ═══════════════════════════════════════════════════════════════════════════════

-- Inference telemetry: every gateway call logged by Kent's callback.
CREATE TABLE IF NOT EXISTS inference_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    request_id TEXT NOT NULL,
    caller_entity TEXT NOT NULL,
    caller_stack TEXT,
    model_logical TEXT NOT NULL,
    model_physical TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    thinking_tokens INTEGER,
    time_to_first_token_ms INTEGER,
    total_latency_ms INTEGER,
    tokens_per_second REAL,
    cache_hit INTEGER,
    status TEXT,
    fallback_used TEXT
);

-- Frontier (cloud) usage log.
CREATE TABLE IF NOT EXISTS frontier_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    escalation_reason TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    thinking_tokens INTEGER,
    cost_usd REAL,
    resolved INTEGER,
    timestamp TEXT NOT NULL
);

-- Nightly QA audit results.
CREATE TABLE IF NOT EXISTS qa_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    original_tier TEXT,
    finding TEXT,
    severity TEXT,
    gitea_issue_id TEXT,
    timestamp TEXT NOT NULL
);

-- Egress proxy telemetry.
CREATE TABLE IF NOT EXISTS egress_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    stack_id TEXT NOT NULL,
    entity TEXT NOT NULL,
    method TEXT NOT NULL,
    domain TEXT NOT NULL,
    path TEXT,
    payload_size_bytes INTEGER,
    flagged INTEGER DEFAULT 0,
    flag_reason TEXT,
    blocked INTEGER DEFAULT 0,
    response_code INTEGER
);

-- Per-stack activity summary (Kent writes periodically).
CREATE TABLE IF NOT EXISTS stack_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stack_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    event TEXT NOT NULL,
    tasks_completed INTEGER,
    tasks_failed INTEGER,
    total_tokens_used INTEGER,
    frontier_escalations INTEGER,
    learnings_shared INTEGER,
    learnings_adopted INTEGER,
    uptime_hours REAL
);

-- ═══════════════════════════════════════════════════════════════════════════════
-- INDEXES
-- ═══════════════════════════════════════════════════════════════════════════════

-- Agent state
CREATE INDEX IF NOT EXISTS idx_routing_tier ON routing_log(routed_tier, timestamp);
CREATE INDEX IF NOT EXISTS idx_routing_outcome ON routing_log(outcome, timestamp);
CREATE INDEX IF NOT EXISTS idx_stack_status ON stack_registry(status);
CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);
CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id, timestamp);

-- Telemetry
CREATE INDEX IF NOT EXISTS idx_telem_entity ON inference_telemetry(caller_entity, timestamp);
CREATE INDEX IF NOT EXISTS idx_telem_model ON inference_telemetry(model_logical, timestamp);
CREATE INDEX IF NOT EXISTS idx_frontier_provider ON frontier_log(provider, timestamp);
CREATE INDEX IF NOT EXISTS idx_egress_flagged ON egress_telemetry(flagged, timestamp);
CREATE INDEX IF NOT EXISTS idx_stack_telem ON stack_telemetry(stack_id, timestamp);
