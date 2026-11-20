"""Persistent local-only production queue with leases, heartbeats and bounded recovery.

No network API, remote footage download or YouTube publishing occurs here.
Story rendering is delegated to the existing production_worker.run_day.
"""
import argparse
import hashlib
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import json
import re
from pathlib import Path
import sqlite3
import threading
import time
import uuid

KINDS = {"story_day", "health_check", "highlight_scan"}
MAX_ATTEMPTS = 3
HEARTBEAT_SECONDS = 15


def utc(now=None):
    dt = now if now is not None else datetime.now(timezone.utc)
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("An aware UTC timestamp is required")
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def database(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "production.sqlite3", timeout=15)
    db.row_factory = sqlite3.Row
    try:
        with db:
            db.execute("""CREATE TABLE IF NOT EXISTS local_queue (
              id INTEGER PRIMARY KEY, kind TEXT NOT NULL, dedupe_key TEXT NOT NULL UNIQUE,
              payload TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
              max_attempts INTEGER NOT NULL DEFAULT 3,
              not_before TEXT NOT NULL, claimed_at TEXT, heartbeat_at TEXT,
              lease TEXT, finished_at TEXT, reason TEXT NOT NULL DEFAULT '')""")
            db.execute("""CREATE TABLE IF NOT EXISTS queue_alerts (
              alert_key TEXT PRIMARY KEY, code TEXT NOT NULL, job_id INTEGER,
              first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
              occurrences INTEGER NOT NULL DEFAULT 1, resolved INTEGER NOT NULL DEFAULT 0)""")
            yield db
    finally:
        db.close()


def enqueue(root, kind, key, payload, *, now=None, max_attempts=MAX_ATTEMPTS):
    if kind not in KINDS or not isinstance(key, str) or not key.strip():
        raise ValueError("Unsupported or missing queue kind/key")
    if not isinstance(payload, dict) or not 1 <= max_attempts <= MAX_ATTEMPTS:
        raise ValueError("Invalid queue payload or retry budget")
    if kind == "story_day":
        from datetime import date
        required = ("day", "ideas_file", "media_dir", "timezone")
        if any(not isinstance(payload.get(k), str) or not payload[k] for k in required):
            raise ValueError("Story job requires day, ideas_file, media_dir and timezone")
        date.fromisoformat(payload["day"])
        if key != "story:" + payload["day"]:
            raise ValueError("Story deduplication key must be the production day")
    elif kind == "highlight_scan":
        if (type(payload.get("clip_id")) is not int or payload["clip_id"] <= 0
            or not isinstance(payload.get("clip_database"), str)
            or not payload["clip_database"]
            or not isinstance(payload.get("source_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}",payload["source_sha256"])):
            raise ValueError("Highlight scan requires a registered clip ID, database and SHA-256")
        expected = hashlib.sha256((str(Path(payload["clip_database"]).resolve())
                + "|" + str(payload["clip_id"]) + "|" + payload["source_sha256"]).encode("utf-8")).hexdigest()
        if key != "highlight:" + expected:
            raise ValueError("Highlight deduplication key differs from approved source")
    elif payload:
        raise ValueError("Health-check payload must be empty")
    with database(root) as db:
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT id FROM local_queue WHERE dedupe_key=?", (key,)).fetchone()
        if existing:
            return existing["id"], False
        row = db.execute("""INSERT INTO local_queue(kind,dedupe_key,payload,state,not_before,max_attempts)
                VALUES (?,?,?,'pending',?,?)""",
                (kind,key,json.dumps(payload,sort_keys=True),utc(now),max_attempts))
        return row.lastrowid, True


def claim(root, *, now=None):
    moment = utc(now)
    with database(root) as db:
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        try:
            control = db.execute("SELECT stopped FROM control WHERE id=1").fetchone()
        except sqlite3.OperationalError:
            control = None
        if control and control["stopped"]:
            return None
        row = db.execute("""SELECT * FROM local_queue
            WHERE state IN ('pending','retry_wait') AND not_before <= ?
                AND attempts < max_attempts ORDER BY id LIMIT 1""", (moment,)).fetchone()
        if row is None:
            return None
        lease = uuid.uuid4().hex
        updated = db.execute("""UPDATE local_queue SET state='claimed',attempts=attempts+1,
                claimed_at=?,heartbeat_at=?,lease=?,reason='' WHERE id=? AND
                state IN ('pending','retry_wait')""", (moment,moment,lease,row["id"]))
        if updated.rowcount != 1:
            return None
        return {"id":row["id"], "kind":row["kind"],
                "payload":json.loads(row["payload"]), "attempt":row["attempts"]+1,
                "lease":lease}


def heartbeat(root, job_id, lease, *, now=None):
    with database(root) as db:
        row = db.execute("""UPDATE local_queue SET heartbeat_at=? WHERE id=? AND
              state='claimed' AND lease=?""", (utc(now),job_id,lease))
        return row.rowcount == 1


def finish(root, job_id, lease, *, now=None):
    with database(root) as db:
        row = db.execute("""UPDATE local_queue SET state='completed',finished_at=?,
              lease=NULL,heartbeat_at=NULL WHERE id=? AND state='claimed' AND lease=?""",
              (utc(now),job_id,lease))
        return row.rowcount == 1


def fail(root, job_id, lease, *, transient=False, code="worker_error", now=None):
    """Only read-only health checks can retry. Story jobs require manual inspection."""
    moment = now if now is not None else datetime.now(timezone.utc)
    stamp = utc(moment)
    if not isinstance(code,str) or not code.replace("_","").isalnum() or len(code)>48:
        raise ValueError("Use an unprivileged error code, never exception text")
    with database(root) as db:
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT kind,attempts,max_attempts FROM local_queue WHERE id=? AND state='claimed' AND lease=?",
                         (job_id,lease)).fetchone()
        if row is None:
            return None
        retry = bool(transient) and row["kind"]=="health_check" and row["attempts"] < row["max_attempts"]
        state = "retry_wait" if retry else "review_required"
        delay = min(3600, 30 * 2 ** (row["attempts"]-1)) if retry else 0
        next_at = utc(moment + timedelta(seconds=delay))
        db.execute("""UPDATE local_queue SET state=?, not_before=?,reason=?,
              lease=NULL,heartbeat_at=NULL WHERE id=?""",
              (state,next_at,code,job_id))
        return {"state":state, "retry_after_seconds":delay, "attempts":row["attempts"]}


def quarantine_stale(root, *, now=None, stale_seconds=120):
    """Explicit fail-closed recovery: never replay an uncertain render/upload."""
    if stale_seconds < HEARTBEAT_SECONDS * 2:
        raise ValueError("Staleness threshold must exceed two heartbeats")
    moment = now if now is not None else datetime.now(timezone.utc)
    boundary = utc(moment - timedelta(seconds=stale_seconds))
    with database(root) as db:
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("""SELECT id FROM local_queue WHERE state='claimed'
            AND (heartbeat_at IS NULL OR heartbeat_at < ?) ORDER BY id""",(boundary,)).fetchall()
        for row in rows:
            db.execute("""UPDATE local_queue SET state='review_required',reason='stale_lease',
                lease=NULL WHERE id=? AND state='claimed'""",(row["id"],))
        return [r["id"] for r in rows]


def queue_status(root):
    with database(root) as db:
        return [dict(row) for row in db.execute("""SELECT id,kind,dedupe_key,state,attempts,
            max_attempts,not_before,claimed_at,heartbeat_at,reason FROM local_queue ORDER BY id""")]


def _run_original(root, job):
    if job["kind"] == "health_check":
        from production_health import inspect
        result = inspect(root)
        if result["status"] == "unknown":
            raise OSError("health_unavailable")
        return "checked"
    if job["kind"] == "highlight_scan":
        from highlight_suggestions import generate
        item = job["payload"]
        result = generate(item["clip_database"],item["clip_id"])
        if result["source_sha256"] != item["source_sha256"]:
            raise ValueError("Licensed source SHA-256 differs from queued approval")
        return "checked"
    if job["kind"] != "story_day":
        raise ValueError("Unsupported job")
    from production_worker import run_day
    item = job["payload"]
    ideas = json.loads(Path(item["ideas_file"]).read_text(encoding="utf-8"))
    return run_day(root, ideas, Path(item["media_dir"]),
                   timezone=item["timezone"],day=item["day"])


def run_once(root, *, processor=None, heartbeat_interval=HEARTBEAT_SECONDS, now=None):
    """One supervised job. No automatic public/private YouTube upload."""
    if heartbeat_interval <= 0:
        raise ValueError("Heartbeat interval must be positive")
    job = claim(root, now=now)
    if job is None:
        return {"status":"idle"}
    stop = threading.Event()
    def pulse():
        while not stop.wait(heartbeat_interval):
            if not heartbeat(root,job["id"],job["lease"]):
                break
    worker = threading.Thread(target=pulse,daemon=True)
    worker.start()
    try:
        outcome = (processor or _run_original)(root, job)
        if outcome in ("ready","checked"):
            if not finish(root,job["id"],job["lease"]):
                raise RuntimeError("Queue lease lost: do not replay job")
            return {"status":"completed", "id":job["id"]}
        result = fail(root,job["id"],job["lease"],code="unexpected_result")
        return {"status":result["state"],"id":job["id"]}
    except (Exception,):
        # Never serialize stack traces, credentials or source file paths.
        result = fail(root,job["id"],job["lease"],
                      transient=job["kind"]=="health_check",code="worker_exception")
        return {"status":result["state"] if result else "lease_lost","id":job["id"]}
    finally:
        stop.set()
        worker.join(timeout=2)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state",required=True,type=Path)
    s = p.add_subparsers(dest="command",required=True)
    story=s.add_parser("enqueue-story")
    for key in ("day","ideas-file","media-dir","timezone"):
        story.add_argument("--"+key,required=True)
    s.add_parser("enqueue-health")
    s.add_parser("run-once")
    s.add_parser("status")
    stale=s.add_parser("quarantine-stale")
    stale.add_argument("--seconds",type=int,default=180)
    a=p.parse_args()
    if a.command=="enqueue-story":
        result=enqueue(a.state,"story_day","story:"+a.day,
              {"day":a.day,"ideas_file":a.ideas_file,"media_dir":a.media_dir,
               "timezone":a.timezone})
    elif a.command=="enqueue-health":
        result=enqueue(a.state,"health_check","health:"+utc()[:10],{})
    elif a.command=="run-once":
        result=run_once(a.state)
    elif a.command=="status":
        result=queue_status(a.state)
    else:
        result=quarantine_stale(a.state,stale_seconds=a.seconds)
    print(json.dumps(result,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
