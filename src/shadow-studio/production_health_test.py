"""Offline monitoring regression tests; no YouTube or production files."""
import sqlite3
from contextlib import closing
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
import worker_queue as queue
import unittest
from production_health import inspect


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "production.sqlite3"
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("CREATE TABLE jobs(day TEXT,state TEXT,attempts INTEGER,message TEXT)")
            db.execute("CREATE TABLE r3born_uploads(day TEXT,slot INTEGER,state TEXT)")
            db.execute("CREATE TABLE control(id INTEGER,stopped INTEGER)")
            db.execute("INSERT INTO control VALUES(1,0)")

    def test_healthy(self):
        self.assertEqual(inspect(self.root)["status"],"ok")

    def test_failed_render_and_exhausted_attempts(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("INSERT INTO jobs VALUES('2026-11-20','failed',2,'private-secret')")
        result = inspect(self.root)
        self.assertEqual(result["status"],"error")
        self.assertEqual({i["code"] for i in result["issues"]},
                         {"render_failed","render_attempts_exhausted"})
        self.assertNotIn("private-secret",str(result))

    def test_upload_ambiguous_never_retried(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("INSERT INTO r3born_uploads VALUES('2026-11-20',1,'needs_reconciliation')")
        report = inspect(self.root)
        self.assertIn("upload_reconciliation",[x["code"] for x in report["issues"]])
        with closing(sqlite3.connect(self.db)) as db, db:
            self.assertEqual(db.execute("SELECT state FROM r3born_uploads").fetchone()[0],
                             "needs_reconciliation")

    def test_paused_and_running_are_not_misreported_healthy(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("INSERT INTO jobs VALUES('2026-11-20','running',1,'')")
            db.execute("UPDATE control SET stopped=1")
        self.assertEqual(inspect(self.root)["status"],"warning")

    def test_missing_ledger_is_unknown(self):
        self.db.unlink()
        self.assertEqual(inspect(self.root)["status"],"unknown")


    def test_queue_heartbeat_stale_and_recovery_required(self):
        moment = datetime(2035, 1, 1, tzinfo=timezone.utc)
        queue.enqueue(self.root,"health_check","health:2035-01-01",{},now=moment)
        job=queue.claim(self.root,now=moment)
        self.assertEqual(inspect(self.root,now=moment+timedelta(seconds=30))["status"],"ok")
        stale=inspect(self.root,now=moment+timedelta(minutes=5))
        self.assertIn("queue_heartbeat_stale",[x["code"] for x in stale["issues"]])
        queue.quarantine_stale(self.root,now=moment+timedelta(minutes=5),stale_seconds=180)
        blocked=inspect(self.root,now=moment+timedelta(minutes=5))
        self.assertIn("queue_review_required",[x["code"] for x in blocked["issues"]])

    def test_fresh_supervised_story_does_not_warn_unverified(self):
        moment=datetime(2035,1,1,tzinfo=timezone.utc)
        queue.enqueue(self.root,"story_day","story:2035-01-01",
                      {"day":"2035-01-01","ideas_file":"ideas.json",
                       "media_dir":"licensed","timezone":"UTC"},now=moment)
        queue.claim(self.root,now=moment)
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("INSERT INTO jobs VALUES('2035-01-01','running',1,'')")
        self.assertEqual(inspect(self.root,now=moment+timedelta(seconds=25))["status"],"ok")



if __name__ == "__main__":
    unittest.main()
