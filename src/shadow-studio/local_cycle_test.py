"""Local authorized production cycle tests. No network, uploads or real rendering."""
from datetime import datetime, timezone, date
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from local_cycle import config, validate_rights, cycle, record_alerts
from worker_queue import queue_status

NOW=datetime(2035,1,1,tzinfo=timezone.utc)


class LocalCycleTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.media=self.root/"licensed"
        self.media.mkdir()
        self.video=self.media/"approved.mp4"
        self.video.write_bytes(b"synthetic fixture, not a real media file")
        self.ideas=self.root/"ideas.json"
        self.ideas.write_text(json.dumps(["A new fictional workplace story with an original twist"]),encoding="utf-8")
        self.manifest=self.root/"rights.json"
        self.rights=[{"file":"approved.mp4","sha256":hashlib.sha256(self.video.read_bytes()).hexdigest(),
                      "permission_reference":"Owner-approved source use reference",
                      "license_scope":"commercial-edit-and-repost","expires_at":"2040-01-01"}]
        self._rights()
        self.info={"enabled":True,"state_dir":str(self.root/"state"),
                   "ideas_file":str(self.ideas),"media_dir":str(self.media),
                   "rights_manifest":str(self.manifest),"timezone":"UTC"}

    def _rights(self):
        self.manifest.write_text(json.dumps(self.rights),encoding="utf-8")

    def test_approved_source_and_cycle_are_idempotent(self):
        self.assertEqual(validate_rights(self.info,today=NOW.date()),1)
        calls=[]
        def approved_story(root, job):
            calls.append(job)
            self.assertEqual(job["kind"],"story_day")
            return "ready"
        one=cycle(self.info,now=NOW,processor=approved_story)
        self.assertTrue(one["queued"])
        self.assertEqual(one["status"],"completed")
        self.assertEqual(one["health"],"ok")
        two=cycle(self.info,now=NOW,processor=approved_story)
        self.assertFalse(two["queued"])
        self.assertEqual(two["status"],"idle")
        self.assertEqual(len(calls),1)
        self.assertEqual(queue_status(self.info["state_dir"])[0]["state"],"completed")
        self.assertTrue((Path(self.info["state_dir"])/"health-latest.json").is_file())

    def test_unlisted_and_mutated_footage_fail_closed(self):
        (self.media/"unapproved.mp4").write_bytes(b"unknown")
        with self.assertRaisesRegex(ValueError,"unapproved"):
            validate_rights(self.info,today=NOW.date())
        (self.media/"unapproved.mp4").unlink()
        self.video.write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError,"SHA-256"):
            validate_rights(self.info,today=NOW.date())

    def test_expired_or_unproven_rights_fail_closed(self):
        self.rights[0]["expires_at"]="2020-01-01"
        self._rights()
        with self.assertRaisesRegex(ValueError,"expired"):
            validate_rights(self.info,today=NOW.date())
        self.rights[0].pop("expires_at")
        self._rights()
        with self.assertRaisesRegex(ValueError,"perpetual"):
            validate_rights(self.info,today=NOW.date())

    def test_disabled_cycle_does_not_create_queue(self):
        self.info["enabled"]=False
        self.assertEqual(cycle(self.info,now=NOW)["status"],"disabled")
        self.assertFalse((self.root/"state").exists())

    def test_alert_dedup_and_rearm(self):
        issue={"code":"render_failed","severity":"error","job":"2035-01-01",
               "message":"Local render failed"}
        report={"status":"error","issues":[issue],"counts":{}}
        self.assertEqual(len(record_alerts(self.root/"alerts",report,now=NOW)),1)
        self.assertEqual(len(record_alerts(self.root/"alerts",report,now=NOW)),0)
        record_alerts(self.root/"alerts",{"status":"ok","issues":[],"counts":{}},now=NOW)
        self.assertEqual(len(record_alerts(self.root/"alerts",report,now=NOW)),1)

    def test_config_disallows_secrets(self):
        self.info["oauth_token"]="do-not-store"
        path=self.root/"worker_config.json"
        path.write_text(json.dumps(self.info),encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"credentials"):
            config(path)


if __name__=="__main__":
    unittest.main()
