"""Explicit, private-only YouTube handoff of a reviewed production artifact."""
import argparse
from datetime import date
import hashlib
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

import requests

from production_worker import ledger

CREATE = 'https://www.googleapis.com/upload/youtube/v3/videos'
MAX_BYTES = 50 * 1024 * 1024


def upload(root, day, approved_sha256, token, *, confirmed=False, for_kids=None, http=requests):
    if not confirmed or not token or type(for_kids) is not bool:
        raise ValueError('Private upload requires explicit approval, an authorized OAuth token, and an audience decision')
    day = date.fromisoformat(day).isoformat()
    root = Path(root)
    with ledger(root) as db:
        row = db.execute("SELECT title,artifact FROM jobs WHERE day=? AND state='ready'",(day,)).fetchone()
    if row is None or row['artifact'] != f'renders/{day}/short.mp4':
        raise ValueError('Only a verified daily export can be uploaded')
    path = root/row['artifact']
    with path.open('rb') as video:
        contents = video.read(MAX_BYTES+1)
    if not contents or len(contents)>MAX_BYTES:
        raise ValueError('Initial private uploads must use a verified video up to 50 MiB')
    digest = hashlib.sha256(contents).hexdigest()
    if digest != approved_sha256.lower():
        raise ValueError('The MP4 differs from the approved SHA-256')
    with ledger(root) as db:
        db.commit()
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT stopped FROM control').fetchone()[0]:
            raise ValueError('Production is stopped')
        try:
            db.execute("INSERT INTO private_uploads(day,sha256,state) VALUES (?,?,'submitting')",(day,digest))
        except sqlite3.IntegrityError:
            raise ValueError('This export has an existing upload record. Check YouTube Studio before taking further action.') from None

    auth = {'Authorization': 'Bearer '+token}
    try:
        initialized = http.post(CREATE, params={'uploadType':'resumable','part':'snippet,status'},
            headers={**auth,'X-Upload-Content-Type':'video/mp4','X-Upload-Content-Length':str(len(contents))},
            json={'snippet':{'title':row['title']},'status':{'privacyStatus':'private','selfDeclaredMadeForKids':for_kids}},
            timeout=(8,30),allow_redirects=False)
        location = initialized.headers.get('Location','')
        parsed = urlsplit(location)
        if (initialized.status_code != 200 or parsed.scheme != 'https'
            or parsed.hostname not in {'www.googleapis.com','youtube.googleapis.com'}
            or parsed.port not in (None,443) or parsed.username or parsed.password or parsed.fragment
            or parsed.path != '/upload/youtube/v3/videos' or not parsed.query):
            raise RuntimeError('Unexpected response when starting the private upload')
        with ledger(root) as db:
            if db.execute('SELECT stopped FROM control').fetchone()[0]:
                raise RuntimeError('Production stopped before media transfer')
        uploaded = http.put(location,headers={**auth,'Content-Type':'video/mp4'},data=contents,
                            timeout=(8,120),allow_redirects=False)
        if uploaded.status_code not in (200,201):
            raise RuntimeError('Video transfer was not confirmed')
        response = uploaded.json()
        video_id = str(response.get('id',''))
        if (not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id)
            or response.get('status',{}).get('privacyStatus') != 'private'):
            raise RuntimeError('YouTube did not confirm the video ID and private visibility')
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError, requests.RequestException):
        # An ambiguous result may already exist remotely. Never automatically issue a second insert.
        with ledger(root) as db:
            db.execute("UPDATE private_uploads SET state='needs_reconciliation' WHERE day=?",(day,))
        raise RuntimeError('Private upload was not confirmed. Check YouTube Studio before retrying.') from None
    with ledger(root) as db:
        db.execute("UPDATE private_uploads SET state='private',video_id=? WHERE day=?",(video_id,day))
    return video_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--day',required=True)
    parser.add_argument('--sha256',required=True)
    parser.add_argument('--confirm-private',action='store_true')
    parser.add_argument('--audience',choices=('made-for-kids','not-made-for-kids'),required=True)
    args = parser.parse_args()
    try:
        result = upload(args.state,args.day,args.sha256,os.getenv('SHADOW_YOUTUBE_ACCESS_TOKEN'),
                        confirmed=args.confirm_private,for_kids=args.audience=='made-for-kids')
    except (OSError, ValueError, RuntimeError) as exc:
        print('Private upload blocked:', exc)
        return 1
    print('Private video confirmed:',result)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
