"""Optional private object-storage upload for approved Shorts.

Only activates with explicit SHADOW_HANDOFF_AUTO_UPLOAD=true OR the owner clicking
Create private share link. Uses a private S3-compatible bucket and signed HTTPS GET
links; it does not grant ChatGPT remote machine access or publish to YouTube.
"""
import json
import os
import re
import threading
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'outputs'
ID_PATTERN=re.compile(r'[a-f0-9]{12}\Z')
_LOCK=threading.Lock()

class HandoffError(Exception): pass

def config():
    bucket=os.getenv('SHADOW_S3_BUCKET','').strip()
    region=os.getenv('SHADOW_S3_REGION','us-east-1').strip()
    endpoint=os.getenv('SHADOW_S3_ENDPOINT','').strip()
    access=os.getenv('SHADOW_S3_ACCESS_KEY','').strip()
    secret=os.getenv('SHADOW_S3_SECRET_KEY','').strip()
    enabled=bool(bucket and access and secret)
    if endpoint:
        parts=urlparse(endpoint)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise HandoffError('S3 endpoint must be an HTTPS server URL without credentials or query')
    return {'enabled':enabled,'bucket':bucket,'region':region,'endpoint':endpoint,
            'auto_upload':os.getenv('SHADOW_HANDOFF_AUTO_UPLOAD','false').strip().lower()=='true'}

def _client(c):
    try: import boto3
    except ImportError: raise HandoffError('Install dependencies with pip install -r requirements.txt')
    kwargs={'service_name':'s3','region_name':c['region'],
            'aws_access_key_id':os.getenv('SHADOW_S3_ACCESS_KEY'),
            'aws_secret_access_key':os.getenv('SHADOW_S3_SECRET_KEY')}
    if c['endpoint']: kwargs['endpoint_url']=c['endpoint']
    return boto3.client(**kwargs)

def _record(jobid):
    if not ID_PATTERN.fullmatch(jobid):raise HandoffError('Invalid video ID')
    parent=OUT/jobid
    p=parent/'metadata.json'; vid=parent/'short.mp4'
    if not p.is_file() or not vid.is_file():raise HandoffError('Completed video not found')
    data=json.loads(p.read_text(encoding='utf8'))
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
    p=OUT/jobid/'cloud_handoff.json'
    if not p.is_file():return {'status':'not_uploaded'}
    try:
        d=json.loads(p.read_text(encoding='utf8'))
        # Never expose any saved link (we deliberately do not persist signed URLs)
        return {k:d.get(k) for k in ['status','uploaded_at','file_sha256','error'] if k in d}
    except Exception:return {'status':'unknown'}

def _write_status(jobid,data):
    p=OUT/jobid/'cloud_handoff.json'
    temp=p.with_suffix('.tmp')
    temp.write_text(json.dumps(data,indent=2),encoding='utf8'); temp.replace(p)

def upload(jobid):
    c=config()
    if not c['enabled']:raise HandoffError('Configure private S3-compatible storage in .env first')
    data,video=_record(jobid)
    # Lock blocks duplicate uploads while a request is running.
    with _LOCK:
        _write_status(jobid,{'status':'uploading'})
        try:
            from approval_security import file_digest
            digest=file_digest(video)
            key=f'shadow-studio/{jobid}/{digest[:16]}.mp4'
            client=_client(c)
            client.upload_file(str(video),c['bucket'],key, ExtraArgs={
                'ContentType':'video/mp4','CacheControl':'private, no-store'})
            # 7d max for AWS SigV4 presigned URLs, shorter by default.
            expires=3600*24
            url=client.generate_presigned_url('get_object',Params={
                'Bucket':c['bucket'],'Key':key,'ResponseContentType':'video/mp4'},ExpiresIn=expires)
            if not url.startswith('https://'):
                raise HandoffError('Storage did not return a secure HTTPS download link')
            _write_status(jobid,{'status':'uploaded','uploaded_at':datetime.now(timezone.utc).isoformat(),
                                  'file_sha256':digest,'object_key':key})
            request=(f'Import this approved Shadow Studio MP4 into vidIQ and create a YouTube Shorts '
                     f'publishing draft for ShadowChronicles. Do NOT publish until I explicitly '
                     f'approve the exact draft.\n\nVideo URL: {url}\n\n'
                     f'Title: {data.get("title", "")}\n\nDescription:\n{data.get("description", "")}\n\n'
                     'Shadow Studio approval is approval of the video ONLY, not public publication.')
            return {'status':'uploaded','video_id':jobid,'url':url,'expires_in_seconds':expires,
                    'request_for_chatgpt':request,'title':data.get('title',''),
                    'note':'Anyone with this temporary URL can view the video. Do not post the link publicly. '
                           'ChatGPT/vidIQ import is not automatic; submit the request in the chat.'}
        except Exception as exc:
            _write_status(jobid,{'status':'failed','error':str(exc)[:300]})
            if isinstance(exc,HandoffError):raise
            raise HandoffError('Secure cloud upload failed: '+str(exc)[:200]) from exc

def auto_upload(jobid):
    try:upload(jobid)
    except Exception:pass  # owner can inspect status and retry; no public publishing
