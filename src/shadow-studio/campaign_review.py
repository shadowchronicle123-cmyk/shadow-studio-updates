"""Offline review and scheduling for the existing R3born private clip worker.

Local-only control plane: no YouTube or paid API calls, no automatic publication.
Uses the same production.sqlite3 and immutable approved MP4 SHA-256.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3

from r3born_campaign import campaign_db, sha256, validate_media

STATES = ("reviewed", "approved", "scheduled", "rejected", "submitting",
          "retryable", "needs_reconciliation", "private")


def _utc(value):
    if not isinstance(value, str):
        raise ValueError("Schedule must be an ISO-8601 timestamp with timezone")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Schedule must be an ISO-8601 timestamp with timezone") from None
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("A timezone offset is required")
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def initialize(db):
    # Additive migration: never replace or reset the pre-existing upload ledger.
    fields = {r["name"] for r in db.execute("PRAGMA table_info(r3born_uploads)")}
    if "scheduled_at" not in fields:
        db.execute("ALTER TABLE r3born_uploads ADD COLUMN scheduled_at TEXT")
    if "review_note" not in fields:
        db.execute("ALTER TABLE r3born_uploads ADD COLUMN review_note TEXT")
    db.execute("""CREATE TABLE IF NOT EXISTS r3born_review_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT, upload_id INTEGER NOT NULL,
        action TEXT NOT NULL, happened_at TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '')""")
    db.commit()


def _get(db, day, slot):
    row = db.execute("SELECT * FROM r3born_uploads WHERE day=? AND slot=?",
                     (day, int(slot))).fetchone()
    if row is None:
        raise ValueError("Campaign slot not found")
    return row


def review(root, day, slot, action, *, at=None, note=""):
    """Explicit transitions. Approved media never changes behind a review."""
    if action not in ("approve", "schedule", "reject", "unschedule"):
        raise ValueError("Choose approve, schedule, reject or unschedule")
    if not isinstance(note, str) or len(note) > 1000:
        raise ValueError("Review note must be at most 1000 characters")
    with campaign_db(root) as db:
        initialize(db)
        db.execute("BEGIN IMMEDIATE")
        row = _get(db, day, slot)
        if row["state"] in ("private", "submitting", "retryable", "needs_reconciliation"):
            raise ValueError("Uploading or uploaded records require manual reconciliation")
        if action == "approve":
            if row["state"] != "reviewed":
                raise ValueError("Only reviewed clips can be approved")
            path = Path(row["path"])
            if not path.is_file() or sha256(path) != row["sha256"]:
                raise ValueError("Approved MP4 checksum mismatch")
            validate_media(path)
            state, scheduled = "approved", None
        elif action == "reject":
            if row["state"] not in ("reviewed", "approved", "scheduled"):
                raise ValueError("Cannot reject this campaign state")
            state, scheduled = "rejected", None
        elif action == "schedule":
            if row["state"] not in ("approved", "scheduled"):
                raise ValueError("Approve this clip before scheduling")
            scheduled = _utc(at)
            if datetime.fromisoformat(scheduled) <= datetime.now(timezone.utc):
                raise ValueError("Schedule must be in the future")
            state = "scheduled"
        else:
            if row["state"] != "scheduled":
                raise ValueError("Only scheduled clips can be unscheduled")
            state, scheduled = "approved", None
        db.execute("""UPDATE r3born_uploads SET state=?, scheduled_at=?, review_note=?
                      WHERE id=?""", (state, scheduled, note, row["id"]))
        db.execute("""INSERT INTO r3born_review_events(upload_id,action,happened_at,detail)
                      VALUES(?,?,?,?)""",
                   (row["id"], action, datetime.now(timezone.utc).isoformat(), note))
        return {"day": day, "slot": int(slot), "state": state, "scheduled_at": scheduled}


def ready(root, now=None):
    """Read-only inspection; never claims upload slots or transfers media."""
    moment = _utc(now or datetime.now(timezone.utc).isoformat())
    with campaign_db(root) as db:
        initialize(db)
        rows = db.execute("""SELECT day,slot,path,sha256,title,description,permission_reference,
                               state,scheduled_at,video_id
                            FROM r3born_uploads ORDER BY day,slot""").fetchall()
        return [{**dict(row), "due": row["state"] == "scheduled" and
                 row["scheduled_at"] is not None and row["scheduled_at"] <= moment}
                for row in rows]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state", required=True)
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("status")
    for action in ("approve", "schedule", "reject", "unschedule"):
        item = sub.add_parser(action)
        item.add_argument("--day", required=True)
        item.add_argument("--slot", type=int, choices=(1, 2, 3), required=True)
        item.add_argument("--note", default="")
        if action == "schedule":
            item.add_argument("--at", required=True, help="ISO-8601 with timezone, e.g. 2026-11-22T14:00:00-05:00")
    args = p.parse_args()
    if args.action == "status":
        # Do not display absolute local source paths in operator-facing output.
        rows = ready(args.state)
        print(json.dumps([{k: r[k] for k in ("day","slot","state","title",
                    "scheduled_at","due","video_id")} for r in rows], indent=2))
    else:
        print(json.dumps(review(args.state, args.day, args.slot,
              args.action, at=getattr(args,"at",None),note=args.note), indent=2))


if __name__ == "__main__":
    main()
