"""Explicit, private-only YouTube handoff of a reviewed production artifact."""
import argparse
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests

from production_worker import ledger

CREATE = 'https://www.googleapis.com/upload/youtube/v3/videos'
CHANNELS = 'https://www.googleapis.com/youtube/v3/channels'
TOKEN = 'https://oauth2.googleapis.com/token'
MAX_BYTES = 50 * 1024 * 1024


class RetryableUpload(RuntimeError):
    """No media transfer started, so another session is safe."""


def refresh_access_token(env, http=requests):
    fields = {key: env.get('SHADOW_YOUTUBE_' + key.upper(), '') for key in
              ('client_id', 'client_secret', 'refresh_token')}
    if not all(fields.values()):
        raise ValueError('Set the YouTube OAuth client ID, client secret and refresh token on the worker')
    try:
        response = http.post(TOKEN, data={**fields, 'grant_type': 'refresh_token'},
                             timeout=(8, 30), allow_redirects=False)
        if response.status_code != 200:
            raise RuntimeError()
        body = response.json()
        token = body.get('access_token')
        if body.get('token_type', '').lower() != 'bearer' or not isinstance(token, str) or not token:
            raise RuntimeError()
    except (requests.RequestException, OSError, RuntimeError, ValueError, TypeError, AttributeError):
        raise RetryableUpload('YouTube OAuth refresh failed. Check the worker credentials and Google authorization.') from None
    return token


def verify_channel(token, channel_id, http):
    if not isinstance(channel_id, str) or not re.fullmatch(r'UC[A-Za-z0-9_-]{22}', channel_id):
        raise ValueError('Configure the exact ShadowChronicles YouTube channel ID')
    try:
        response = http.get(CHANNELS, params={'part': 'id', 'mine': 'true'},
                            headers={'Authorization': 'Bearer ' + token},
                            timeout=(8, 30), allow_redirects=False)
        if response.status_code != 200:
            raise RuntimeError()
        body = response.json()
        ids = [item['id'] for item in body.get('items', [])]
    except (requests.RequestException, OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError):
        raise RetryableUpload('Unable to verify the YouTube channel. Check authorization and its read-only scope.') from None
    if ids != [channel_id] or body.get('nextPageToken'):
        raise ValueError('The OAuth connection must resolve to the configured ShadowChronicles channel only')


def upload(root, day, approved_sha256, token, *, channel_id, confirmed=False, for_kids=None, http=requests):
    if not confirmed or not token or type(for_kids) is not bool:
        raise ValueError('Private upload requires explicit approval, an authorized OAuth token, and an audience decision')
    day = date.fromisoformat(day).isoformat()
    root = Path(root)
    with ledger(root) as db:
        row = db.execute("SELECT title,artifact FROM jobs WHERE day=? AND state='ready'",(day,)).fetchone()
    if row is None or row['artifact'] != f'renders/{day}/short.mp4':
        raise ValueError('Only a verified daily export can be uploaded')
    path = root/row['artifact']
    if path.resolve() != root.resolve()/row['artifact']:
        raise ValueError('The verified export location changed')
    with path.open('rb') as video:
        contents = video.read(MAX_BYTES+1)
    if not contents or len(contents)>MAX_BYTES:
        raise ValueError('Initial private uploads must use a verified video up to 50 MiB')
    digest = hashlib.sha256(contents).hexdigest()
    if digest != approved_sha256.lower():
        raise ValueError('The MP4 differs from the approved SHA-256')
    with ledger(root) as db:
        existing = db.execute('SELECT state FROM private_uploads WHERE day=? OR sha256=?',(day,digest)).fetchone()
        if existing and existing['state'] != 'retryable':
            raise ValueError('This export has an existing upload record. Check YouTube Studio before taking further action.')
        if db.execute('SELECT stopped FROM control').fetchone()[0]:
            raise ValueError('Production is stopped')
    verify_channel(token, channel_id, http)

    with ledger(root) as db:
        db.commit()
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT stopped FROM control').fetchone()[0]:
            raise ValueError('Production is stopped')
        try:
            claim = db.execute("""INSERT INTO private_uploads(day,sha256,state) VALUES (?,?,'submitting')
                               ON CONFLICT(day) DO UPDATE SET state='submitting'
                               WHERE private_uploads.state='retryable' AND private_uploads.sha256=excluded.sha256""",
                               (day,digest))
            if claim.rowcount != 1:
                raise sqlite3.IntegrityError()
        except sqlite3.IntegrityError:
            raise ValueError('This export has an existing upload record. Check YouTube Studio before taking further action.') from None

    auth = {'Authorization': 'Bearer '+token}
    media_started = False
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
        media_started = True
        uploaded = http.put(location,headers={**auth,'Content-Type':'video/mp4'},data=contents,
                            timeout=(8,120),allow_redirects=False)
        if uploaded.status_code not in (200,201):
            raise RuntimeError('Video transfer was not confirmed')
        response = uploaded.json()
        video_id = str(response.get('id',''))
        if (not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id)
            or response.get('status',{}).get('privacyStatus') != 'private'
            or response.get('snippet',{}).get('channelId') != channel_id):
            raise RuntimeError('YouTube did not confirm the video ID, target channel and private visibility')
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError, requests.RequestException):
        # An ambiguous result may already exist remotely. Never automatically issue a second insert.
        with ledger(root) as db:
            state = 'needs_reconciliation' if media_started else 'retryable'
            db.execute('UPDATE private_uploads SET state=? WHERE day=?',(state,day))
        if not media_started:
            raise RetryableUpload('Private upload stopped before media transfer and can be retried safely.') from None
        raise RuntimeError('Private upload was not confirmed. Check YouTube Studio before retrying.') from None
    with ledger(root) as db:
        db.execute("UPDATE private_uploads SET state='private',video_id=? WHERE day=?",(video_id,day))
    return video_id


def upload_daily(root, day, env, http=requests):
    if env.get('SHADOW_YOUTUBE_PRIVATE_AUTOMATION') != '1':
        raise ValueError('Private upload automation is disabled')
    audience = env.get('SHADOW_YOUTUBE_AUDIENCE')
    if audience not in ('made-for-kids', 'not-made-for-kids'):
        raise ValueError('Configure the audience for private automation')
    with ledger(root) as db:
        if db.execute('SELECT stopped FROM control').fetchone()[0]:
            return 'stopped'
        row = db.execute("""SELECT verified_exports.sha256, private_uploads.state AS upload_state
                           FROM jobs JOIN verified_exports USING (day)
                           LEFT JOIN private_uploads USING (day) WHERE day=? AND jobs.state='ready'""",
                         (day,)).fetchone()
    if row is None:
        return 'no_verified_export_today'
    if row['upload_state'] not in (None, 'retryable'):
        return row['upload_state']
    token = refresh_access_token(env, http)
    return upload(root,day,row['sha256'],token,channel_id=env.get('SHADOW_YOUTUBE_CHANNEL_ID'),
                  confirmed=True,for_kids=audience=='made-for-kids',http=http)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--day')
    parser.add_argument('--sha256')
    parser.add_argument('--confirm-private',action='store_true')
    parser.add_argument('--audience',choices=('made-for-kids','not-made-for-kids'))
    parser.add_argument('--daily',action='store_true')
    parser.add_argument('--timezone',default='UTC')
    args = parser.parse_args()
    try:
        if args.daily:
            if args.day or args.sha256 or args.confirm_private or args.audience:
                parser.error('--daily cannot be combined with single-video approval options')
            day = datetime.now(ZoneInfo(args.timezone)).date().isoformat()
            env = os.environ
            if directory := env.get('CREDENTIALS_DIRECTORY'):
                try:
                    env = json.loads((Path(directory)/'youtube.json').read_text(encoding='utf-8'))
                    if not isinstance(env, dict) or not all(isinstance(value,str) for value in env.values()):
                        raise ValueError()
                except (OSError, ValueError):
                    raise ValueError('The YouTube worker credential file is missing or invalid') from None
            result = upload_daily(args.state,day,env)
        else:
            if not (args.day and args.sha256 and args.confirm_private and args.audience):
                parser.error('supply --day, --sha256, --confirm-private and --audience')
            token = os.getenv('SHADOW_YOUTUBE_ACCESS_TOKEN') or refresh_access_token(os.environ)
            result = upload(args.state,args.day,args.sha256,token,channel_id=os.getenv('SHADOW_YOUTUBE_CHANNEL_ID'),
                            confirmed=True,for_kids=args.audience=='made-for-kids')
    except RetryableUpload as exc:
        print('Private upload blocked:', exc)
        return 75
    except (OSError, ValueError, RuntimeError) as exc:
        print('Private upload blocked:', exc)
        return 1
    print('Private upload result:',result)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
