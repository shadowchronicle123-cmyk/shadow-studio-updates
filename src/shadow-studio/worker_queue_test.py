"""Regression tests for persistent local queue; never run FFmpeg or upload."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

import worker_queue as q


T0 = datetime(2035,1,1,tzinfo=timezone.utc)


class WorkerQueueTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)

    def test_persistent_queue_and_dedup(self):
        item=q.enqueue(self.root,"health_check","health:2035-01-01",{},now=T0)
        self.assertTrue(item[1])
        self.assertEqual(q.enqueue(self.root,"health_check","health:2035-01-01",{},now=T0),
                         (item[0],False))
        self.assertEqual(q.queue_status(self.root)[0]["state"],"pending")

    def test_concurrent_single_claim_and_heartbeat_lease(self):
        q.enqueue(self.root,"health_check","health:2035-01-01",{},now=T0)
        with ThreadPoolExecutor(max_workers=5) as pool:
            results=list(pool.map(lambda _:q.claim(self.root,now=T0),range(5)))
        jobs=[x for x in results if x is not None]
        self.assertEqual(len(jobs),1)
        job=jobs[0]
        self.assertFalse(q.heartbeat(self.root,job["id"],"wrong",now=T0))
        self.assertTrue(q.heartbeat(self.root,job["id"],job["lease"],now=T0))
        self.assertFalse(q.finish(self.root,job["id"],"wrong",now=T0))
        self.assertTrue(q.finish(self.root,job["id"],job["lease"],now=T0))
        self.assertIsNone(q.claim(self.root,now=T0))

    def test_health_retry_exponential_and_bounded(self):
        q.enqueue(self.root,"health_check","health:2035-01-01",{},now=T0,max_attempts=3)
        expected=[30,60,0]
        point=T0
        for i,delay in enumerate(expected):
            job=q.claim(self.root,now=point)
            self.assertEqual(job["attempt"],i+1)
            result=q.fail(self.root,job["id"],job["lease"],transient=True,now=point)
            self.assertEqual(result["retry_after_seconds"],delay)
            self.assertEqual(result["state"],"retry_wait" if delay else "review_required")
            if delay:
                self.assertIsNone(q.claim(self.root,now=point+timedelta(seconds=delay-1)))
                point+=timedelta(seconds=delay)
        self.assertIsNone(q.claim(self.root,now=point+timedelta(days=1)))

    def test_story_never_blindly_retried(self):
        payload={"day":"2035-01-01","ideas_file":"ideas.json",
                 "media_dir":"licensed","timezone":"UTC"}
        q.enqueue(self.root,"story_day","story:2035-01-01",payload,now=T0)
        job=q.claim(self.root,now=T0)
        result=q.fail(self.root,job["id"],job["lease"],transient=True,now=T0)
        self.assertEqual(result["state"],"review_required")
        self.assertIsNone(q.claim(self.root,now=T0+timedelta(days=1)))

    def test_stale_quarantine_does_not_replay(self):
        q.enqueue(self.root,"health_check","health:2035-01-01",{},now=T0)
        job=q.claim(self.root,now=T0)
        self.assertEqual(q.quarantine_stale(self.root,now=T0+timedelta(seconds=40),
                         stale_seconds=60),[])
        self.assertEqual(q.quarantine_stale(self.root,now=T0+timedelta(seconds=61),
                         stale_seconds=60),[job["id"]])
        self.assertIsNone(q.claim(self.root,now=T0+timedelta(hours=1)))
        self.assertFalse(q.heartbeat(self.root,job["id"],job["lease"],now=T0))

    def test_processing_done_and_exception_is_sanitized(self):
        q.enqueue(self.root,"health_check","health:2035-01-01",{},
                  now=datetime.now(timezone.utc)-timedelta(minutes=1))
        self.assertEqual(q.run_once(self.root,processor=lambda root,job:"checked")["status"],
                         "completed")
        q.enqueue(self.root,"health_check","health:2035-01-02",{})
        def failure(root,job):
            raise RuntimeError("secret-token-should-not-be-persisted")
        outcome=q.run_once(self.root,processor=failure)
        self.assertEqual(outcome["status"],"retry_wait")
        self.assertNotIn("secret-token",str(q.queue_status(self.root)))

    def test_stopped_control_blocks_claim(self):
        q.enqueue(self.root,"health_check","health:2035-01-01",{},now=T0)
        with q.database(self.root) as db:
            db.execute("CREATE TABLE control (id INTEGER PRIMARY KEY, stopped INTEGER)")
            db.execute("INSERT INTO control VALUES(1,1)")
        self.assertIsNone(q.claim(self.root,now=T0))

    def test_invalid_payload_and_retry_budget_rejected(self):
        with self.assertRaisesRegex(ValueError,"Invalid"):
            q.enqueue(self.root,"health_check","check",{},max_attempts=500)
        with self.assertRaisesRegex(ValueError,"Story"):
            q.enqueue(self.root,"story_day","story:2035-01-01",{})


if __name__=="__main__":
    unittest.main()
