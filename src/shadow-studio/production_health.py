"""Read-only Shadow Studio production health checks and bounded alert deduplication.

No network calls, no credentials, no uploads, no public publishing.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3
import sys


def _tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def inspect(state, *, now=None, stale_minutes=3, max_render_attempts=2):
    """Check a production SQLite snapshot without mutating it or retrying uploads."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must include timezone")
    if stale_minutes < 1 or max_render_attempts < 1:
        raise ValueError("Thresholds must be positive")
    dbpath = Path(state) / "production.sqlite3"
    if not dbpath.is_file():
        return {"status":"unknown","issues":[{"code":"ledger_missing","severity":"warning",
                  "message":"Production ledger not found; deployment may not be initialized"}],
                "counts":{}}
    issues, counts = [], {}
    try:
        # SQLite immutable mode is unsafe with WAL; mode=ro honors committed WAL
        # data and never creates a new database.
        with closing(sqlite3.connect(dbpath.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            tables = _tables(db)
            queue_heartbeat_days = set()
            if "local_queue" in tables:
                queued = db.execute("""SELECT id,kind,dedupe_key,state,attempts,max_attempts,
                                              heartbeat_at,not_before FROM local_queue""").fetchall()
                counts["queue_jobs"] = len(queued)
                boundary = now.astimezone(timezone.utc) - timedelta(minutes=stale_minutes)
                for item in queued:
                    if item["state"] == "claimed":
                        heartbeat_value = item["heartbeat_at"]
                        try:
                            beat = datetime.fromisoformat(heartbeat_value) if heartbeat_value else None
                        except (TypeError, ValueError):
                            beat = None
                        if beat is None or beat.tzinfo is None or beat < boundary:
                            issues.append({"code":"queue_heartbeat_stale","severity":"error",
                                "job":item["id"],
                                "message":"Worker lease heartbeat missing or stale; quarantine before any restart"})
                        elif item["kind"] == "story_day" and item["dedupe_key"].startswith("story:"):
                            queue_heartbeat_days.add(item["dedupe_key"][6:])
                    elif item["state"] == "review_required":
                        issues.append({"code":"queue_review_required","severity":"error",
                            "job":item["id"],"message":"Queue job needs operator reconciliation; no replay"})
                    elif item["state"] == "retry_wait":
                        issues.append({"code":"retry_pending","severity":"warning",
                            "job":item["id"],"message":"Bounded health-check retry waiting until due"})
            if "jobs" in tables:
                rows = db.execute("SELECT day,state,attempts,message FROM jobs").fetchall()
                counts["jobs"] = len(rows)
                for row in rows:
                    if row["state"] == "failed":
                        issues.append({"code":"render_failed","severity":"error",
                            "job":row["day"],"message":"Render job failed; operator review required"})
                    elif row["state"] == "running" and row["day"] not in queue_heartbeat_days:
                        issues.append({"code":"running_unverified","severity":"warning",
                            "job":row["day"],"message":"Render job lacks a fresh supervised queue heartbeat"})
                    if row["attempts"] >= max_render_attempts and row["state"] == "failed":
                        issues.append({"code":"render_attempts_exhausted","severity":"error",
                            "job":row["day"],"message":"Render retry budget exhausted; no automatic retry"})
            if "r3born_uploads" in tables:
                rows = db.execute("SELECT day,slot,state FROM r3born_uploads").fetchall()
                counts["campaign_slots"] = len(rows)
                for row in rows:
                    if row["state"] in ("needs_reconciliation","retryable","submitting"):
                        issues.append({"code":"upload_reconciliation","severity":"error",
                            "job":f'{row["day"]}:{row["slot"]}',
                            "message":"Private upload status requires manual verification; never blindly retry"})
            if "private_uploads" in tables:
                for row in db.execute("SELECT day,state FROM private_uploads"):
                    if row["state"] in ("needs_reconciliation","submitting","uncertain"):
                        issues.append({"code":"upload_reconciliation","severity":"error",
                            "job":row["day"],"message":"Private upload requires manual verification"})
            if "control" in tables:
                row = db.execute("SELECT stopped FROM control WHERE id=1").fetchone()
                if row and row["stopped"]:
                    issues.append({"code":"worker_paused","severity":"warning",
                        "message":"Production worker is paused"})
    except (sqlite3.Error, OSError) as exc:
        return {"status":"unknown","issues":[{"code":"ledger_unreadable","severity":"error",
                "message":"Cannot inspect production ledger; check database permissions/integrity"}],
                "counts":{}}
    return {"status":"error" if any(i["severity"]=="error" for i in issues)
            else "warning" if issues else "ok", "issues":issues, "counts":counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="Optional JSON alert report; no secrets included")
    args = parser.parse_args()
    report = inspect(args.state)
    payload = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    # Unknown monitoring state is NOT healthy.
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
