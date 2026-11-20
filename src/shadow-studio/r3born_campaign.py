"""Explicitly reviewed R3born campaign clips: private-only, three slots per UTC day.

Never scrape a stream or bypass access controls. Download only a direct, authorized
HTTPS campaign asset with a known SHA-256; registration is not upload approval.
"""
import argparse
from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
from urllib.parse import urlsplit

import requests
from production_worker import ledger
from private_upload import CREATE, MAX_BYTES, transfer, verify_channel, refresh_access_token

REQUIRED_DESCRIPTION = "#r3born @hardscope"
MAX_SLOTS = 3


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download_campaign(url, destination, expected_sha256, *, http=requests):
    """Fetch an explicitly authorized direct HTTPS download, without redirects."""
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
        parsed.password or parsed.port not in (None, 443) or parsed.fragment):
        raise ValueError("A direct HTTPS campaign download URL is required")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
        raise ValueError("Expected SHA-256 is required")
    target = Path(destination)
    if target.exists():
        raise ValueError("Refusing to overwrite an existing media file")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".partial")
    if temp.exists():
        raise ValueError("Partial download exists; inspect it first")
    digest = hashlib.sha256()
    size = 0
    try:
        with http.get(url, stream=True, timeout=(8, 90), allow_redirects=False) as response:
            if response.status_code != 200:
                raise ValueError("Campaign download unavailable; redirects are not followed")
            with temp.open("xb") as output:
                for block in response.iter_content(1024 * 1024):
                    if not block:
                        continue
                    size += len(block)
                    if size > MAX_BYTES:
                        raise ValueError("Campaign file exceeds the private-upload limit")
                    digest.update(block)
                    output.write(block)
        if not size or digest.hexdigest() != expected_sha256.lower():
            raise ValueError("Campaign download SHA-256 mismatch")
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)
    return target



def validate_media(path):
    """Check the actual rendered Short before labeling it reviewed."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,width,height", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, check=True)
        info = json.loads(result.stdout)
        streams = info.get("streams", [])
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        duration = float(info.get("format", {}).get("duration", 0))
        if not video or not audio or not 7 <= duration <= 180:
            raise ValueError("Clip needs video, original audio track, and 7-180 second duration")
        width, height = int(video.get("width", 0)), int(video.get("height", 0))
        if width < 360 or height < 640 or not 0.53 <= width / height <= 0.60:
            raise ValueError("Clip must be vertical 9:16 with usable resolution")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError("Unable to verify rendered MP4 with ffprobe") from exc
    return {"duration": duration, "width": width, "height": height}


@contextmanager
def campaign_db(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "production.sqlite3", timeout=12)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS r3born_uploads (
        id INTEGER PRIMARY KEY, day TEXT NOT NULL, slot INTEGER NOT NULL,
        path TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL, description TEXT NOT NULL,
        permission_reference TEXT NOT NULL, state TEXT NOT NULL,
        video_id TEXT, UNIQUE(day,slot))""")
    db.commit()
    try:
        with db:
            yield db
    finally:
        db.close()


def register(root, day, path, title, permission_reference, expected_sha256,
             *, description=REQUIRED_DESCRIPTION):
    """Record reviewed exact bytes. Upload still needs explicit confirmation."""
    day = date.fromisoformat(day).isoformat()
    path = Path(path).resolve(strict=True)
    if not path.is_file() or path.suffix.lower() != ".mp4":
        raise ValueError("Use an existing captioned MP4")
    if not 0 < path.stat().st_size <= MAX_BYTES:
        raise ValueError("MP4 must be between 1 byte and 50 MiB")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
        raise ValueError("Approved SHA-256 is required")
    if sha256(path) != expected_sha256.lower():
        raise ValueError("MP4 differs from approved bytes")
    if not title.strip() or not permission_reference.strip():
        raise ValueError("Title and documented campaign repost permission are required")
    if "faze" in title.casefold() or "faze" in description.casefold():
        raise ValueError("Campaign prohibits FaZe references in upload metadata")
    validate_media(path)
    if not all(term in description.lower() for term in ("#r3born", "@hardscope")):
        raise ValueError("Campaign description must include #r3born and @hardscope")
    with campaign_db(root) as db:
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM r3born_uploads WHERE sha256=?", (expected_sha256.lower(),)).fetchone():
            raise ValueError("These bytes have already been registered")
        used = {r[0] for r in db.execute("SELECT slot FROM r3born_uploads WHERE day=?", (day,))}
        slot = next((n for n in range(1, MAX_SLOTS + 1) if n not in used), None)
        if slot is None:
            raise ValueError("All three persisted campaign slots are occupied")
        db.execute("""INSERT INTO r3born_uploads
            (day,slot,path,sha256,title,description,permission_reference,state)
            VALUES (?,?,?,?,?,?,?,'reviewed')""",
            (day,slot,str(path),expected_sha256.lower(),title.strip(),
             description,permission_reference.strip()))
        return slot


def upload_reviewed(root, day, slot, expected_sha256, env, *,
                    confirmed=False, audience=None, http=requests):
    """Explicit private-only upload; ambiguous transfer results require reconciliation."""
    day = date.fromisoformat(day).isoformat()
    if not confirmed or audience not in ("made-for-kids", "not-made-for-kids"):
        raise ValueError("Explicit private confirmation and audience selection required")
    with ledger(root) as db:
        if db.execute("SELECT stopped FROM control").fetchone()[0]:
            raise ValueError("Production is stopped")
    with campaign_db(root) as db:
        row = db.execute("SELECT * FROM r3born_uploads WHERE day=? AND slot=?", (day,slot)).fetchone()
        if row is None or row["state"] not in ("reviewed", "scheduled"):
            raise ValueError("Slot is not reviewed or due; check YouTube Studio before retrying")
        if row["state"] == "scheduled":
            if "scheduled_at" not in row.keys() or not row["scheduled_at"]:
                raise ValueError("Scheduled slot has no valid due time")
            try:
                due = datetime.fromisoformat(row["scheduled_at"])
            except ValueError:
                raise ValueError("Scheduled slot has an invalid due time") from None
            if due.tzinfo is None or due.utcoffset() is None or due > datetime.now(timezone.utc):
                raise ValueError("Scheduled private upload is not due")
        path = Path(row["path"])
        if (not path.is_file() or sha256(path) != row["sha256"] or
            row["sha256"] != expected_sha256.lower()):
            raise ValueError("Reviewed media checksum mismatch")
        contents = path.read_bytes()
        if not contents or len(contents) > MAX_BYTES:
            raise ValueError("Invalid MP4 size")
    token = env.get("SHADOW_YOUTUBE_ACCESS_TOKEN") or refresh_access_token(env, http)
    channel_id = env.get("SHADOW_YOUTUBE_CHANNEL_ID")
    verify_channel(token, channel_id, http)
    with campaign_db(root) as db:
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        # Preserve the historical manually reviewed upload path, but require an
        # unchanged, due schedule for newly scheduled private handoffs.
        if row["state"] == "scheduled":
            updated = db.execute("""UPDATE r3born_uploads SET state='submitting'
                WHERE day=? AND slot=? AND state='scheduled' AND scheduled_at=?
                AND sha256=?""", (day,slot,row["scheduled_at"],row["sha256"]))
        else:
            updated = db.execute("""UPDATE r3born_uploads SET state='submitting'
                WHERE day=? AND slot=? AND state='reviewed' AND sha256=?""",
                (day,slot,row["sha256"]))
        if updated.rowcount != 1:
            raise ValueError("Slot already claimed or schedule changed")
    auth = {"Authorization": "Bearer " + token}
    media_started = False
    phase = "session_initialization"
    try:
        initialized = http.post(CREATE, params={"uploadType":"resumable","part":"snippet,status"},
            headers={**auth,"X-Upload-Content-Type":"video/mp4",
                     "X-Upload-Content-Length":str(len(contents))},
            json={"snippet":{"title":row["title"],"description":row["description"]},
                  "status":{"privacyStatus":"private",
                            "selfDeclaredMadeForKids":audience=="made-for-kids"}},
            timeout=(8,30), allow_redirects=False)
        location = initialized.headers.get("Location","")
        parsed = urlsplit(location)
        if (initialized.status_code != 200 or parsed.scheme != "https" or
            parsed.hostname not in ("www.googleapis.com","youtube.googleapis.com") or
            parsed.port not in (None,443) or parsed.username or parsed.password or
            parsed.fragment or parsed.path != "/upload/youtube/v3/videos" or not parsed.query):
            raise RuntimeError("Invalid YouTube resumable upload session")
        def check_stop():
            with ledger(root) as db:
                if db.execute("SELECT stopped FROM control").fetchone()[0]:
                    raise RuntimeError("Production stopped")
        check_stop()
        media_started = True
        phase = "media_transfer"
        uploaded = transfer(http, location, auth, contents, check_stop)
        phase = "response_validation"
        response = uploaded.json()
        video_id = str(response.get("id",""))
        if (not re.fullmatch(r"[A-Za-z0-9_-]{11}",video_id) or
            response.get("status",{}).get("privacyStatus") != "private" or
            response.get("snippet",{}).get("channelId") != channel_id):
            raise RuntimeError("YouTube did not confirm private upload and channel")
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError,
            requests.RequestException) as exc:
        with campaign_db(root) as db:
            db.execute("UPDATE r3born_uploads SET state=? WHERE day=? AND slot=?",
                       ("needs_reconciliation" if media_started else "retryable",day,slot))
        # Never include response bodies, resumable session URLs, OAuth tokens, or
        # exception strings: those may contain credentials or private metadata.
        kind = type(exc).__name__
        raise RuntimeError(
            f"Upload unconfirmed at {phase} ({kind}); "
            "inspect YouTube Studio before any retry") from None
    with campaign_db(root) as db:
        db.execute("UPDATE r3born_uploads SET state='private',video_id=? WHERE day=? AND slot=?",
                   (video_id,day,slot))
    return video_id


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state",required=True)
    sub = p.add_subparsers(dest="action",required=True)
    reg = sub.add_parser("register")
    for flag in ("day","file","title","permission-reference","sha256"):
        reg.add_argument("--"+flag,required=True)
    up = sub.add_parser("upload")
    for flag in ("day","slot","sha256"):
        up.add_argument("--"+flag,required=True)
    up.add_argument("--confirm-private",action="store_true")
    up.add_argument("--audience",choices=("made-for-kids","not-made-for-kids"),required=True)
    up.add_argument("--credentials",required=True)
    args = p.parse_args()
    if args.action == "register":
        print("Reviewed slot:",register(args.state,args.day,args.file,args.title,
              args.permission_reference,args.sha256))
    else:
        from private_upload import read_worker_settings
        print("Private YouTube ID:",upload_reviewed(args.state,args.day,int(args.slot),
              args.sha256,read_worker_settings(Path(args.credentials)),
              confirmed=args.confirm_private,audience=args.audience))


if __name__ == "__main__":
    main()
