"""Optional private object-storage upload for approved Shorts.

Only activates with explicit SHADOW_HANDOFF_AUTO_UPLOAD=true OR the owner clicking
Create private share link. Uses a private S3-compatible bucket and signed HTTPS GET
links; it does not grant ChatGPT remote machine access or publish to YouTube.
"""
import json
import os
import re
import threading
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'outputs'
ID_PATTERN=re.compile(r'[a-f0-9]{12}\Z')
_LOCK=threading.RLock()
_ACTIVE={}
_LINKS={}

class HandoffError(Exception): pass

def config():
    bucket=os.getenv('SHADOW_S3_BUCKET','').strip()
    region=os.getenv('SHADOW_S3_REGION','us-east-1').strip()
    endpoint=os.getenv('SHADOW_S3_ENDPOINT','').strip()
    access=os.getenv('SHADOW_S3_ACCESS_KEY','').strip()
    secret=os.getenv('SHADOW_S3_SECRET_KEY','').strip()
    missing = [name for name, value in (
        ('SHADOW_S3_BUCKET', bucket), ('SHADOW_S3_ACCESS_KEY', access),
        ('SHADOW_S3_SECRET_KEY', secret)) if not value]
    enabled = not missing
    if endpoint:
        parts=urlparse(endpoint)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise HandoffError('S3 endpoint must be an HTTPS server URL without credentials or query')
    return {'enabled':enabled,'missing':missing,'bucket':bucket,'region':region,'endpoint':endpoint,
            'auto_upload':os.getenv('SHADOW_HANDOFF_AUTO_UPLOAD','false').strip().lower()=='true'}

def _client(c):
    try: import boto3
    except ImportError: raise HandoffError('Install dependencies with pip install -r requirements.txt')
    from botocore.config import Config
    kwargs={'config':Config(connect_timeout=8, read_timeout=20, retries={'total_max_attempts':2}), 'service_name':'s3','region_name':c['region'],
            'aws_access_key_id':os.getenv('SHADOW_S3_ACCESS_KEY'),
            'aws_secret_access_key':os.getenv('SHADOW_S3_SECRET_KEY')}
    if c['endpoint']: kwargs['endpoint_url']=c['endpoint']
    return boto3.client(**kwargs)

def _provider_error(error):
    response = getattr(error, 'response', {})
    code = response.get('Error', {}).get('Code', '') if isinstance(response, dict) else ''
    if code in {'403', 'AccessDenied', 'Forbidden', 'InvalidAccessKeyId', 'SignatureDoesNotMatch'}:
        return 'Storage rejected access. Check the endpoint, bucket permissions, and local credentials.'
    if code in {'404', 'NoSuchBucket', 'NotFound'}:
        return 'Storage could not find the bucket. Check its name and endpoint.'
    return 'Storage request failed. Check the endpoint and network, then retry or download the publishing kit.'


def test_connection():
    settings = config()
    if not settings['enabled']:
        raise HandoffError('Set these fields in your local .env: ' + ', '.join(settings['missing']))
    try:
        _client(settings).head_bucket(Bucket=settings['bucket'])
    except Exception as exc:
        raise HandoffError(_provider_error(exc)) from exc
    return {'status': 'reachable', 'message': 'Bucket access confirmed. This check made no uploads. Upload permissions are checked during transfer.'}


def _record(jobid):
    if not ID_PATTERN.fullmatch(jobid):raise HandoffError('Invalid video ID')
    parent=OUT/jobid
    p=parent/'metadata.json'; vid=parent/'short.mp4'
    if not p.is_file() or not vid.is_file():raise HandoffError('Completed video not found')
    try:
        data=json.loads(p.read_text(encoding='utf8'))
    except (OSError, ValueError) as exc:
        raise HandoffError('Video review information could not be read') from exc
    from approval_security import approved_unchanged
    if data.get('id')!=jobid or not approved_unchanged(data, vid):
        raise HandoffError('Video has not been approved or has changed since approval; approve it again')
    # Validate CURRENT approval / quality, not a stale stored result.
    from quality_check import analyze
    report=analyze(vid)
    if not report.get('passed'):raise HandoffError('Video failed the current quality check')
    if vid.stat().st_size > 299*1024*1024:
        raise HandoffError('Video exceeds vidIQ public URL import limit (299 MB)')
    return data,vid

def state(jobid):
    if not ID_PATTERN.fullmatch(jobid):raise HandoffError('Invalid video ID')
    with _LOCK:
        if jobid in _ACTIVE:
            return dict(_ACTIVE[jobid])
        link=_LINKS.get(jobid)
        if not link or link[0] <= time.monotonic():
            _LINKS.pop(jobid,None)
            link = None
    if link:
        result = dict(link[1])
        if result['status'] == 'uploaded':
            from approval_security import approved_unchanged
            folder = OUT / jobid
            try:
                reviewed = json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
                current = (reviewed.get('approved_video_sha256') == result.pop('file_sha256')
                           and approved_unchanged(reviewed, folder/'short.mp4'))
            except (OSError, ValueError):
                current = False
            if not current:
                return {'status':'failed','error':'Video approval changed. Review and approve the current file.'}
        return result
    p=OUT/jobid/'cloud_handoff.json'
    if not p.is_file():return {'status':'not_uploaded'}
    try:
        d=json.loads(p.read_text(encoding='utf8'))
        if d.get('status') in {'queued','checking','uploading'}:
            return {'status':'failed','error':'The app restarted during transfer. Retry to create a new link.'}
        if d.get('status')=='uploaded':
            return {'status':'link_unavailable','message':'Create a new link or download the publishing kit.'}
        return {k:d[k] for k in ('status','uploaded_at','file_sha256','error') if k in d}
    except Exception:return {'status':'unknown'}

def _write_status(jobid,data):
    p=OUT/jobid/'cloud_handoff.json'
    temp=p.with_suffix('.tmp')
    temp.write_text(json.dumps(data,indent=2),encoding='utf8'); temp.replace(p)

def start_upload(jobid):
    if not ID_PATTERN.fullmatch(jobid):
        raise HandoffError('Invalid video ID')
    settings = config()
    if not settings['enabled']:
        raise HandoffError('Private storage is unconfigured. Use Download publishing kit, or set up storage in Settings.')
    with _LOCK:
        if jobid in _ACTIVE:
            return dict(_ACTIVE[jobid])
        _LINKS.pop(jobid,None)
        status = {'status':'queued', 'bytes_sent':0, 'bytes_total':0}
        _ACTIVE[jobid] = status
        threading.Thread(target=_upload, args=(jobid, settings), daemon=True).start()
        return dict(status)


def _upload(jobid, settings):
    try:
        with _LOCK:
            _ACTIVE[jobid]['status'] = 'checking'
        data, video = _record(jobid)
        expected = data['approved_video_sha256']
        with tempfile.TemporaryDirectory(prefix='shadow_handoff_') as folder:
            snapshot = Path(folder)/'short.mp4'
            shutil.copyfile(video, snapshot)
            from approval_security import file_digest, approved_unchanged
            if file_digest(snapshot) != expected:
                raise HandoffError('Video changed while preparing upload. Review and approve the current file.')
            key = f'shadow-studio/{jobid}/{expected[:16]}.mp4'
            client = _client(settings)
            with _LOCK:
                _ACTIVE[jobid].update(status='uploading', bytes_total=snapshot.stat().st_size)
            _write_status(jobid, {'status':'uploading'})

            def progress(count):
                with _LOCK:
                    job = _ACTIVE[jobid]
                    job['bytes_sent'] = min(job['bytes_total'],job['bytes_sent']+count)

            client.upload_file(str(snapshot), settings['bucket'], key, Callback=progress,
                               ExtraArgs={'ContentType':'video/mp4','CacheControl':'private, no-store'})
            reviewed = json.loads((video.parent/'metadata.json').read_text(encoding='utf-8'))
            if reviewed.get('approved_video_sha256') != expected or not approved_unchanged(reviewed, video):
                raise HandoffError('Local approval changed during upload. No link was issued. Review the current video before retrying.')
            expires = 86400
            url = client.generate_presigned_url('get_object', Params={
                'Bucket':settings['bucket'], 'Key':key, 'ResponseContentType':'video/mp4'}, ExpiresIn=expires)
            if not url.startswith('https://'):
                raise HandoffError('Storage did not return a secure HTTPS link')
            permanent = {'status':'uploaded', 'uploaded_at':datetime.now(timezone.utc).isoformat(),
                         'file_sha256':expected,'object_key':key}
            _write_status(jobid, permanent)
            request = (
                'If the vidIQ connector is available, import this video and prepare a YouTube Shorts draft '
                'for ShadowChronicles. Show the draft title, description and visibility for my explicit approval '
                f'before publishing.\n\nVideo URL: {url}\n\nTitle: {reviewed.get("title", "")}'
                f'\n\nDescription:\n{reviewed.get("description", "")}'
            )
            with _LOCK:
                # Keep signed links only in this app session. Persist status and digest on disk.
                now = time.monotonic()
                for old_id in [key for key, value in _LINKS.items() if value[0] <= now]:
                    del _LINKS[old_id]
                _LINKS[jobid] = (now+expires, {'status':'uploaded', 'url':url, 'video_id':jobid,
                                              'expires_in_seconds':expires, 'request_for_chatgpt':request,
                                              'file_sha256':expected})
    except Exception as exc:
        message = str(exc) if isinstance(exc,HandoffError) else _provider_error(exc)
        try:
            if (OUT/jobid/'metadata.json').is_file():
                _write_status(jobid, {'status':'failed','error':message})
        finally:
            with _LOCK:
                _LINKS[jobid] = (time.monotonic()+3600, {'status':'failed','error':message})
    finally:
        with _LOCK:
            _ACTIVE.pop(jobid,None)


def auto_upload(jobid):
    try:
        start_upload(jobid)
    except HandoffError:
        pass
