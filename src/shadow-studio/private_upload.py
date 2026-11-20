"""Explicit, private-only YouTube handoff of a reviewed production artifact."""
import argparse
from datetime import date, datetime, timezone as utc_timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
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


def transfer(http, location, auth, contents, check_stop):
    headers = {**auth, 'Content-Type': 'video/mp4'}
    try:
        uploaded = http.put(location, headers=headers, data=contents,
                            timeout=(8,120), allow_redirects=False)
        if uploaded.status_code in (200,201):
            return uploaded
        if uploaded.status_code != 308 and not 500 <= uploaded.status_code < 600:
            raise RuntimeError('Video transfer was not confirmed')
    except (OSError, requests.RequestException):
        pass
    total = len(contents)
    state = http.put(location, headers={**auth,'Content-Length':'0','Content-Range':f'bytes */{total}'},
                     data=b'', timeout=(8,30), allow_redirects=False)
    if state.status_code in (200,201):
        return state
    if state.status_code != 308:
        raise RuntimeError('Unable to verify the existing upload session')
    received = state.headers.get('Range','')
    match = re.fullmatch(r'bytes=0-([0-9]{1,9})',received)
    if received and not match:
        raise RuntimeError('Unexpected upload byte range')
    start = int(match[1])+1 if match else 0
    if start >= total:
        raise RuntimeError('Unexpected upload byte range')
    check_stop()
    remaining = contents[start:]
    uploaded = http.put(location, headers={**headers,'Content-Length':str(len(remaining)),
                                          'Content-Range':f'bytes {start}-{total-1}/{total}'},
                        data=remaining, timeout=(8,120), allow_redirects=False)
    if uploaded.status_code not in (200,201):
        raise RuntimeError('Resumed video transfer was not confirmed')
    return uploaded


def upload(root, day, approved_sha256, token, *, channel_id, confirmed=False, for_kids=None, http=requests):
    require_approval(confirmed, token, for_kids)
    day = date.fromisoformat(day).isoformat()
    root = Path(root)
    with ledger(root) as db:
        row = db.execute("SELECT title,artifact FROM jobs WHERE day=? AND state='ready'",(day,)).fetchone()
    if row is None or row['artifact'] != f'renders/{day}/short.mp4':
        raise ValueError('Only a verified daily export can be uploaded')
    path = root/row['artifact']
    if path.resolve() != root.resolve()/row['artifact']:
        raise ValueError('The verified export location changed')
    return upload_verified(root, day, path, row['title'], approved_sha256, token,
                           channel_id=channel_id, for_kids=for_kids, http=http)


def require_approval(confirmed, token, for_kids):
    if not confirmed or not token or type(for_kids) is not bool:
        raise ValueError('Private upload requires explicit approval, an authorized OAuth token, and an audience decision')


def upload_clip(root, projects, clips, clip_id, approved_sha256, token, *, channel_id,
                confirmed=False, for_kids=None, timezone='UTC', http=requests):
    """Upload one explicitly approved local clip through the shared daily slot."""
    from quality_check import analyze
    from streamer_clips import clip_review

    require_approval(confirmed, token, for_kids)
    if not re.fullmatch(r'[a-f0-9]{12}', clip_id):
        raise ValueError('Invalid clip render ID')
    database = Path(projects)/'streamer-clips.sqlite3'
    path = Path(clips)/f'{clip_id}.mp4'
    if path.resolve() != Path(clips).resolve()/path.name:
        raise ValueError('The verified clip location changed')

    def check_approval():
        review = clip_review(database, clip_id)
        if (review is None or review['state'] != 'approved'
                or review['sha256'] != approved_sha256.lower()
                or review['license_scope'] not in ('edit-and-repost', 'commercial-edit-and-repost')):
            raise ValueError('Clip needs a matching approval and edit-and-repost rights')
        return review

    review = check_approval()
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('Clip is missing or exceeds the private worker limit')
    try:
        quality = analyze(path)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        raise ValueError('Clip could not be verified for private upload') from exc
    if not quality['passed'] or (quality['width'], quality['height']) != (1080, 1920):
        raise ValueError('Clip failed its current technical checks')
    clock = utc_timezone.utc if timezone == 'UTC' else ZoneInfo(timezone)
    day = datetime.now(clock).date().isoformat()
    creator = ' '.join(review['creator'].split())
    return upload_verified(root, day, path, f'{creator[:82]} clip #Shorts', approved_sha256, token,
                           channel_id=channel_id, for_kids=for_kids, http=http, clip_id=clip_id,
                           check_approval=check_approval)


def upload_verified(root, day, path, title, approved_sha256, token, *, channel_id, for_kids,
                    http, clip_id=None, check_approval=None):
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
            claim = db.execute("""INSERT INTO private_uploads(day,sha256,state,clip_id) VALUES (?,?,'submitting',?)
                               ON CONFLICT(day) DO UPDATE SET state='submitting'
                               WHERE private_uploads.state='retryable' AND private_uploads.sha256=excluded.sha256
                                 AND private_uploads.clip_id IS excluded.clip_id""",
                               (day,digest,clip_id))
            if claim.rowcount != 1:
                raise sqlite3.IntegrityError()
        except sqlite3.IntegrityError:
            raise ValueError('This export has an existing upload record. Check YouTube Studio before taking further action.') from None

    auth = {'Authorization': 'Bearer '+token}
    media_started = False
    def check_stop():
        with ledger(root) as db:
            if db.execute('SELECT stopped FROM control').fetchone()[0]:
                raise RuntimeError('Production stopped before media transfer')
        if check_approval:
            check_approval()

    try:
        initialized = http.post(CREATE, params={'uploadType':'resumable','part':'snippet,status'},
            headers={**auth,'X-Upload-Content-Type':'video/mp4','X-Upload-Content-Length':str(len(contents))},
            json={'snippet':{'title':title},'status':{'privacyStatus':'private','selfDeclaredMadeForKids':for_kids}},
            timeout=(8,30),allow_redirects=False)
        location = initialized.headers.get('Location','')
        parsed = urlsplit(location)
        if (initialized.status_code != 200 or parsed.scheme != 'https'
            or parsed.hostname not in {'www.googleapis.com','youtube.googleapis.com'}
            or parsed.port not in (None,443) or parsed.username or parsed.password or parsed.fragment
            or parsed.path != '/upload/youtube/v3/videos' or not parsed.query):
            raise RuntimeError('Unexpected response when starting the private upload')
        check_stop()
        media_started = True
        uploaded = transfer(http,location,auth,contents,check_stop)
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


def read_worker_settings(path):
    try:
        settings = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(settings, dict) and all(isinstance(value,str) for value in settings.values()):
            return settings
    except (OSError, ValueError):
        pass
    raise ValueError('The YouTube worker credential file is missing or invalid')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--day')
    parser.add_argument('--clip-id')
    parser.add_argument('--projects',type=Path)
    parser.add_argument('--clips',type=Path)
    parser.add_argument('--sha256')
    parser.add_argument('--confirm-private',action='store_true')
    parser.add_argument('--audience',choices=('made-for-kids','not-made-for-kids'))
    parser.add_argument('--daily',action='store_true')
    parser.add_argument('--credentials',type=Path)
    parser.add_argument('--timezone',default='UTC')
    args = parser.parse_args()
    try:
        config = args.credentials
        if config is None and (directory := os.getenv('CREDENTIALS_DIRECTORY')):
            config = Path(directory)/'youtube.json'
        env = os.environ if config is None else read_worker_settings(config)
        if args.daily:
            if args.day or args.sha256 or args.confirm_private or args.audience or args.clip_id or args.projects or args.clips:
                parser.error('--daily cannot be combined with single-video approval options')
            day = datetime.now(ZoneInfo(args.timezone)).date().isoformat()
            result = upload_daily(args.state,day,env)
        else:
            if not (args.sha256 and args.confirm_private and args.audience):
                parser.error('supply --sha256, --confirm-private and --audience')
            if args.clip_id:
                if args.day or not (args.projects and args.clips):
                    parser.error('clip uploads require --projects and --clips and use the current day')
            elif not args.day or args.projects or args.clips:
                parser.error('supply --day for a story upload or --clip-id for a clip')
            token = env.get('SHADOW_YOUTUBE_ACCESS_TOKEN') or refresh_access_token(env)
            options = dict(channel_id=env.get('SHADOW_YOUTUBE_CHANNEL_ID'),
                           confirmed=True, for_kids=args.audience=='made-for-kids')
            if args.clip_id:
                result = upload_clip(args.state,args.projects,args.clips,args.clip_id,args.sha256,token,
                                     timezone=args.timezone,**options)
            else:
                result = upload(args.state,args.day,args.sha256,token,**options)
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
