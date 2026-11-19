"""Offline regression tests for streamer clip intake."""
import json
from pathlib import Path
import tempfile

from streamer_clips import queue, validate


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
