"""Offline real-FFmpeg highlight acceptance; never accesses the internet."""
import hashlib
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest

from highlight_suggestions import generate, suggest, render_preview
from streamer_clips import queue_items


class HighlightTests(unittest.TestCase):
    def setUp(self):
        if not shutil.which("ffmpeg"):
            self.skipTest("FFmpeg unavailable")
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.media=self.root/"media"
        self.media.mkdir()
        self.source=self.media/"licensed.mp4"
        subprocess.run(["ffmpeg","-nostdin","-y","-hide_banner","-loglevel","error",
            "-f","lavfi","-i","color=c=blue:s=160x90:r=15:d=36",
            "-f","lavfi","-i","sine=frequency=550:sample_rate=8000:duration=36",
            "-shortest","-c:v","libx264","-preset","ultrafast",
            "-c:a","aac","-pix_fmt","yuv420p",str(self.source)],
            check=True,timeout=90)
        self.db=self.root/"clips.sqlite3"
        self.item={"creator":"Authorized example streamer",
          "source_url":"https://www.twitch.tv/authorized-example",
          "permission_reference":"Written edit and repost approval reference",
          "license_scope":"commercial-edit-and-repost",
          "file":"licensed.mp4","start_seconds":0,"end_seconds":36}
        self.clip_id=queue_items([self.item],self.media,self.db)["ids"][0]

    def test_offline_audio_suggestions_persist_and_deduplicate(self):
        one=generate(self.db,self.clip_id)
        self.assertGreaterEqual(len(one["suggestions"]),1)
        self.assertEqual(one["uploads"],0)
        self.assertEqual(one["method"],"audio_activity_only_no_transcription")
        for row in one["suggestions"]:
            self.assertGreaterEqual(row["end_seconds"]-row["start_seconds"],15)
            self.assertLessEqual(row["end_seconds"],36)
            self.assertEqual(row["state"],"review_required")
        two=generate(self.db,self.clip_id)
        self.assertEqual([x["id"] for x in one["suggestions"]],
                         [x["id"] for x in two["suggestions"]])
        with closing(sqlite3.connect(self.db)) as db, db:
            count=db.execute("SELECT count(*) FROM clip_suggestions").fetchone()[0]
        self.assertEqual(count,len(one["suggestions"]))

    def test_tampered_source_rejected_before_ffmpeg(self):
        self.source.write_bytes(self.source.read_bytes()+b"modified")
        with self.assertRaisesRegex(ValueError,"SHA-256"):
            generate(self.db,self.clip_id)

    def test_non_editing_license_rejected(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE clips SET license_scope='repost' WHERE id=?",(self.clip_id,))
        with self.assertRaisesRegex(ValueError,"Editing and reposting"):
            generate(self.db,self.clip_id)

    def test_silent_audio_yields_no_speculative_highlights(self):
        self.assertEqual(suggest([0.0]*40,0.0),[])

    def test_overlap_suppression(self):
        levels=[0.01+0.15*(10 <= n < 15 or 30 <= n < 36) for n in range(60)]
        result=suggest(levels,8.0,max_results=3)
        for i,a in enumerate(result):
            for b in result[i+1:]:
                inter=max(0,min(a["end_seconds"],b["end_seconds"])-max(a["start_seconds"],b["start_seconds"]))
                union=(a["end_seconds"]-a["start_seconds"])+(b["end_seconds"]-b["start_seconds"])-inter
                self.assertLessEqual(inter/union,0.35+1e-8)


    def test_real_review_only_vertical_preview_preserves_audio(self):
        first=generate(self.db,self.clip_id)["suggestions"][0]
        result=render_preview(self.db,first["id"],self.root/"previews")
        self.assertTrue(Path(result["preview"]).is_file())
        self.assertEqual(result["uploads"],0)
        self.assertEqual(result["state"],"review_required")
        from quality_check import analyze
        report=analyze(Path(result["preview"]))
        self.assertTrue(report["passed"],report["blockers"])
        self.assertEqual((report["width"],report["height"]),(1080,1920))
        self.assertTrue(report["audio_present"])
        with self.assertRaisesRegex(ValueError,"already exists"):
            render_preview(self.db,first["id"],self.root/"previews")



if __name__=="__main__":
    unittest.main()
