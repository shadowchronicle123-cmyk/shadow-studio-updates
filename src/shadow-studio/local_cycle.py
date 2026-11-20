"""Opt-in scheduled Windows production cycle using an approved local media inventory.

Never downloads footage or uploads to YouTube. No paid APIs. Safe for repeating
Windows Task Scheduler invocation after the owner explicitly installs it.
"""
import argparse
from contextlib import closing
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import shutil
import sys
import uuid
from zoneinfo import ZoneInfo

from production_health import inspect
import worker_queue as queue

MEDIA_EXTENSIONS = {".mp4",".mov",".webm",".mkv"}
SCOPES = {"edit-and-repost","commercial-edit-and-repost"}
MIN_FREE_BYTES = 1024 * 1024 * 1024


def _json_object(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value,dict):
        raise ValueError("Configuration must be a JSON object")
    return value


def config(path):
    info = _json_object(path)
    if any(word in k.lower() for k in info for word in ("token","secret","password","oauth")):
        raise ValueError("Never store credentials in the local cycle configuration")
    for key in ("state_dir","ideas_file","media_dir","rights_manifest","timezone"):
        if not isinstance(info.get(key),str) or not info[key].strip():
            raise ValueError("Missing required configuration field: "+key)
    if type(info.get("enabled")) is not bool:
        raise ValueError("Set enabled to true or false explicitly")
    ZoneInfo(info["timezone"])  # Fail closed if Windows timezone database is unavailable.
    if "auto_highlights" in info and type(info["auto_highlights"]) is not bool:
        raise ValueError("auto_highlights must be an explicit boolean")
    if info.get("auto_highlights") and (
        not isinstance(info.get("clip_database"),str) or not info["clip_database"]):
        raise ValueError("clip_database is required for automatic licensed highlights")
    return info


def _checksum(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):
            h.update(block)
    return h.hexdigest()


def validate_rights(info, *, today=None):
    """Reject unlisted, modified, expired, unlicensed or symlinked media."""
    now = today or date.today()
    root = Path(info["media_dir"]).resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Authorized media root must be a real directory")
    manifest = json.loads(Path(info["rights_manifest"]).read_text(encoding="utf-8"))
    if not isinstance(manifest,list) or not manifest:
        raise ValueError("An explicit approved media manifest is required")
    expected = set()
    for item in manifest:
        if not isinstance(item,dict):
            raise ValueError("Rights entry must be an object")
        name = item.get("file")
        if (not isinstance(name,str) or not name or name != Path(name).name or
            name in (".","..") or name in expected):
            raise ValueError("Rights manifest must contain unique media basenames")
        if item.get("license_scope") not in SCOPES:
            raise ValueError("Media license must explicitly authorize editing and reposting")
        if not isinstance(item.get("permission_reference"),str) or len(item["permission_reference"].strip()) < 3:
            raise ValueError("Each media file requires a documented permission reference")
        expiry = item.get("expires_at")
        if expiry:
            if date.fromisoformat(expiry) < now:
                raise ValueError("Source license expired")
        elif item.get("perpetual") is not True:
            raise ValueError("Set a rights expiration date or explicitly confirm perpetual permission")
        digest = item.get("sha256")
        if not isinstance(digest,str) or not re.fullmatch(r"[0-9a-fA-F]{64}",digest):
            raise ValueError("Every source requires an approved SHA-256")
        file = root / name
        if (not file.is_file() or file.is_symlink() or file.suffix.lower() not in MEDIA_EXTENSIONS or
            not 0 < file.stat().st_size <= 512*1024*1024 or file.resolve() != root / name):
            raise ValueError("Approved footage file missing, symlinked or unsupported")
        if _checksum(file) != digest.lower():
            raise ValueError("Authorized footage differs from its approved SHA-256")
        expected.add(name)
    discovered = {x.name for x in root.iterdir() if x.is_file() and x.suffix.lower() in MEDIA_EXTENSIONS}
    if expected != discovered:
        raise ValueError("Media folder contains unapproved or missing video files")
    if any(x.is_symlink() for x in root.iterdir()):
        raise ValueError("Media folder contains a symlink")
    ideas = json.loads(Path(info["ideas_file"]).read_text(encoding="utf-8"))
    if not isinstance(ideas,list) or not ideas or not all(isinstance(s,str) and 3 <= len(s.strip()) <= 350 for s in ideas):
        raise ValueError("Ideas must be an owner-approved JSON list of original premises")
    return len(expected)


def enqueue_licensed_highlights(info, *, now=None):
    """Only scan registered clips matching the current approved source inventory."""
    if not info.get("auto_highlights",False):
        return 0
    clip_db=Path(info["clip_database"]).resolve(strict=True)
    if not clip_db.is_file():
        raise ValueError("Registered clip ledger is missing")
    media_root=Path(info["media_dir"]).resolve(strict=True)
    approved=json.loads(Path(info["rights_manifest"]).read_text(encoding="utf-8"))
    expected={r["file"]:r["sha256"].lower() for r in approved}
    with closing(sqlite3.connect(clip_db.as_uri()+"?mode=ro",uri=True,timeout=10)) as db:
        db.row_factory=sqlite3.Row
        registered=db.execute("""SELECT id,file,sha256,license_scope,state
                                 FROM clips ORDER BY id LIMIT 101""").fetchall()
    if len(registered)>100:
        raise ValueError("Clip intake exceeds the explicit local scan limit")
    created=0
    for row in registered:
        if row["license_scope"] not in SCOPES or row["state"] in ("rejected","blocked","revoked"):
            continue
        source=Path(row["file"])
        if (not source.is_file() or source.is_symlink() or
            source.resolve().parent != media_root or
            expected.get(source.name)!=row["sha256"]):
            continue
        sha=row["sha256"]
        key="highlight:"+hashlib.sha256((str(clip_db)+"|"+str(row["id"])+"|"+sha).encode("utf-8")).hexdigest()
        _,added=queue.enqueue(info["state_dir"],"highlight_scan",key,
            {"clip_database":str(clip_db),"clip_id":row["id"],
             "source_sha256":sha},now=now,max_attempts=1)
        created+=int(added)
    return created


def record_alerts(root, report, *, now=None):
    """Deduplicate persistent local alerts without outbound requests."""
    stamp = queue.utc(now)
    current = set()
    new = []
    with queue.database(root) as db:
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        for issue in report["issues"]:
            code = str(issue["code"])
            job = str(issue.get("job",""))
            key = hashlib.sha256((code+"|"+job).encode("utf-8")).hexdigest()
            current.add(key)
            old = db.execute("SELECT resolved FROM queue_alerts WHERE alert_key=?",(key,)).fetchone()
            if old is None:
                db.execute("""INSERT INTO queue_alerts
                  (alert_key,code,job_id,first_seen,last_seen,occurrences,resolved)
                  VALUES (?,?,?,?,?,1,0)""",(key,code,job,stamp,stamp))
            else:
                db.execute("""UPDATE queue_alerts SET last_seen=?, occurrences=occurrences+1,
                  resolved=0 WHERE alert_key=?""",(stamp,key))
            if old is None or old["resolved"]:
                new.append({"code":code, "job":job, "severity":issue["severity"], "timestamp":stamp})
        active = db.execute("SELECT alert_key FROM queue_alerts WHERE resolved=0").fetchall()
        for row in active:
            if row["alert_key"] not in current:
                db.execute("UPDATE queue_alerts SET resolved=1 WHERE alert_key=?",(row["alert_key"],))
    return new


def _write_report(root, report, events):
    root=Path(root)
    root.mkdir(parents=True,exist_ok=True)
    tmp=root/("health-"+uuid.uuid4().hex+".tmp")
    try:
        tmp.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
        os.replace(tmp,root/"health-latest.json")
    finally:
        tmp.unlink(missing_ok=True)
    if events:
        # Local alert inbox for dashboard integration. No credentials or exception strings.
        with (root/"alerts.jsonl").open("a",encoding="utf-8") as out:
            for event in events:
                out.write(json.dumps(event)+"\n")


def cycle(info, *, now=None, processor=None):
    """One bounded tick; Task Scheduler supplies the repetition."""
    moment=now or datetime.now(timezone.utc)
    root=Path(info["state_dir"])
    if not info["enabled"]:
        return {"status":"disabled", "queued":False}
    if shutil.disk_usage(root if root.exists() else root.parent).free < MIN_FREE_BYTES:
        raise RuntimeError("Insufficient free disk space for local rendering")
    today=moment.astimezone(ZoneInfo(info["timezone"])).date()
    licensed=validate_rights(info,today=today)
    job_id,created=queue.enqueue(root,"story_day","story:"+today.isoformat(),
            {"day":today.isoformat(),"ideas_file":info["ideas_file"],
             "media_dir":info["media_dir"],"timezone":info["timezone"]},now=moment)
    added_highlights=enqueue_licensed_highlights(info,now=moment)
    result=queue.run_once(root,processor=processor,now=moment)
    report=inspect(root,now=moment)
    alerts=record_alerts(root,report,now=moment)
    _write_report(root,report,alerts)
    return {"status":result["status"],"job_id":job_id,"queued":created,
            "licensed_highlight_jobs":added_highlights,
            "approved_sources":licensed,"health":report["status"],"new_alerts":len(alerts)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,required=True)
    parser.add_argument("action",choices=("once","check"))
    args=parser.parse_args()
    try:
        info=config(args.config)
        if args.action=="check":
            result={"configuration":"valid","approved_sources":validate_rights(info)}
        else:
            result=cycle(info)
        print(json.dumps(result,indent=2))
        return 0 if result.get("health","ok") in ("ok",) else 2
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,ImportError):
        # Exception messages may contain source paths or sensitive information.
        print(json.dumps({"status":"blocked","reason":"configuration_or_worker_error"}))
        return 2


if __name__=="__main__":
    sys.exit(main())
