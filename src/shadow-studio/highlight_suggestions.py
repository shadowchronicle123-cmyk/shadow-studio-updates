"""Suggest audio-active Shorts from already-licensed local streamer footage.

Offline heuristics only; these are REVIEW CANDIDATES, not proof of engagement.
No speech recognition, titles, downloading, publishing or AI cloud API calls.
"""
import argparse
from array import array
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys

from streamer_clips import MAX_BYTES

SAMPLE_RATE = 8000
INTERVAL = 3
WINDOWS = (15, 25, 35, 50)
MAX_SUGGESTIONS = 3


def _licensed_clip(database, clip_id):
    with closing(sqlite3.connect(database,timeout=10)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM clips WHERE id=?",(int(clip_id),)).fetchone()
    if row is None:
        raise ValueError("Source clip is not registered in the rights ledger")
    if row["license_scope"] not in ("edit-and-repost","commercial-edit-and-repost"):
        raise ValueError("Editing and reposting permission is required")
    if row["state"] in ("rejected","blocked","revoked"):
        raise ValueError("The rights ledger rejected this source")
    source = Path(row["file"])
    if not source.is_file() or source.is_symlink() or not 0 < source.stat().st_size <= MAX_BYTES:
        raise ValueError("Approved local source is missing or unsuitable")
    digest=hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""):
            digest.update(chunk)
    if digest.hexdigest() != row["sha256"]:
        raise ValueError("Licensed source changed after SHA-256 registration")
    start,end=float(row["start_seconds"]),float(row["end_seconds"])
    if not 0 <= start < end <= start+180 or end-start < 15:
        raise ValueError("A licensed 15-180 second review range is required")
    return dict(row), source, start, end


def _audio_levels(path, start, span):
    """Decode at most 180 seconds of mono PCM for one-second audio activity scores."""
    command=["ffmpeg","-hide_banner","-nostdin","-loglevel","error",
             "-ss",str(start),"-i",str(path),"-t",str(span),"-map","0:a:0",
             "-vn","-ac","1","-ar",str(SAMPLE_RATE),"-f","s16le","pipe:1"]
    try:
        result=subprocess.run(command,capture_output=True,timeout=150,check=False)
    except (subprocess.TimeoutExpired,OSError) as exc:
        raise ValueError("Unable to inspect the licensed local audio") from None
    if result.returncode or len(result.stdout) < SAMPLE_RATE*2*5:
        raise ValueError("Source contains insufficient decodable audio")
    if len(result.stdout) > 180*SAMPLE_RATE*2+SAMPLE_RATE*2:
        raise ValueError("Unexpected audio output size")
    samples=array("h")
    samples.frombytes(result.stdout[:len(result.stdout)//2*2])
    levels=[]
    for offset in range(0,len(samples),SAMPLE_RATE):
        sample=samples[offset:offset+SAMPLE_RATE]
        if len(sample) < SAMPLE_RATE//4:
            break
        rms=math.sqrt(sum(int(s)*s for s in sample)/len(sample))/32768.0
        levels.append(rms)
    return levels


def suggest(levels, start_seconds, *, max_results=MAX_SUGGESTIONS):
    """Deterministic, explainable energy-based ranking with overlap suppression."""
    if len(levels) < 15 or not 1 <= max_results <= 10:
        raise ValueError("At least 15 seconds of audio activity is required")
    if not all(isinstance(x,(float,int)) and math.isfinite(x) and x >= 0 for x in levels):
        raise ValueError("Invalid audio activity measurements")
    if max(levels) < 0.001:
        return []  # Silent footage has no defensible audio-based highlights.
    items=[]
    for duration in WINDOWS:
        if duration > len(levels):
            continue
        positions=range(0,len(levels)-duration+1,INTERVAL)
        for offset in positions:
            chunk=levels[offset:offset+duration]
            mean=statistics.fmean(chunk)
            peak=max(chunk)
            contrast=peak-statistics.median(chunk)
            quiet=sum(x < 0.007 for x in chunk)/len(chunk)
            # Ranking is heuristic, not an outcome or speech-content prediction.
            score=mean*100 + peak*50 + contrast*20 - quiet*8
            items.append((score,offset,duration,mean,peak,quiet))
    items.sort(key=lambda x:(-x[0],x[1],x[2]))
    selected=[]
    for score,offset,duration,mean,peak,quiet in items:
        overlap=False
        for prior in selected:
            inter=max(0,min(offset+duration,prior["offset"]+prior["duration"])-max(offset,prior["offset"]))
            union=(duration+prior["duration"]-inter)
            if union and inter/union > 0.35:
                overlap=True
                break
        if overlap:
            continue
        selected.append({"offset":offset,"duration":duration,
            "start_seconds":round(start_seconds+offset,3),
            "end_seconds":round(start_seconds+offset+duration,3),
            "score":round(max(0,min(100,score)),2),
            "reason":"Higher local audio activity; listen and review context before selection",
            "state":"review_required"})
        if len(selected)>=max_results:
            break
    return selected


def save(database, clip_id, selections, expected_sha256):
    with closing(sqlite3.connect(database,timeout=12)) as db, db:
        db.row_factory=sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS clip_suggestions(
           id INTEGER PRIMARY KEY,clip_id INTEGER NOT NULL,source_sha256 TEXT NOT NULL,
           start_seconds REAL NOT NULL,end_seconds REAL NOT NULL,score REAL NOT NULL,
           reason TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'review_required',
           UNIQUE(clip_id,start_seconds,end_seconds))""")
        ids=[]
        for s in selections:
            db.execute("""INSERT OR IGNORE INTO clip_suggestions
              (clip_id,source_sha256,start_seconds,end_seconds,score,reason,state)
              VALUES (?,?,?,?,?,?,'review_required')""",
              (clip_id,expected_sha256,s["start_seconds"],s["end_seconds"],
               s["score"],s["reason"]))
            row=db.execute("""SELECT id,state FROM clip_suggestions WHERE
              clip_id=? AND start_seconds=? AND end_seconds=?""",
              (clip_id,s["start_seconds"],s["end_seconds"])).fetchone()
            if row["state"] not in ("review_required","approved","rejected"):
                raise ValueError("Unexpected persisted suggestion state")
            ids.append(row["id"])
        return ids


def generate(database, clip_id, *, max_results=MAX_SUGGESTIONS):
    clip,source,start,end=_licensed_clip(database,clip_id)
    levels=_audio_levels(source,start,end-start)
    selections=suggest(levels,start,max_results=max_results)
    ids=save(database,int(clip_id),selections,clip["sha256"])
    return {"clip_id":int(clip_id),"source_sha256":clip["sha256"],
            "method":"audio_activity_only_no_transcription",
            "suggestions":[{"id":id,**row} for id,row in zip(ids,selections)],
            "uploads":0}



def render_preview(database, suggestion_id, output_dir, *, anchor="center"):
    """Render a single review-only 9:16 candidate with original source audio."""
    import tempfile
    from quality_check import analyze
    from studio_tools import clip_media

    if anchor not in ("left","center","right"):
        raise ValueError("Choose left, center or right framing")
    with closing(sqlite3.connect(database,timeout=10)) as db:
        db.row_factory=sqlite3.Row
        suggestion=db.execute("SELECT * FROM clip_suggestions WHERE id=?",
                              (int(suggestion_id),)).fetchone()
    if suggestion is None or suggestion["state"] not in ("review_required","approved"):
        raise ValueError("Suggestion is unavailable or rejected")
    clip,source,start,end=_licensed_clip(database,suggestion["clip_id"])
    selected_start,selected_end=suggestion["start_seconds"],suggestion["end_seconds"]
    if (suggestion["source_sha256"] != clip["sha256"] or
        selected_start < start or selected_end > end or
        not 15 <= selected_end-selected_start <= 60):
        raise ValueError("Suggestion no longer matches the licensed review range")
    output_dir=Path(output_dir)
    output_dir.mkdir(parents=True,exist_ok=True)
    destination=output_dir/f'candidate-{suggestion["clip_id"]}-{suggestion_id}.mp4'
    if destination.exists():
        raise ValueError("Preview already exists; refusing to overwrite reviewed bytes")
    with tempfile.TemporaryDirectory(prefix="highlight-source-") as source_dir:
        snapshot=Path(source_dir)/("source"+source.suffix.lower())
        h=hashlib.sha256()
        with source.open("rb") as stream, snapshot.open("xb") as out:
            for chunk in iter(lambda:stream.read(1024*1024),b""):
                h.update(chunk)
                out.write(chunk)
        if h.hexdigest()!=clip["sha256"]:
            raise ValueError("Source changed while preparing the preview")
        with tempfile.TemporaryDirectory(prefix="highlight-stage-",dir=output_dir) as stage:
            rendered=clip_media(snapshot,selected_start,selected_end,
                                output_dir=Path(stage),width=1080,height=1920,
                                horizontal_anchor=anchor,normalize=True)
            report=analyze(rendered)
            if (not report["passed"] or
                (report["width"],report["height"])!=(1080,1920) or
                abs(report["duration_seconds"]-(selected_end-selected_start))>0.3 or
                abs(report["fps"]-30)>0.2):
                raise ValueError("Clip preview failed local audio/video quality checks")
            rendered.replace(destination)
    with destination.open("rb") as stream:
        digest=hashlib.file_digest(stream,"sha256").hexdigest()
    return {"preview":str(destination),"sha256":digest,"suggestion_id":int(suggestion_id),
            "state":"review_required","uploads":0}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database",required=True,type=Path)
    action=p.add_mutually_exclusive_group(required=True)
    action.add_argument("--clip-id",type=int)
    action.add_argument("--suggestion-id",type=int)
    p.add_argument("--max-results",type=int,default=3)
    p.add_argument("--output",type=Path)
    p.add_argument("--output-dir",type=Path)
    args=p.parse_args()
    if args.suggestion_id is not None:
        if args.output_dir is None:
            p.error("--suggestion-id requires --output-dir")
        result=render_preview(args.database,args.suggestion_id,args.output_dir)
    else:
        result=generate(args.database,args.clip_id,max_results=args.max_results)
    payload=json.dumps(result,indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(payload+"\n",encoding="utf-8")
    print(payload)


if __name__=="__main__":
    main()
