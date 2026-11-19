"""Read-only integration diagnostics. Live probes never create paid content or upload files.

All remote calls use fixed provider endpoints or a validated private S3 config.
Tokens and keys must not be returned in API responses or exception messages.
"""
import importlib.util
import os
import shutil
from urllib.parse import urlparse


def _item(name, configured, detail, howto, live=None):
    return {'name':name, 'configured':bool(configured), 'reachable':live,
            'detail':detail, 'setup':howto}


def integrations(probe=False):
    import requests
    rows=[]
    model=os.environ.get('SHADOW_OLLAMA_MODEL','').strip()
    ollama_detail='Select a model in .env to enable local AI story generation.'
    ollama_live=None
    if probe and model:
        try:
            resp=requests.get('http://127.0.0.1:11434/api/tags',timeout=3)
            resp.raise_for_status()
            names=[str(m.get('name','')) for m in resp.json().get('models',[]) if isinstance(m,dict)]
            ollama_live=any(n==model or n.split(':')[0]==model.split(':')[0] for n in names)
            ollama_detail='Model found in local Ollama.' if ollama_live else 'Ollama responded, but the configured model is not installed.'
        except (requests.RequestException,ValueError,KeyError,TypeError):
            ollama_live=False;ollama_detail='Ollama is not reachable on this computer.'
    rows.append(_item('Local AI stories · Ollama',bool(model),ollama_detail,
                      'Install Ollama, pull a model, set SHADOW_OLLAMA_MODEL in .env.',ollama_live))

    edge=importlib.util.find_spec('edge_tts') is not None
    rows.append(_item('Neural voices · Edge TTS',edge,
                      'Voice library installed; a live voice preview is needed to verify network access.' if edge else 'Voice package missing.',
                      'Install requirements, then preview a neural voice in Shadow Studio.'))

    key=os.environ.get('ELEVENLABS_API_KEY','').strip()
    vid=os.environ.get('ELEVENLABS_VOICE_ID','').strip()
    premium=bool(key and vid)
    eleven_live=None;eleven_detail='Voice and API key provided; an actual preview verifies narration.' if premium else 'API key and voice ID required.'
    if probe and premium:
        try:
            resp=requests.get('https://api.elevenlabs.io/v1/voices',headers={'xi-api-key':key},timeout=7)
            eleven_live=resp.status_code==200
            if eleven_live:
                entries=resp.json().get('voices',[])
                eleven_live=any(v.get('voice_id')==vid for v in entries if isinstance(v,dict))
                eleven_detail='Credentials valid and selected voice is accessible.' if eleven_live else 'Credentials worked but selected voice was not listed.'
            else: eleven_detail='Provider rejected the credentials or request (HTTP '+str(resp.status_code)+').'
        except (requests.RequestException,ValueError,TypeError):
            eleven_live=False;eleven_detail='Could not reach the premium voice provider.'
    rows.append(_item('Premium voice · ElevenLabs',premium,eleven_detail,
                      'Set ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID in .env.',eleven_live))

    sd_live=None
    sd_detail='Optional local image server, no cloud API key needed.'
    if probe:
        try:
            resp=requests.get('http://127.0.0.1:7860/sdapi/v1/options',timeout=3)
            sd_live=resp.ok and isinstance(resp.json(),dict)
            sd_detail='Local image API reachable.' if sd_live else 'Local image API is not responding.'
        except (requests.RequestException,ValueError,TypeError):
            sd_live=False;sd_detail='Local image API is not running.'
    rows.append(_item('AI images · Stable Diffusion WebUI',False if not probe else sd_live,sd_detail,
                      'Start a compatible WebUI with --api on 127.0.0.1:7860.',sd_live))

    transcription=importlib.util.find_spec('faster_whisper') is not None
    rows.append(_item('Video transcription · faster-whisper',transcription,
                      'Installed; model files may still need downloading on first use.' if transcription else 'Optional local transcription package not installed.',
                      'Install faster-whisper separately to enable transcription.'))

    from cloud_handoff import config,HandoffError
    s3=False;s3_live=None;s3_detail='Private bucket credentials not configured.'
    try:
        c=config();s3=c['enabled']
        if s3:s3_detail='Private cloud transfer is configured; no file has been sent by this check.'
        if s3 and probe:
            try:
                from cloud_handoff import _client
                _client(c).head_bucket(Bucket=c['bucket'])
                s3_live=True;s3_detail='Private storage bucket is accessible.'
            except Exception:
                s3_live=False;s3_detail='Could not confirm private bucket access. Check account, region and permissions.'
    except HandoffError:s3_detail='Cloud storage endpoint configuration is invalid.'
    rows.append(_item('Approved-video cloud handoff · S3 / R2',s3,s3_detail,
                      'Configure a private bucket and access keys in .env; auto-upload stays off by default.',s3_live))

    token=os.environ.get('SHADOW_AGENT_TOKEN','')
    agent=len(token)>=32
    oauth=(os.environ.get('SHADOW_MCP_AUTH_MODE','token').strip().lower()=='oauth' and
           all(os.environ.get(x,'').strip() for x in ('SHADOW_MCP_OAUTH_ISSUER','SHADOW_MCP_OAUTH_JWKS_URL','SHADOW_MCP_OAUTH_AUDIENCE','SHADOW_MCP_OWNER_SUB')))
    rows.append(_item('Private ChatGPT MCP connector',agent or oauth,
                      'Local connector configured; ChatGPT account connection requires independent OAuth and setup.' if (agent or oauth) else 'Connector disabled until authenticated.',
                      'Configure OAuth for remote MCP and connect it in an eligible ChatGPT account. This does not send messages into an existing chat.'))
    rows.append(_item('vidIQ / YouTube publishing',False,
                      'Available through the connected vidIQ tool in ChatGPT, not directly from this local app. Final publishing approval is always separate.',
                      'Approve your Short, create a handoff, then import into vidIQ through ChatGPT.'))
    return {'integrations':rows,'live_probes':bool(probe),
            'disclaimer':'Connectivity is not a successful paid generation or a completed YouTube publish. Keys are never included in this response.'}
