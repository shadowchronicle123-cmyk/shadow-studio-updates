"""Rights-aware, offline streamer clip intake. No downloading or publishing."""
import argparse
from contextlib import closing, contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
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
    with candidate.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256")
    return {"creator": item["creator"].strip(), "source_url": item["source_url"],
            "permission_reference": item["permission_reference"], "license_scope": item["license_scope"],
            "file": str(candidate), "start_seconds": start, "end_seconds": end,
            "sha256": digest.hexdigest(), "state": "review_required"}


def queue(manifest, media_root, database):
    data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    return queue_items(data, media_root, database)


def queue_items(data, media_root, database):
    if not isinstance(data, list):
        raise ValueError("Manifest must contain a list of clips")
    validated = [validate(item, media_root) for item in data]
    db_path = Path(database)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    ids = []
    with closing(sqlite3.connect(db_path)) as db, db:
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
            match = db.execute("""SELECT id,sha256 FROM clips
                WHERE (source_url=? OR sha256=?) AND start_seconds=? AND end_seconds=? ORDER BY id LIMIT 1""",
                (item['source_url'],item['sha256'],item['start_seconds'],item['end_seconds'])).fetchone()
            if match[1] != item["sha256"]:
                raise ValueError("This source and time range already has a different approved file in the rights ledger")
            ids.append(match[0])
    return {"submitted": len(validated), "queued": added, "ids": ids, "state": "review_required", "uploads": 0}


def make_preview(database, clip_id, output_dir, anchor="center"):
    from advanced_tools import media_duration
    from quality_check import analyze
    from studio_tools import clip_media

    if anchor not in ("left", "center", "right"):
        raise ValueError("Choose left, center or right framing")
    with closing(sqlite3.connect(database)) as db:
        db.row_factory = sqlite3.Row
        clip = db.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
    if clip is None:
        raise ValueError("Clip record was not found")
    if clip["license_scope"] not in ("edit-and-repost", "commercial-edit-and-repost"):
        raise ValueError("Preview requires permission to edit and repost the source")
    source = Path(clip["file"])
    with tempfile.TemporaryDirectory(prefix="streamer-preview-") as temp_dir:
        scratch = Path(temp_dir)
        snapshot = scratch / ("source" + source.suffix)
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as stream, snapshot.open("wb") as out:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(block)
                if size > MAX_BYTES:
                    raise ValueError("Source file exceeds the approved size limit")
                digest.update(block)
                out.write(block)
        if digest.hexdigest() != clip["sha256"]:
            raise ValueError("Source file changed after rights intake")
        span = clip["end_seconds"] - clip["start_seconds"]
        if not 3 <= span <= 60 or clip["end_seconds"] > media_duration(snapshot) + .05:
            raise ValueError("Choose a 3–60-second range within the source video")
        # Stage the final file on the destination filesystem for atomic placement.
        with tempfile.TemporaryDirectory(prefix="checked-clip-", dir=output_dir) as stage:
            rendered = clip_media(snapshot, clip["start_seconds"], clip["end_seconds"],
                                  output_dir=Path(stage), width=1080, height=1920,
                                  horizontal_anchor=anchor, normalize=True)
            report = analyze(rendered)
            if (not report['passed'] or (report['width'],report['height']) != (1080,1920)
                or abs(report['duration_seconds'] - span) > .2 or abs(report['fps'] - 30) > .1):
                raise ValueError("The preview failed audio, video or timing checks")
            destination = Path(output_dir) / rendered.name
            rendered.replace(destination)
            try:
                with rendered_reviews(database) as db:
                    with destination.open("rb") as stream:
                        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
                    db.execute("INSERT INTO rendered_reviews(video_id, clip_id, sha256) VALUES (?,?,?)",
                               (destination.stem, clip_id, checksum))
            except (OSError, sqlite3.Error):
                destination.unlink(missing_ok=True)
                raise
    return destination




@contextmanager
def rendered_reviews(database):
    with closing(sqlite3.connect(database)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS rendered_reviews (
            video_id TEXT PRIMARY KEY, clip_id INTEGER NOT NULL, sha256 TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'review_required'
        )""")
        yield db


def recent_reviews(database):
    if not database.is_file():
        return []
    with rendered_reviews(database) as db:
        return [dict(row) for row in db.execute("""
            SELECT r.video_id,r.state,c.creator,c.start_seconds,c.end_seconds
            FROM rendered_reviews r JOIN clips c ON c.id=r.clip_id
            ORDER BY r.rowid DESC LIMIT 50
        """)]


def decide_review(database, output_dir, video_id, decision):
    if not database.is_file():
        raise FileNotFoundError()
    with rendered_reviews(database) as db:
        entry = db.execute("SELECT sha256 FROM rendered_reviews WHERE video_id=?", (video_id,)).fetchone()
        if entry is None:
            raise FileNotFoundError()
        if decision == "approved":
            with (output_dir / (video_id + ".mp4")).open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != entry["sha256"]:
                    raise ValueError("Preview changed after quality checks. Render a new preview.")
        db.execute("UPDATE rendered_reviews SET state=? WHERE video_id=?", (decision, video_id))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--media-root", required=True)
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    print(json.dumps(queue(args.manifest, args.media_root, args.database)))


if __name__ == "__main__":
    main()
