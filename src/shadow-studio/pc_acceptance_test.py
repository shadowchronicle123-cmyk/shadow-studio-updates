"""Offline acceptance diagnostics and non-destructive cleanup tests."""
import contextlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pc_acceptance as pc


class AcceptanceTests(unittest.TestCase):
    def test_missing_tools_and_low_space(self):
        with patch.object(pc.shutil, "disk_usage", return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(RuntimeError, "1 GiB"):
                pc.run(Path("."))
        with patch.object(pc.shutil, "disk_usage", return_value=SimpleNamespace(free=pc.MIN_FREE_BYTES)):
            for missing in ("ffmpeg", "ffprobe"):
                with patch.object(pc.shutil, "which", side_effect=lambda name: None if name == missing else name):
                    with patch.object(pc.subprocess, "run"):
                        with self.assertRaisesRegex(RuntimeError, missing):
                            pc.run(Path("."))

    def test_unwritable_temp_is_reported(self):
        with patch.object(pc.tempfile, "mkdtemp", side_effect=PermissionError), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(pc.main(), 1)
        self.assertIn("writable temporary", out.getvalue())

    def test_staged_output_cleanup_and_reports(self):
        with tempfile.TemporaryDirectory(prefix="Shadow Studio test ") as folder:
            parent = Path(folder)
            existing = parent / "existing settings.txt"
            existing.write_text("keep", encoding="utf-8")
            for index, (failure, code, text) in enumerate([
                (None, 0, "PASS"),
                (ModuleNotFoundError(name="PIL"), 1, "Missing Python dependency"),
                (PermissionError("sample write failed"), 1, "sample write failed"),
                (KeyboardInterrupt(), 130, "CANCELLED"),
            ]):
                root = parent / str(index)
                root.mkdir()
                real_mkdtemp = pc.tempfile.mkdtemp

                def create(*args, **kwargs):
                    return str(root) if not args and "dir" not in kwargs else real_mkdtemp(*args, **kwargs)

                def render(work):
                    (work / "short.mp4").write_bytes(b"fixture")
                    if failure:
                        raise failure
                    (work / "test-background.mp4").touch()
                    return ["PASS"]

                with patch.object(pc.tempfile, "mkdtemp", side_effect=create), patch.object(pc, "run", side_effect=render):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(pc.main(), code)
                self.assertIn(text, (root / "acceptance-report.txt").read_text(encoding="utf-8"))
                self.assertFalse(list(root.glob("render staging *")))
                self.assertEqual((root / "sample" / "short.mp4").is_file(), code == 0)
                self.assertFalse((root / "sample" / "test-background.mp4").exists())
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
