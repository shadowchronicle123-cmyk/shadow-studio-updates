"""Local or remote ShadowChronicles worker. No publishing or paid providers."""
import argparse
from contextlib import contextmanager
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
from zoneinfo import ZoneInfo

from captions import read_captions
from quality_check import analyze
from renderer import generate
from story_engine import make_story

VOICES = (('en-us','en-gb'), ('en-us','en-sc'), ('en-gb','en-sc'))
MEDIA_TYPES = {'.mp4', '.mov', '.webm', '.mkv'}
ATTEMPTS = 2


class Stopped(Exception):
    pass


@contextmanager
def ledger(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root/'production.sqlite3', timeout=12)
    db.row_factory = sqlite3.Row
    try:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS control (id INTEGER PRIMARY KEY CHECK(id=1), stopped INTEGER NOT NULL)')
            db.execute('INSERT OR IGNORE INTO control VALUES (1,0)')
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                day TEXT PRIMARY KEY, state TEXT NOT NULL, idea_hash TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL DEFAULT '', voice TEXT NOT NULL, clip TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, progress INTEGER NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '', fingerprint TEXT UNIQUE,
                duration REAL, artifact TEXT
            )''')
            db.execute('''CREATE TABLE IF NOT EXISTS private_uploads (
                day TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, state TEXT NOT NULL, video_id TEXT
            )''')
            yield db
    finally:
        db.close()


def set_stop(root, stopped):
    with ledger(root) as db:
        db.execute('UPDATE control SET stopped=? WHERE id=1', (int(stopped),))


def status(root):
    with ledger(root) as db:
        return {'stopped':bool(db.execute('SELECT stopped FROM control').fetchone()[0]),
                'daily_limit':1, 'paid_provider_limit_usd':0,
                'jobs':[dict(row) for row in db.execute(
                    '''SELECT jobs.day,jobs.state,title,attempts,progress,message,duration,artifact,
                       private_uploads.state AS upload_state, video_id FROM jobs
                       LEFT JOIN private_uploads ON jobs.day=private_uploads.day
                       ORDER BY jobs.day DESC LIMIT 60''')]}


def _hash(text):
    return hashlib.sha256(' '.join(text.casefold().split()).encode('utf-8')).hexdigest()


def reserve(root, day, ideas, clips):
    with ledger(root) as db:
        # The daily row, idea uniqueness and active-job check share a write transaction.
        db.commit()
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT stopped FROM control').fetchone()[0]:
            return 'stopped', None
        if db.execute('SELECT 1 FROM jobs WHERE day=?', (day,)).fetchone():
            return 'daily_limit', None
        if db.execute("SELECT 1 FROM jobs WHERE state='running'").fetchone():
            return 'active_job', None
        used = {row[0] for row in db.execute('SELECT idea_hash FROM jobs')}
        premise = next((item for item in ideas if _hash(item) not in used), None)
        if premise is None or not clips:
            return 'inputs_needed', None
        recent = db.execute('SELECT voice,clip FROM jobs ORDER BY day DESC LIMIT 2').fetchall()
        voice = next((pair for pair in VOICES if ','.join(pair) not in {row['voice'] for row in recent}), VOICES[0])
        clip = next((item for item in clips if not recent or item.name != recent[0]['clip']), clips[0])
        db.execute("INSERT INTO jobs (day,state,idea_hash,voice,clip) VALUES (?,'running',?,?,?)",
                   (day,_hash(premise),','.join(voice),clip.name))
        return 'reserved', (premise,voice,clip)


def local_narration(text, path, voice, rate):
    executable = shutil.which('espeak-ng') or shutil.which('espeak')
    if not executable:
        raise RuntimeError('Install eSpeak NG on the production worker')
    subprocess.run([executable, '-v', voice, '-s', str(145+rate), '-w', str(path), '--stdin'],
                   input=text, encoding='utf-8', stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, check=True, timeout=30)


def _check_story(story):
    lines = story['lines']
    text = [_hash(line['text']) for line in lines]
    if len(lines) < 6 or len(set(text)) != len(text) or len({line['speaker'] for line in lines}) < 2:
        raise ValueError('Script needs unique dialogue and distinct speakers')
    return _hash(' '.join(line['text'] for line in lines))


def _check_export(result):
    report = analyze(Path(result['path']))
    dimensions = report['width'],report['height']
    if not report['passed'] or dimensions != (1080,1920) or abs(report['fps']-30) > .1:
        raise ValueError('Technical check rejected the video')
    if not 30 <= report['duration_seconds'] <= 60:
        raise ValueError('Video must be 30–60 seconds')
    srt = Path(result['captions_path'])
    cues = read_captions(srt) if srt.is_file() else []
    if (not cues or cues[-1]['end'] > report['duration_seconds'] + .10
        or any(current['start'] < previous['end'] - .002 for previous,current in zip(cues,cues[1:]))):
        raise ValueError('Timed captions are missing')
    return report


def run_day(root, ideas, media, timezone='UTC', *, day=None, narrator=local_narration):
    root,media = Path(root),Path(media)
    if day is None:
        day = datetime.now(ZoneInfo(timezone)).date().isoformat()
    day = date.fromisoformat(day).isoformat()
    clips = sorted(path for path in media.iterdir() if path.is_file() and not path.is_symlink()
                   and path.suffix.lower() in MEDIA_TYPES and path.stat().st_size <= 512*1024*1024)
    if not isinstance(ideas,list) or not all(isinstance(item,str) and 3 <= len(item.strip()) <= 350 for item in ideas):
        raise ValueError('Supply a JSON list of original story premises, 3–350 characters each')
    state, work = reserve(root,day,ideas,clips)
    if work is None:
        return state
    premise, voices, clip = work
    output = root/'renders'
    output.mkdir(exist_ok=True)

    def progress(percent, message):
        with ledger(root) as db:
            if db.execute('SELECT stopped FROM control').fetchone()[0]:
                raise Stopped()
            db.execute('UPDATE jobs SET progress=?,message=? WHERE day=?', (min(percent,95),message,day))

    try:
        for attempt in range(1,ATTEMPTS+1):
            with ledger(root) as db:
                db.execute('UPDATE jobs SET attempts=? WHERE day=?', (attempt,day))
            try:
                progress(1,'Preparing original story')
                script = make_story(premise, target=60 if attempt==2 else 45)
                fingerprint = _check_story(script)
                with ledger(root) as db:
                    if db.execute('SELECT 1 FROM jobs WHERE fingerprint=? AND day<>?',(fingerprint,day)).fetchone():
                        raise ValueError('Generated script is a duplicate')
                with tempfile.TemporaryDirectory(prefix='working-',dir=output) as folder:
                    result = generate(script,folder,background=clip,resolution='1080p',
                                      voice=voices[0],second_voice=voices[1],voice_factory=narrator,
                                      progress=progress,target_seconds=45)
                    progress(96,'Checking export')
                    report = _check_export(result)
                    final = output/day
                    staged = Path(folder)/'verified'
                    staged.mkdir()
                    for key,name in (('path','short.mp4'),('captions_path','captions.srt')):
                        Path(result[key]).replace(staged/name)
                    (staged/'story.json').write_text(json.dumps(script,ensure_ascii=False,indent=2),encoding='utf-8')
                    with ledger(root) as db:
                        db.commit()
                        db.execute('BEGIN IMMEDIATE')
                        if db.execute('SELECT stopped FROM control').fetchone()[0]:
                            raise Stopped()
                        if final.exists():
                            raise ValueError('The daily output already exists. Review the existing files.')
                        staged.rename(final)
                        db.execute("""UPDATE jobs SET state='ready',title=?,progress=100,message='Ready for review',
                                   fingerprint=?,duration=?,artifact=? WHERE day=?""",
                                   (script['title'],fingerprint,report['duration_seconds'],f'renders/{day}/short.mp4',day))
                    return 'ready'
            except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
                if attempt == ATTEMPTS or (output/day).exists():
                    reason = str(exc) if isinstance(exc,ValueError) else 'Render or narration failed. Check FFmpeg, eSpeak and footage.'
                    with ledger(root) as db:
                        db.execute("UPDATE jobs SET state='failed',message=? WHERE day=?", (reason,day))
                    return 'failed'
    except Stopped:
        with ledger(root) as db:
            db.execute("UPDATE jobs SET state='stopped',message='Emergency stop requested' WHERE day=?", (day,))
        return 'stopped'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('run','status','stop','resume'))
    parser.add_argument('--state',required=True,type=Path)
    parser.add_argument('--ideas',type=Path)
    parser.add_argument('--media',type=Path)
    parser.add_argument('--timezone',default='UTC')
    args = parser.parse_args()
    if args.command == 'run':
        if args.ideas is None or args.media is None:
            parser.error('run requires --ideas and --media')
        result = run_day(args.state,json.loads(args.ideas.read_text(encoding='utf-8')),args.media,args.timezone)
        print(result)
        return 1 if result=='failed' else 0
    if args.command in ('stop','resume'):
        set_stop(args.state,args.command=='stop')
    print(json.dumps(status(args.state),ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
