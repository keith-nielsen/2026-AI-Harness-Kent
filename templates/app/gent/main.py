"""
Kent — Gent CEO Main Entry Point

The CEO agent for a fractal stack. Runs inside a Docker container with:
- Gateway access via GATEWAY_URL (fast + smart tiers only)
- API key at /run/secrets/gent_key
- Project database at /data/stack.db
- Egress via HTTP_PROXY (Squid, GET allowed, POST restricted)

Lifecycle:
1. Boot: load secrets, connect to DB, verify gateway
2. Main loop: poll kanban for backlog tasks
3. Per task: route via T0, build crew with tier-appropriate LLMs, execute
4. Post-task: validate output, update kanban, publish learnings
5. On failure: retry once, then escalate to Kent via shared_learnings
"""

import json
import os
import sqlite3
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from gent.crew import build_crew
from gent.router import RouteDecision, route
from gent.tools import get_default_tools

# ─── Configuration ────────────────────────────────────────────────────────────

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://172.30.0.1:4000")
STACK_ID = os.environ.get("STACK_ID", "unknown")
DB_PATH = "/data/stack.db"
KEY_PATH = "/run/secrets/gent_key"
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "10"))  # seconds
MAX_RETRIES = 1

LOG_PREFIX = f"[gent-{STACK_ID}]"


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{ts} {LOG_PREFIX} {msg}", flush=True)


# ─── Secrets ──────────────────────────────────────────────────────────────────

def load_api_key() -> str:
    if os.path.exists(KEY_PATH):
        key = Path(KEY_PATH).read_text().strip()
        if key:
            return key
    log(f"FATAL: No API key at {KEY_PATH}")
    sys.exit(1)


# ─── Database ─────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def claim_next_task(db: sqlite3.Connection) -> dict | None:
    """Claim the highest-priority backlog task. Returns None if empty."""
    row = db.execute(
        """UPDATE kanban
           SET status = 'in_progress',
               started_at = datetime('now'),
               heartbeat_at = datetime('now')
           WHERE task_id = (
               SELECT task_id FROM kanban
               WHERE status = 'backlog'
               ORDER BY priority DESC, created_at ASC
               LIMIT 1
           )
           RETURNING *;"""
    ).fetchone()
    db.commit()
    return dict(row) if row else None


def complete_task(db: sqlite3.Connection, task_id: str, output: str):
    db.execute(
        """UPDATE kanban
           SET status = 'done', completed_at = datetime('now'), output_summary = ?
           WHERE task_id = ?;""",
        (output[:2000], task_id),
    )
    db.commit()


def fail_task(db: sqlite3.Connection, task_id: str, error: str):
    db.execute(
        """UPDATE kanban
           SET status = 'failed', completed_at = datetime('now'),
               error_text = ?, retry_count = retry_count + 1
           WHERE task_id = ?;""",
        (error[:2000], task_id),
    )
    db.commit()


def heartbeat(db: sqlite3.Connection, task_id: str):
    db.execute(
        "UPDATE kanban SET heartbeat_at = datetime('now') WHERE task_id = ?;",
        (task_id,),
    )
    db.commit()


def publish_learning(
    db: sqlite3.Connection, category: str, summary: str, detail: str = "",
    confidence: float = 0.5, applied_locally: bool = False,
):
    db.execute(
        """INSERT INTO shared_learnings
           (timestamp, category, summary, detail, confidence, applied_locally)
           VALUES (datetime('now'), ?, ?, ?, ?, ?);""",
        (category, summary, detail, confidence, int(applied_locally)),
    )
    db.commit()


def request_escalation(db: sqlite3.Connection, task_id: str, reason: str):
    """Escalate to Kent by publishing an escalation request."""
    publish_learning(
        db,
        category="escalation_request",
        summary=f"ESCALATION for task {task_id}: {reason}",
        detail=reason,
        confidence=0.0,
    )
    log(f"Escalation published for task {task_id}")


# ─── Output Validation ───────────────────────────────────────────────────────

def validate_output(
    api_key: str, task_description: str, output: str,
) -> tuple[bool, str, float]:
    """Ask the smart tier to review output quality.

    Returns: (passed, critique, confidence)
    """
    import requests as req

    validation_prompt = f"""Review this task output for quality.

TASK: {task_description[:1000]}

OUTPUT: {output[:3000]}

Respond with JSON only:
{{"passed": true|false, "critique": "...", "confidence": 0.0-1.0}}"""

    try:
        resp = req.post(
            f"{GATEWAY_URL}/v1/chat/completions",
            json={
                "model": "smart",
                "messages": [{"role": "user", "content": validation_prompt}],
                "max_tokens": 512,
                "response_format": {"type": "json_object"},
            },
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]
        data = json.loads(raw)
        return (
            bool(data.get("passed", False)),
            data.get("critique", ""),
            float(data.get("confidence", 0.5)),
        )
    except Exception as e:
        log(f"Validation error: {e}")
        return False, f"Validation call failed: {e}", 0.0


# ─── Task Execution ──────────────────────────────────────────────────────────

def execute_task(task: dict, api_key: str, db: sqlite3.Connection) -> bool:
    """Route, build crew, execute, validate. Returns True if successful."""
    task_id = task["task_id"]
    description = task["description"] or task["title"]

    # Step 1: Route
    log(f"Routing task {task_id}...")
    try:
        decision = route(description, api_key)
    except Exception as e:
        log(f"Router failed: {e}. Defaulting to smart.")
        decision = RouteDecision(
            tier="smart", review_required=True, confidence=0.0,
            original_tier="smart",
        )

    log(f"  Tier={decision.tier} confidence={decision.confidence:.2f} "
        f"review={decision.review_required} original={decision.original_tier}")

    # Update kanban with routing decision
    db.execute(
        "UPDATE kanban SET routed_tier = ? WHERE task_id = ?;",
        (decision.tier, task_id),
    )
    db.commit()

    # Step 2: Build and run crew
    log(f"Building crew on tier={decision.tier}...")
    try:
        crew = build_crew(
            tier=decision.tier,
            api_key=api_key,
            task_descriptions=[{
                "description": description,
                "expected_output": "A complete, accurate response.",
                "agent_index": 0,
            }],
            process="sequential",
            tools=get_default_tools(),
        )

        heartbeat(db, task_id)
        result = crew.kickoff()
        output = result.raw if hasattr(result, "raw") else str(result)

    except Exception as e:
        log(f"Crew execution failed: {e}")
        return False

    # Step 3: Validate (if required or 10% random sample)
    import random
    needs_review = decision.review_required or random.random() < 0.10

    if needs_review:
        log(f"Validating output for task {task_id}...")
        passed, critique, conf = validate_output(api_key, description, output)

        if not passed or conf < 0.6:
            log(f"Validation failed: {critique} (confidence={conf:.2f})")

            # If router originally said frontier, escalate to Kent
            if decision.original_tier == "frontier":
                request_escalation(db, task_id, critique)
                fail_task(db, task_id, f"Escalated to Kent: {critique}")
                return False

            # Otherwise this is a retry-eligible failure
            return False

    # Step 4: Complete
    complete_task(db, task_id, output)
    log(f"Task {task_id} completed.")
    return True


# ─── Self-Reflection (Learning Publication) ───────────────────────────────────

def reflect_on_completion(task: dict, db: sqlite3.Connection):
    """After completing a task, check if anything is worth sharing."""
    # Placeholder: in production, the CEO asks the smart model:
    # "Was anything in this task execution transferable to other projects?"
    # If yes, publishes to shared_learnings for Kent to evaluate.
    pass


# ─── Main Loop ────────────────────────────────────────────────────────────────

def main():
    log("CEO starting.")
    api_key = load_api_key()
    log(f"Gateway: {GATEWAY_URL}")
    log(f"Database: {DB_PATH}")

    # Verify gateway connectivity
    import requests as req
    try:
        resp = req.get(f"{GATEWAY_URL}/health", timeout=10)
        resp.raise_for_status()
        log("Gateway health check passed.")
    except Exception as e:
        log(f"WARNING: Gateway health check failed: {e}")

    db = get_db()

    # Check for seed context
    seed_count = db.execute("SELECT COUNT(*) FROM seed_context;").fetchone()[0]
    if seed_count > 0:
        log(f"Seed context loaded: {seed_count} entries from archived stack.")

    log("Entering main loop.")
    while True:
        try:
            task = claim_next_task(db)
            if task is None:
                time.sleep(POLL_INTERVAL)
                continue

            task_id = task["task_id"]
            log(f"Claimed task: {task_id} — {task['title']}")

            success = execute_task(task, api_key, db)

            if not success and task.get("retry_count", 0) < MAX_RETRIES:
                # Reset to backlog for one retry
                log(f"Task {task_id} failed — queueing retry.")
                db.execute(
                    "UPDATE kanban SET status = 'backlog' WHERE task_id = ?;",
                    (task_id,),
                )
                db.commit()
            elif not success:
                log(f"Task {task_id} failed after retries.")
                fail_task(db, task_id, "Max retries exceeded.")
                request_escalation(
                    db, task_id,
                    f"Task failed after {MAX_RETRIES} retries: {task['title']}",
                )
            else:
                reflect_on_completion(task, db)

        except KeyboardInterrupt:
            log("Shutdown requested.")
            break
        except Exception as e:
            log(f"Unhandled error: {e}")
            traceback.print_exc()
            time.sleep(POLL_INTERVAL)

    db.close()
    log("CEO stopped.")


if __name__ == "__main__":
    main()
