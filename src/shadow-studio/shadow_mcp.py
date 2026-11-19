"""Shadow Studio owner-only, read-only MCP bridge.

Run on 127.0.0.1:8766, separate from the unauthenticated editing dashboard.
For local tests, configure SHADOW_AGENT_TOKEN (32+ random characters). For a
ChatGPT remote integration, configure a REAL OAuth 2.1 provider and JWT validation.
The bridge does not upload or publish video, or make any changes to local records.
"""
from __future__ import annotations

import hmac
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'outputs'
ID = re.compile(r'[a-f0-9]{12}\Z')
SUPPORTED_PROTOCOL = '2025-06-18'
MCP_NAME = 'shadow-studio-private'

app = FastAPI(title='Shadow Studio private MCP connector', docs_url=None, redoc_url=None, openapi_url=None)


def _auth(authorization: str | None) -> None:
    mode = os.environ.get('SHADOW_MCP_AUTH_MODE', 'token').strip().lower()
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Bearer authentication required', headers={'WWW-Authenticate': 'Bearer'})
    token = authorization[7:]
    if mode == 'token':
        secret = os.environ.get('SHADOW_AGENT_TOKEN', '')
        if len(secret) < 32:
            raise HTTPException(503, 'Connector disabled until SHADOW_AGENT_TOKEN is configured')
        if not hmac.compare_digest(secret, token):
            raise HTTPException(401, 'Invalid token', headers={'WWW-Authenticate': 'Bearer'})
    elif mode == 'oauth':
        issuer = os.environ.get('SHADOW_MCP_OAUTH_ISSUER', '').rstrip('/')
        audience = os.environ.get('SHADOW_MCP_OAUTH_AUDIENCE', '')
        jwks_url = os.environ.get('SHADOW_MCP_OAUTH_JWKS_URL', '')
        if not issuer.startswith('https://') or not audience or not jwks_url.startswith('https://'):
            raise HTTPException(503, 'OAuth issuer, audience and JWKS URL must be configured')
        try:
            import jwt
            signing_key = jwt.PyJWKClient(jwks_url, timeout=5).get_signing_key_from_jwt(token)
            claims = jwt.decode(token, signing_key.key, algorithms=['RS256','ES256'],
                                issuer=issuer, audience=audience, options={'require':['exp','iss','aud']}, leeway=30)
            required = os.environ.get('SHADOW_MCP_REQUIRED_SCOPE', 'shadow:videos:read').strip()
            granted = set(str(claims.get('scope','')).split())
            if required and required not in granted:
                raise ValueError('Required read scope missing')
            # Prevent other authorized users of the same OAuth tenant from seeing your Shorts.
            expected_sub = os.environ.get('SHADOW_MCP_OWNER_SUB','').strip()
            if not expected_sub or claims.get('sub') != expected_sub:
                raise ValueError('Owner identity mismatch')
        except (ImportError, Exception):
            raise HTTPException(401, 'Invalid OAuth identity or access token', headers={'WWW-Authenticate':'Bearer'})
    else:
        raise HTTPException(503, 'Unknown authentication mode')


def _record(video_id: str) -> tuple[dict,Path]:
    if not ID.fullmatch(video_id):
        raise ValueError('Invalid video ID')
    base = OUT / video_id
    manifest, movie = base / 'metadata.json', base / 'short.mp4'
    if not manifest.is_file() or not movie.is_file():
        raise ValueError('Video not found')
    try:
        item = json.loads(manifest.read_text('utf-8'))
    except (ValueError, OSError):
        raise ValueError('Video metadata invalid')
    from approval_security import approved_unchanged
    if item.get('id') != video_id or not approved_unchanged(item, movie):
        raise ValueError('Video not approved, or the MP4 changed since approval')
    from quality_check import analyze
    report = analyze(movie)
    if not report.get('passed'):
        raise ValueError('Video failed the current quality check')
    return item, movie


def _list_videos() -> dict:
    items=[]
    for p in OUT.glob('*/metadata.json'):
        try:
            item, movie = _record(p.parent.name)
            items.append({'video_id':item['id'], 'title':item.get('title',''),
                          'description':item.get('description',''),
                          'created_at':item.get('created_at',''),
                          'size_bytes': movie.stat().st_size, 'ready_for_draft':True})
        except (ValueError, OSError):
            continue
    items.sort(key=lambda v: v['created_at'], reverse=True)
    return {'videos':items[:100], 'public_publishing_authorized':False}


def _details(video_id: str) -> dict:
    item, movie = _record(video_id)
    return {'video_id':video_id, 'title':item.get('title',''),
            'description':item.get('description',''),
            'size_bytes':movie.stat().st_size,
            'channel':'ShadowChronicles', 'video_review_approved':True,
            'publish_permission':False,
            'next_step':'Get temporary video link, import to vidIQ, create a draft, then request separate final approval.'}


def _cloud_link(video_id: str) -> dict:
    item, movie = _record(video_id)
    from cloud_handoff import config, _client, HandoffError
    config_data = config()
    if not config_data['enabled']:
        return {'ready':False, 'reason':'Set up private S3-compatible storage and upload the approved Short first.'}
    status_file = OUT / video_id / 'cloud_handoff.json'
    try:
        state = json.loads(status_file.read_text('utf-8'))
    except (OSError, ValueError):
        return {'ready':False, 'reason':'Approved video has not been uploaded. Enable auto-upload on approval or upload from the dashboard.'}
    # Verify file has not changed since cloud upload before issuing a fresh signed link.
    import hashlib
    with movie.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if state.get('status') != 'uploaded' or state.get('file_sha256') != digest:
        return {'ready':False, 'reason':'Cloud copy missing or outdated; upload it again from Shadow Studio.'}
    key = state.get('object_key','')
    if key != f'shadow-studio/{video_id}/{digest[:16]}.mp4':
        return {'ready':False, 'reason':'Cloud handoff record is invalid.'}
    try:
        url = _client(config_data).generate_presigned_url('get_object',Params={
            'Bucket':config_data['bucket'],'Key':key,'ResponseContentType':'video/mp4'},ExpiresIn=3600)
    except Exception:
        return {'ready':False, 'reason':'Could not sign the cloud video link; check private storage configuration.'}
    if not url.startswith('https://'):
        return {'ready':False, 'reason':'Private storage must return an HTTPS link.'}
    return {'ready':True, 'video_id':video_id, 'video_url':url, 'expires_in_seconds':3600,
            'title':item.get('title',''),'description':item.get('description',''),
            'create_draft_only':True, 'public_publishing_authorized':False,
            'instruction':'Import this MP4 with vidIQ; prepare a draft for ShadowChronicles. Ask for explicit approval before public publishing.'}


TOOLS = [
 {'name':'list_approved_videos','description':'List only approved Shorts that pass the current technical quality gate. Read-only.',
  'inputSchema':{'type':'object','properties':{},'additionalProperties':False}},
 {'name':'get_approved_video','description':'Retrieve metadata about a previously approved Short. This does not publish it.',
  'inputSchema':{'type':'object','properties':{'video_id':{'type':'string','pattern':'^[a-f0-9]{12}$'}},'required':['video_id'],'additionalProperties':False}},
 {'name':'get_approved_video_link','description':'Get a one-hour HTTPS download link to a previously uploaded, approved Short. Does not upload or publish anything.',
  'inputSchema':{'type':'object','properties':{'video_id':{'type':'string','pattern':'^[a-f0-9]{12}$'}},'required':['video_id'],'additionalProperties':False}}
]


def _rpc_response(identifier: Any, result=None, error=None):
    message={'jsonrpc':'2.0','id':identifier}
    if error is not None: message['error']=error
    else: message['result']=result
    return message


@app.get('/health')
def health():
    return {'service':'Shadow Studio private MCP', 'running':True,'public_publishing_enabled':False}


@app.get('/.well-known/oauth-protected-resource')
def oauth_resource():
    if os.environ.get('SHADOW_MCP_AUTH_MODE', 'token') != 'oauth':
        raise HTTPException(404, 'OAuth is not configured')
    server = os.environ.get('SHADOW_MCP_RESOURCE_URL','')
    issuer = os.environ.get('SHADOW_MCP_OAUTH_ISSUER','')
    if not server.startswith('https://') or not issuer.startswith('https://'):
        raise HTTPException(503,'Public resource URL and issuer are required')
    return {'resource':server,'authorization_servers':[issuer.rstrip('/')],
            'scopes_supported':['shadow:videos:read'],'bearer_methods_supported':['header']}


@app.post('/mcp')
async def mcp(request: Request, authorization: str | None = Header(default=None)):
    _auth(authorization)
    if request.headers.get('content-type','').split(';')[0].strip() != 'application/json':
        raise HTTPException(415,'application/json required')
    try:
        msg=await request.json()
    except Exception:
        return JSONResponse(_rpc_response(None,error={'code':-32700,'message':'Parse error'}))
    if not isinstance(msg,dict) or msg.get('jsonrpc')!='2.0' or not isinstance(msg.get('method'),str):
        return JSONResponse(_rpc_response(msg.get('id') if isinstance(msg,dict) else None,error={'code':-32600,'message':'Invalid request'}))
    if 'id' not in msg:
        # notifications/initialized and other notifications are not requests.
        return JSONResponse(status_code=202, content={})
    method, identifier = msg['method'], msg['id']
    params=msg.get('params') or {}
    if not isinstance(params,dict):
        return JSONResponse(_rpc_response(identifier,error={'code':-32602,'message':'Invalid params'}))
    if method=='initialize':
        return JSONResponse(_rpc_response(identifier, {'protocolVersion':SUPPORTED_PROTOCOL,
            'capabilities':{'tools':{'listChanged':False}}, 'serverInfo':{'name':MCP_NAME,'version':'0.20.0'}}))
    if method=='ping':return JSONResponse(_rpc_response(identifier,{}))
    if method=='tools/list':return JSONResponse(_rpc_response(identifier,{'tools':TOOLS}))
    if method=='tools/call':
        name,args=params.get('name'),params.get('arguments',{})
        if name not in [x['name'] for x in TOOLS]:
            return JSONResponse(_rpc_response(identifier,error={'code':-32602,'message':'Unknown tool'}))
        if not isinstance(args,dict):
            return JSONResponse(_rpc_response(identifier,error={'code':-32602,'message':'Invalid tool arguments'}))
        try:
            if name=='list_approved_videos':
                if args:raise ValueError('Unexpected arguments')
                data=_list_videos()
            else:
                if set(args) != {'video_id'} or not isinstance(args['video_id'],str):
                    raise ValueError('video_id is required')
                data = _details(args['video_id']) if name=='get_approved_video' else _cloud_link(args['video_id'])
            result={'content':[{'type':'text','text':json.dumps(data,ensure_ascii=False)}], 'isError':False}
        except (ValueError, OSError) as exc:
            result={'content':[{'type':'text','text':str(exc)}],'isError':True}
        return JSONResponse(_rpc_response(identifier,result))
    return JSONResponse(_rpc_response(identifier,error={'code':-32601,'message':'Method not found'}))


if __name__=='__main__':
    import uvicorn
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    uvicorn.run(app,host='127.0.0.1',port=8766)
