"""Rights-aware, offline streamer clip intake. No downloading or publishing."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

ALLOWED = {".mp4", ".mov", ".mkv", ".webm"}
MAX_BYTES = 2 * 1024**3


def validate(item, media_root):
    """Require creator-specific permission evidence before queueing a local clip."""
    required = ("creator", "source_url", "permission_reference", "license_scope", "file", "start_seconds", "end_seconds")
    if not isinstance(item, dict) or any(not item.get(key) and item.get(key) != 0 for key in required):
        raise ValueError("Missing clip metadata or documented permission")
    if item["license_scope"] not in ("repost", "edit-and-repost", "commercial-edit-and-repost"):
        raise ValueError("Permission scope does not authorize reposting")
    source = urlsplit(str(item["source_url"]))
    if (source.scheme != "https" or source.hostname not in
            {"www.youtube.com", "youtube.com", "youtu.be", "www.twitch.tv", "clips.twitch.tv"}
            or source.username or source.password or source.port not in (None, 443)):
        raise ValueError("Unrecognized source URL")
    start, end = item["start_seconds"], item["end_seconds"]
    if type(start) not in (int, float) or type(end) not in (int, float) or not 0 <= start < end <= start + 180:
        raise ValueError("Clip timing must be between 0 and 180 seconds")
    root = Path(media_root).resolve(strict=True)
    candidate = (root / str(item["file"])).resolve(strict=True)
    if not candidate.is_relative_to(root) or not candidate.is_file() or candidate.suffix.lower() not in ALLOWED:
        raise ValueError("Clip must be a local media file inside the approved folder")
    if not 0 < candidate.stat().st_size <= MAX_BYTES:
        raise ValueError("Clip file is empty or too large")
    for field in ("creator", "permission_reference"):
        if not isinstance(item[field], str) or len(item[field].strip()) < 3:
            raise ValueError("Invalid creator or permission evidence")
    digest = hashlib.sha256()
    with candidate.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"creator": item["creator"].strip(), "source_url": item["source_url"],
            "permission_reference": item["permission_reference"], "license_scope": item["license_scope"],
            "file": str(candidate), "start_seconds": start, "end_seconds": end,
            "sha256": digest.hexdigest(), "state": "review_required"}


def queue(manifest, media_root, database):
    data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Manifest must contain a list of clips")
    validated = [validate(item, media_root) for item in data]
    db_path = Path(database)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS clips (
            id INTEGER PRIMARY KEY, creator TEXT NOT NULL, source_url TEXT NOT NULL,
            permission_reference TEXT NOT NULL, license_scope TEXT NOT NULL,
            file TEXT NOT NULL, start_seconds REAL NOT NULL, end_seconds REAL NOT NULL,
            sha256 TEXT NOT NULL, state TEXT NOT NULL,
            UNIQUE(source_url, start_seconds, end_seconds), UNIQUE(sha256, start_seconds, end_seconds)
        )""")
        added = 0
        for item in validated:
            columns = tuple(item)
            values = tuple(item.values())
            cursor = db.execute(
                "INSERT OR IGNORE INTO clips (" + ",".join(columns) + ") VALUES (" +
                ",".join("?" for _ in columns) + ")", values)
            added += cursor.rowcount
    return {"submitted": len(validated), "queued": added, "state": "review_required", "uploads": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--media-root", required=True)
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    print(json.dumps(queue(args.manifest, args.media_root, args.database)))


if __name__ == "__main__":
    main()
