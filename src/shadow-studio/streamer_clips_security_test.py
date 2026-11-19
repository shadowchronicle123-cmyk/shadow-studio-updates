"""Independent negative-case QA for streamer clip intake."""
import json
from pathlib import Path
import sqlite3
import tempfile
from streamer_clips import queue, validate

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    media = root / "licensed"
    media.mkdir()
    (media / "clip.mp4").write_bytes(b"fixture")
    item = dict(creator="Example creator", source_url="https://www.twitch.tv/example",
                permission_reference="Written permission reference 123",
                license_scope="commercial-edit-and-repost", file="clip.mp4",
                start_seconds=1, end_seconds=20)
    bad = [
        {"source_url": "http://www.twitch.tv/example"},
        {"source_url": "https://www.twitch.tv.evil.test/clip"},
        {"source_url": "https://www.twitch.tv@evil.test/clip"},
        {"source_url": "https://user:pass@www.twitch.tv/clip"},
        {"source_url": "https://www.twitch.tv:8443/clip"},
        {"start_seconds": True},
        {"end_seconds": "20"},
        {"end_seconds": 1},
        {"start_seconds": -1},
        {"file": "../outside.mp4"},
        {"license_scope": "unknown"},
    ]
    for change in bad:
        try:
            validate({**item, **change}, media)
        except (ValueError, OSError):
            pass
        else:
            raise AssertionError(f"Unsafe input accepted: {change}")
    manifest = root / "manifest.json"
    database = root / "clips.sqlite3"
    manifest.write_text(json.dumps([item, {**item, "source_url": "https://evil.test"}]))
    try:
        queue(manifest, media, database)
    except (ValueError, OSError):
        pass
    else:
        raise AssertionError("Mixed valid/invalid manifest accepted")
    if database.exists():
        with sqlite3.connect(database) as db:
            assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='clips'").fetchone() is None
    manifest.write_text(json.dumps([item]))
    assert queue(manifest, media, database)["queued"] == 1
    assert queue(manifest, media, database)["queued"] == 0
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT state FROM clips").fetchall() == [("review_required",)]
print("PASS independent URL spoofing, metadata, atomicity and review-only checks")
