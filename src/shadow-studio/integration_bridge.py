"""Owner-only automation bridge; no external AI service is granted access by default.

Configure SHADOW_AGENT_TOKEN in .env and connect through a private network.
Keep the token private; it grants read access to project metadata and ability to stage drafts.
"""
import hmac
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix='/api/agent', tags=['private-automation'])
ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT / 'outputs'
DRAFT_DIR = ROOT / 'publishing_drafts'
DRAFT_DIR.mkdir(exist_ok=True)
ID_RE = re.compile(r'[0-9a-f]{12}\Z')

def authorize(x_shadow_agent_token: str | None = Header(default=None)):
    secret = os.environ.get('SHADOW_AGENT_TOKEN', '')
    if len(secret) < 32:
        raise HTTPException(503, 'Automation disabled: set a 32+ character SHADOW_AGENT_TOKEN in .env')
    if not x_shadow_agent_token or not hmac.compare_digest(secret, x_shadow_agent_token):
        raise HTTPException(401, 'Invalid agent token')
    return True

def record(jobid: str):
    if not ID_RE.fullmatch(jobid):
        raise HTTPException(400, 'Invalid video identifier')
    path = OUTPUTS / jobid / 'metadata.json'
    video = OUTPUTS / jobid / 'short.mp4'
    if not path.is_file() or not video.is_file():
        raise HTTPException(404, 'Completed video not found')
    try:
        item = json.loads(path.read_text(encoding='utf-8'))
        if item.get('id') != jobid:
            raise ValueError('Identifier mismatch')
    except (ValueError, OSError, json.JSONDecodeError):
        raise HTTPException(422, 'Invalid video record')
    return item, video

class DraftRequest(BaseModel):
    video_id: str = Field(min_length=12, max_length=12)
    title: str = Field(min_length=3, max_length=100)
    description: str = Field(default='', max_length=5000)
    privacy: str = Field(default='private')

@router.get('/status', dependencies=[Depends(authorize)])
def status():
    return {'connected': True, 'scope': 'local automation + publishing drafts',
            'youtube_connected': False, 'public_publishing_enabled': False,
            'instructions': 'No video is uploaded or published by this bridge.'}

@router.get('/videos', dependencies=[Depends(authorize)])
def videos():
    items=[]
    for path in OUTPUTS.glob('*/metadata.json'):
        try:
            jid=path.parent.name
            data, _ = record(jid)
            items.append({'id':jid,'title':data.get('title',''),
                          'review_status':data.get('review_status','needs_review'),
                          'created_at':data.get('created_at',''),
                          'description':data.get('description','')})
        except (HTTPException, OSError, ValueError):
            continue
    return {'videos':sorted(items,key=lambda x:x['created_at'],reverse=True)[:100]}

@router.post('/drafts', status_code=201, dependencies=[Depends(authorize)])
def create_draft(req: DraftRequest):
    if req.privacy not in ('private','unlisted','public'):
        raise HTTPException(400, 'Invalid visibility')
    data, movie = record(req.video_id)
    from approval_security import approved_unchanged
    from quality_check import analyze
    if not approved_unchanged(data, movie):
        raise HTTPException(409, 'Video has not been approved or has changed since approval')
    if not analyze(movie)['passed']:
        raise HTTPException(409, 'Video failed the current technical quality checks')
    # This is a publishing plan ONLY; never an upload or a scheduled task.
    draftid=uuid.uuid4().hex[:12]
    draft={'id':draftid,'video_id':req.video_id,'title':req.title,
           'description':req.description,'desired_privacy':req.privacy,
           'status':'local_draft','created_at':datetime.now(timezone.utc).isoformat(),
           'ready_for_upload':True,'published':False,
           'instructions':'Review in Shadow Studio; then upload through YouTube Studio or authorized vidIQ.'}
    path=DRAFT_DIR / (draftid+'.json')
    path.write_text(json.dumps(draft,indent=2,ensure_ascii=False),encoding='utf-8')
    return draft

@router.get('/drafts', dependencies=[Depends(authorize)])
def list_drafts():
    drafts=[]
    for path in sorted(DRAFT_DIR.glob('*.json'),reverse=True):
        try:drafts.append(json.loads(path.read_text(encoding='utf-8')))
        except (ValueError,OSError):pass
    return {'drafts':drafts[:100]}

@router.get('/drafts/{draftid}', dependencies=[Depends(authorize)])
def get_draft(draftid:str):
    if not ID_RE.fullmatch(draftid):
        raise HTTPException(400, 'Invalid draft ID')
    path=DRAFT_DIR/(draftid+'.json')
    if not path.is_file():raise HTTPException(404, 'Draft not found')
    return json.loads(path.read_text(encoding='utf-8'))
