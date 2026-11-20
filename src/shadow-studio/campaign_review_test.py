"""No network or YouTube: persistent manual review and schedule tests."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from r3born_campaign import campaign_db, register
from campaign_review import review, ready


class ReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / "captioned.mp4"
        self.path.write_bytes(b"synthetic fixture only")
        self.digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        validate = patch("r3born_campaign.validate_media", return_value={"duration": 30, "width":1080,"height":1920})
        validate.start()
        self.addCleanup(validate.stop)
        # campaign_review separately imports the function, so mock both references.
        review_validate = patch("campaign_review.validate_media", return_value={"duration":30,"width":1080,"height":1920})
        review_validate.start()
        self.addCleanup(review_validate.stop)
        register(self.root,"2026-11-20",self.path,"R3born authorized gameplay",
                 "Documented permission to edit and repost",self.digest)

    def test_approve_schedule_unschedule_reject_persists(self):
        self.assertEqual(review(self.root,"2026-11-20",1,"approve")["state"],"approved")
        self.assertEqual(review(self.root,"2026-11-20",1,"schedule",at="2035-11-22T14:00:00-05:00")["state"],"scheduled")
        self.assertTrue(ready(self.root,"2035-11-22T20:00:00+00:00")[0]["due"])
        self.assertEqual(review(self.root,"2026-11-20",1,"unschedule")["state"],"approved")
        self.assertEqual(review(self.root,"2026-11-20",1,"reject")["state"],"rejected")
        with campaign_db(self.root) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM r3born_review_events").fetchone()[0],4)

    def test_reject_cannot_be_approved_again(self):
        review(self.root,"2026-11-20",1,"reject")
        with self.assertRaisesRegex(ValueError,"Only reviewed"):
            review(self.root,"2026-11-20",1,"approve")

    def test_schedule_must_follow_approval_and_have_timezone(self):
        with self.assertRaisesRegex(ValueError,"Approve"):
            review(self.root,"2026-11-20",1,"schedule",at="2035-11-22T14:00:00Z")
        review(self.root,"2026-11-20",1,"approve")
        with self.assertRaisesRegex(ValueError,"timezone"):
            review(self.root,"2026-11-20",1,"schedule",at="2035-11-22T14:00:00")

    def test_changed_mp4_blocks_approval(self):
        self.path.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError,"checksum"):
            review(self.root,"2026-11-20",1,"approve")

    def test_previous_upload_states_are_immutable(self):
        with campaign_db(self.root) as db:
            db.execute("UPDATE r3born_uploads SET state='private',video_id='k35fTGK9erE'")
        with self.assertRaisesRegex(ValueError,"manual reconciliation"):
            review(self.root,"2026-11-20",1,"reject")
        self.assertEqual(ready(self.root)[0]["video_id"],"k35fTGK9erE")


if __name__ == "__main__":
    unittest.main()
