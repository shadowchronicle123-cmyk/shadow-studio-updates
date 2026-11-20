"""Local authorized production cycle tests. No network, uploads or real rendering."""
from datetime import datetime, timezone, date, timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from local_cycle import config, validate_rights, cycle, record_alerts, enqueue_licensed_highlights
from streamer_clips import queue_items
from worker_queue import claim, enqueue, finish, queue_status

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

    def test_restart_quarantines_render_and_retries_read_only_work(self):
        root = self.info["state_dir"]
        story = {"day": NOW.date().isoformat(), "ideas_file": self.info["ideas_file"],
                 "media_dir": self.info["media_dir"], "timezone": "UTC"}
        enqueue(root, "story_day", "story:"+story["day"], story, now=NOW)
        abandoned_render = claim(root, now=NOW)
        enqueue(root, "health_check", "restart-health", {}, now=NOW, max_attempts=2)
        abandoned_check = claim(root, now=NOW)
        restart = NOW + timedelta(seconds=181)
        calls = []
        def check(_root, job):
            calls.append(job["kind"])
            return "checked"

        result = cycle(self.info, now=restart, processor=check)
        self.assertEqual(result["recovered_queue_claims"], 2)
        self.assertEqual(result["status"], "idle")
        self.assertEqual([row["state"] for row in queue_status(root)], ["review_required", "retry_wait"])
        self.assertFalse(finish(root, abandoned_render["id"], abandoned_render["lease"]))
        self.assertFalse(finish(root, abandoned_check["id"], abandoned_check["lease"]))
        self.assertEqual(cycle(self.info, now=restart+timedelta(seconds=29), processor=check)["status"], "idle")
        self.assertEqual(cycle(self.info, now=restart+timedelta(seconds=30), processor=check)["status"], "completed")
        self.assertEqual(calls, ["health_check"])
        self.assertEqual(queue_status(root)[1]["attempts"], 2)

    def test_stale_story_payload_never_reaches_renderer(self):
        root = self.info["state_dir"]
        day = NOW.date().isoformat()
        old = {"day": day, "ideas_file": self.info["ideas_file"],
               "media_dir": str(self.root/"old-footage"), "timezone": "UTC"}
        enqueue(root, "story_day", "story:"+day, old, now=NOW)
        calls = []
        outcome = cycle(self.info, now=NOW, processor=lambda *args: calls.append(args))
        self.assertEqual(outcome["status"], "review_required")
        self.assertEqual(queue_status(root)[0]["reason"], "approval_changed")
        self.assertFalse(calls)

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


    def test_opt_in_licensed_highlights_process_without_uploads(self):
        self.info["auto_highlights"]=True
        clip_db=self.root/"clips.sqlite3"
        self.info["clip_database"]=str(clip_db)
        item={"creator":"Authorized creator","source_url":"https://www.twitch.tv/example",
              "permission_reference":"Owner license reference",
              "license_scope":"commercial-edit-and-repost",
              "file":"approved.mp4","start_seconds":0,"end_seconds":36}
        queue_items([item],self.media,clip_db)
        calls=[]
        def fake_processor(root,job):
            calls.append(job["kind"])
            return "ready" if job["kind"]=="story_day" else "checked"
        first=cycle(self.info,now=NOW,processor=fake_processor)
        self.assertEqual(first["licensed_highlight_jobs"],1)
        self.assertEqual(first["status"],"completed")
        second=cycle(self.info,now=NOW,processor=fake_processor)
        self.assertEqual(second["licensed_highlight_jobs"],0)
        self.assertEqual(second["status"],"completed")
        third=cycle(self.info,now=NOW,processor=fake_processor)
        self.assertEqual(third["status"],"idle")
        # Preflight queues approved highlights before the daily story; FIFO claims
        # therefore process the highlight first, then the story on the next tick.
        self.assertEqual(calls,["highlight_scan","story_day"])
        self.assertEqual(len(queue_status(self.info["state_dir"])),2)

    def test_unapproved_clip_ledger_entry_is_not_enqueued(self):
        clip_db=self.root/"clips.sqlite3"
        self.info["clip_database"]=str(clip_db)
        self.info["auto_highlights"]=True
        item={"creator":"Authorized creator","source_url":"https://www.twitch.tv/example",
              "permission_reference":"Owner license reference",
              "license_scope":"repost",
              "file":"approved.mp4","start_seconds":0,"end_seconds":36}
        queue_items([item],self.media,clip_db)
        self.assertEqual(enqueue_licensed_highlights(self.info,now=NOW),0)



    def test_preflight_failure_generates_once_only_local_alert(self):
        extra=self.media/"unknown.mp4"
        extra.write_bytes(b"unauthorized")
        first=cycle(self.info,now=NOW,processor=lambda root,job:"ready")
        self.assertEqual(first["status"],"blocked")
        self.assertEqual(first["reason"],"rights_or_ledger_preflight_blocked")
        self.assertEqual(first["new_alerts"],1)
        second=cycle(self.info,now=NOW,processor=lambda root,job:"ready")
        self.assertEqual(second["new_alerts"],0)
        self.assertEqual(queue_status(self.info["state_dir"]),[])
        inbox=Path(self.info["state_dir"])/"alerts.jsonl"
        self.assertEqual(len(inbox.read_text(encoding="utf-8").splitlines()),1)
        extra.unlink()
        cleared=cycle(self.info,now=NOW,processor=lambda root,job:"ready")
        self.assertEqual(cleared["status"],"completed")
        import worker_queue as q
        with q.database(self.info["state_dir"]) as db:
            self.assertEqual(db.execute("SELECT resolved FROM queue_alerts").fetchone()[0],1)

    def test_disk_space_failure_alerts_and_never_queues(self):
        with patch("local_cycle.shutil.disk_usage",return_value=SimpleNamespace(free=0)):
            result=cycle(self.info,now=NOW,processor=lambda root,job:"ready")
        self.assertEqual(result["status"],"blocked")
        self.assertEqual(result["reason"],"disk_space_low")
        self.assertEqual(queue_status(self.info["state_dir"]),[])

    def test_missing_opt_in_clip_ledger_alerts_before_story_enqueue(self):
        self.info["auto_highlights"]=True
        self.info["clip_database"]=str(self.root/"unavailable.sqlite3")
        result=cycle(self.info,now=NOW,processor=lambda root,job:"ready")
        self.assertEqual(result["reason"],"rights_or_ledger_preflight_blocked")
        self.assertEqual(queue_status(self.info["state_dir"]),[])



if __name__=="__main__":
    unittest.main()
