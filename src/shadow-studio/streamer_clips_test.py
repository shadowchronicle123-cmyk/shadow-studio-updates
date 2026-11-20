"""Offline regression tests for streamer clip intake."""
import json
from pathlib import Path
import tempfile
import sys
from types import ModuleType
from unittest.mock import patch

from streamer_clips import queue, validate, make_preview


def main():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        media = root / "licensed"
        media.mkdir()
        (media / "clip.mp4").write_bytes(b"local fixture")
        item = {"creator": "Example streamer", "source_url": "https://www.twitch.tv/example",
                "permission_reference": "Written permission, agreement #123",
                "license_scope": "commercial-edit-and-repost", "file": "clip.mp4",
                "start_seconds": 10, "end_seconds": 42}
        manifest = root / "clips.json"
        manifest.write_text(json.dumps([item]))
        database = root / "clips.sqlite3"
        assert queue(manifest, media, database)["queued"] == 1
        assert queue(manifest, media, database)["queued"] == 0
        # A new preview destination should be created before staging.
        # Missing licensed source must still fail closed.
        missing_output = root / "new" / "preview"
        (media / "clip.mp4").unlink()
        try:
            make_preview(database, 1, missing_output)
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("Missing licensed source was accepted")
        assert missing_output.is_dir()
        (media / "clip.mp4").write_bytes(b"local fixture")
        # Exercise a successful first-use preview with offline media stubs.
        duration = ModuleType("advanced_tools")
        duration.media_duration = lambda source: 45
        quality = ModuleType("quality_check")
        quality.analyze = lambda output: {
            "passed": True, "width": 1080, "height": 1920,
            "duration_seconds": 32, "fps": 30,
        }
        studio = ModuleType("studio_tools")
        def render(source, start, end, output_dir, **kwargs):
            assert source.read_bytes() == b"local fixture"
            assert (start, end) == (10, 42)
            assert kwargs["width"] == 1080 and kwargs["height"] == 1920
            output = Path(output_dir) / "preview.mp4"
            output.write_bytes(b"offline rendered fixture")
            return output
        studio.clip_media = render
        fresh_output = root / "first-successful-preview" / "nested"
        with patch.dict(sys.modules, {
            "advanced_tools": duration, "quality_check": quality,
            "studio_tools": studio,
        }):
            result = make_preview(database, 1, fresh_output)
        assert result == fresh_output / "preview.mp4"
        assert result.read_bytes() == b"offline rendered fixture"
        assert not list(fresh_output.glob("checked-clip-*"))
        for update in ({"permission_reference": ""}, {"license_scope": "unknown"},
                       {"file": "../clips.json"}, {"end_seconds": 500},
                       {"source_url": "https://example.com/video"}):
            try:
                validate({**item, **update}, media)
            except (ValueError, OSError):
                pass
            else:
                raise AssertionError(f"Unsafe clip accepted: {update}")
    print("PASS rights evidence, path safety, timing, deduplication and review-only queue")


if __name__ == "__main__":
    main()
