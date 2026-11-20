"""Offline regression tests for campaign slots; never contact YouTube."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from r3born_campaign import register, campaign_db, upload_reviewed, download_campaign, validate_media


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        media_patch = patch("r3born_campaign.validate_media", return_value={"duration":35,"width":1080,"height":1920})
        media_patch.start()
        self.addCleanup(media_patch.stop)
        self.files = []
        for n in range(4):
            path = self.root / f"captioned-{n}.mp4"
            path.write_bytes(b"synthetic-captioned-mp4-" + bytes([n]))
            self.files.append(path)

    def register(self, n, day="2026-11-19", description="#r3born @hardscope"):
        path = self.files[n]
        return register(self.root, day, path, "N3on R3born Ep 2",
                        "Documented campaign editing and YouTube repost permission",
                        hashlib.sha256(path.read_bytes()).hexdigest(), description=description)

    def test_three_persisted_slots_and_next_day(self):
        self.assertEqual([self.register(n) for n in range(3)], [1,2,3])
        with campaign_db(self.root) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM r3born_uploads").fetchone()[0], 3)
        with self.assertRaisesRegex(ValueError, "three persisted"):
            self.register(3)
        self.assertEqual(self.register(3, "2026-11-20"), 1)

    def test_registration_after_schema_initialization(self):
        # campaign_db creates and commits the table before register's write transaction.
        self.assertEqual(self.register(0), 1)
        self.assertEqual(self.register(1), 2)
        with campaign_db(self.root) as db:
            rows = db.execute("SELECT slot,state FROM r3born_uploads ORDER BY slot").fetchall()
        self.assertEqual([(r["slot"],r["state"]) for r in rows],
                         [(1,"reviewed"),(2,"reviewed")])

    def test_duplicate_and_required_description(self):
        self.register(0)
        with self.assertRaisesRegex(ValueError, "already been registered"):
            self.register(0)
        with self.assertRaisesRegex(ValueError, "description"):
            self.register(1, description="#r3born")

    def test_tamper_blocks_before_network(self):
        self.register(0)
        digest = hashlib.sha256(self.files[0].read_bytes()).hexdigest()
        self.files[0].write_bytes(b"changed")
        with patch("r3born_campaign.refresh_access_token") as token:
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                upload_reviewed(self.root, "2026-11-19", 1, digest,
                                {"SHADOW_YOUTUBE_CHANNEL_ID":"UC" + "x"*22},
                                confirmed=True,audience="not-made-for-kids")
            token.assert_not_called()

    def test_confirmation_required(self):
        self.register(0)
        digest = hashlib.sha256(self.files[0].read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "Explicit private confirmation"):
            upload_reviewed(self.root,"2026-11-19",1,digest,{},confirmed=False)

    def test_metadata_rejects_campaign_banned_reference(self):
        with self.assertRaisesRegex(ValueError, "FaZe"):
            self.register(0, description="#r3born @hardscope FaZe")

    def test_ffprobe_detects_missing_audio(self):
        example = {"format":{"duration":"35.0"},"streams":[{"codec_type":"video","width":1080,"height":1920}]}
        with patch("r3born_campaign.subprocess.run") as probe:
            probe.return_value.stdout = json.dumps(example)
            with self.assertRaisesRegex(ValueError, "original audio"):
                validate_media(self.files[0])

    def test_ffprobe_detects_landscape(self):
        example = {"format":{"duration":"35.0"},"streams":[{"codec_type":"video","width":1920,"height":1080},{"codec_type":"audio"}]}
        with patch("r3born_campaign.subprocess.run") as probe:
            probe.return_value.stdout = json.dumps(example)
            with self.assertRaisesRegex(ValueError, "vertical"):
                validate_media(self.files[0])

    def test_ffprobe_accepts_vertical_with_audio(self):
        example = {"format":{"duration":"35.0"},"streams":[{"codec_type":"video","width":1080,"height":1920},{"codec_type":"audio"}]}
        with patch("r3born_campaign.subprocess.run") as probe:
            probe.return_value.stdout = json.dumps(example)
            self.assertEqual(validate_media(self.files[0])["duration"], 35)

    def test_download_rejects_non_https(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            download_campaign("http://example.com/video.mp4",
                              self.root/"x.mp4","0"*64)


if __name__ == "__main__":
    unittest.main()
