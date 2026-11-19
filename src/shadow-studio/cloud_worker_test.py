"""Offline safety and idempotency acceptance for Phase 1."""
import json
import random
import tempfile
from pathlib import Path
from unittest.mock import patch

from cloud_worker import open_db, plan_day


def main():
    with tempfile.TemporaryDirectory() as directory:
        with open_db(Path(directory) / "jobs.sqlite3") as db:
            with patch("story_engine._local_json", side_effect=AssertionError("External AI must not be called")):
                first = plan_day(db, "2026-11-19", random.Random(1))
                again = plan_day(db, "2026-11-19", random.Random(2))
                assert first["reused"] is False and again["reused"] is True
                assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
                assert first["voice_a"] != first["voice_b"]
                story = json.loads(first["story"])
                assert story["source"] == "offline_expansion"
                assert any(first["idea"] == line["text"] for line in story["lines"])
                jobs = [first]
                for day in range(20, 27):
                    jobs.append(plan_day(db, f"2026-11-{day:02d}", random.Random(day)))
                pairs = [frozenset((job["voice_a"], job["voice_b"])) for job in jobs]
                assert len(pairs) == len(set(pairs)), "Repeated a voice pair while alternatives remain"
                assert all(job["state"] == "planned" for job in jobs)
            db.close()
        with open_db(Path(directory) / "jobs.sqlite3") as db:
            assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 8
            db.close()
    print("PASS offline story creation, SQLite persistence, daily idempotency, voice rotation, no paid providers")


if __name__ == "__main__":
    main()
