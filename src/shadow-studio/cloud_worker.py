"""Phase 1: safe, free-first production planner (no upload or paid API calls).

Run: python cloud_worker.py --once --database /path/to/jobs.sqlite3
The planner persists a unique daily job, original story and rotated voice pair.
Rendering and publishing are deliberately NOT enabled in Phase 1.
"""
import argparse
from contextlib import closing
import datetime as dt
import json
import os
from pathlib import Path
import random
import sqlite3

from story_engine import make_story
from voice_engine import VOICES

DEFAULT_IDEAS = (
    "A roommate received a mysterious package addressed to someone who moved out years ago.",
    "A wedding guest found an invitation with a date nobody recognized.",
    "A friend accidentally sent a secret birthday plan to the guest of honor.",
    "A coworker discovered the office group chat had a second hidden member.",
    "A neighbor left a handwritten note that changed the meaning of an old photograph.",
    "Someone discovered the receipt for a gift they never received.",
    "A family dinner took an unexpected turn when an old voicemail resurfaced.",
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    day TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK(state IN ('planned','failed','rendered','approved','uploaded')),
    idea TEXT NOT NULL,
    story_json TEXT NOT NULL,
    voice_a TEXT NOT NULL,
    voice_b TEXT NOT NULL,
    created_at TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT ''
);
"""


def open_db(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute(SCHEMA)
    connection.commit()
    return connection


def select_voices(connection, rng, lookback=10):
    """Select two distinct free Edge voices, avoiding recent pairs when possible."""
    voices = [v["id"] for v in VOICES if v["provider"] == "online"]
    if len(voices) < 2:
        raise RuntimeError("At least two non-premium voices are required")
    history = connection.execute(
        "SELECT voice_a, voice_b FROM jobs ORDER BY day DESC LIMIT ?", (lookback,)
    ).fetchall()
    recent_pairs = {tuple(sorted(row)) for row in history}
    recent_voices = {voice for row in history for voice in row}
    pairs = [(a, b) for a in voices for b in voices if a != b and tuple(sorted((a, b))) not in recent_pairs]
    if not pairs:
        pairs = [(a, b) for a in voices for b in voices if a != b]
    min_reuse = min(int(a in recent_voices) + int(b in recent_voices) for a, b in pairs)
    candidates = [(a, b) for a, b in pairs if int(a in recent_voices) + int(b in recent_voices) == min_reuse]
    return rng.choice(candidates)


def plan_day(connection, day, rng=None, idea=None):
    """Idempotent: a repeat scheduler invocation never creates another job."""
    rng = rng or random.Random()
    existing = connection.execute(
        "SELECT day,state,idea,story_json,voice_a,voice_b FROM jobs WHERE day=?", (day,)
    ).fetchone()
    if existing:
        return dict(zip(("day", "state", "idea", "story", "voice_a", "voice_b"), existing), reused=True)
    previous = connection.execute("SELECT idea FROM jobs ORDER BY day DESC LIMIT 7").fetchall()
    used = {row[0] for row in previous}
    candidates = [value for value in DEFAULT_IDEAS if value not in used]
    chosen = idea or rng.choice(candidates or DEFAULT_IDEAS)
    voices = select_voices(connection, rng)
    story = make_story(chosen, target=45, genre="drama", provider="offline")
    created = dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        connection.execute(
            "INSERT INTO jobs(day,state,idea,story_json,voice_a,voice_b,created_at) VALUES(?,?,?,?,?,?,?)",
            (day, "planned", chosen, json.dumps(story), *voices, created),
        )
        connection.commit()
    except sqlite3.IntegrityError:
        return plan_day(connection, day, rng, idea)
    return {"day": day, "state": "planned", "idea": chosen, "story": json.dumps(story),
            "voice_a": voices[0], "voice_b": voices[1], "reused": False}


def main():
    parser = argparse.ArgumentParser(description="ShadowChronicles safe cloud production planner")
    parser.add_argument("--once", action="store_true", required=True)
    parser.add_argument("--database", default=os.getenv("SHADOW_WORKER_DB", "worker-data/jobs.sqlite3"))
    parser.add_argument("--day", default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    parser.add_argument("--idea", default=None)
    args = parser.parse_args()
    dt.date.fromisoformat(args.day)
    with closing(open_db(args.database)) as connection:
        result = plan_day(connection, args.day, idea=args.idea)
    print(json.dumps({**result, "story": json.loads(result["story"])}))
    print("PHASE 1 ONLY: No rendering, YouTube uploads, external AI or paid services performed.")


if __name__ == "__main__":
    main()
