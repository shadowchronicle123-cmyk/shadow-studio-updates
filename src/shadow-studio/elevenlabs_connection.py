"""ElevenLabs local account connector. API keys never leave the owner machine except to ElevenLabs."""
import os, re, threading
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
ENV=ROOT/'.env'
_lock=threading.Lock()

class ElevenLabsError(Exception):pass

def _request_voices(key):
    try:
        response=requests.get('https://api.elevenlabs.io/v1/voices',headers={'xi-api-key':key},timeout=12)
    except requests.RequestException as exc:
        raise ElevenLabsError('Could not contact ElevenLabs over HTTPS') from exc
    if response.status_code in (401,403):raise ElevenLabsError('ElevenLabs refused the API key or its permissions')
    if not response.ok:raise ElevenLabsError('ElevenLabs voice check failed (HTTP '+str(response.status_code)+')')
    try:
        voices=response.json().get('voices',[])
    except (ValueError,AttributeError) as exc:
        raise ElevenLabsError('Unexpected response from ElevenLabs') from exc
    return [{'id':v['voice_id'],'name':str(v.get('name','Voice'))[:70]}
            for v in voices if isinstance(v,dict) and re.fullmatch(r'[A-Za-z0-9]{12,64}',str(v.get('voice_id','')))]

def list_voices():
    key=os.environ.get('ELEVENLABS_API_KEY','').strip()
    if not key:raise ElevenLabsError('ElevenLabs is not connected')
    return _request_voices(key)

def connect(key,voice_id=None):
    """Verify before saving; preserve all existing .env entries and avoid echoing secrets."""
    key=key.strip()
    if not 15<=len(key)<=256 or '\n' in key or '\r' in key or '"' in key or "'" in key:
        raise ElevenLabsError('Invalid API key format')
    voices=_request_voices(key)
    if not voices:raise ElevenLabsError('The connected account has no available voices')
    selected=voice_id if voice_id and any(v['id']==voice_id for v in voices) else voices[0]['id']
    with _lock:
        lines=ENV.read_text(encoding='utf-8').splitlines() if ENV.is_file() else []
        lines=[l for l in lines if not re.match(r'^\s*(ELEVENLABS_API_KEY|ELEVENLABS_VOICE_ID)\s*=',l)]
        lines += ['ELEVENLABS_API_KEY='+key,'ELEVENLABS_VOICE_ID='+selected]
        temp=ENV.with_suffix('.env.tmp')
        temp.write_text('\n'.join(lines)+'\n',encoding='utf-8')
        try:os.chmod(temp,0o600)
        except OSError:pass
        temp.replace(ENV)
        os.environ['ELEVENLABS_API_KEY']=key
        os.environ['ELEVENLABS_VOICE_ID']=selected
    return {'connected':True,'voices':voices,'selected_voice':selected}

def choose_voice(voice_id):
    if not re.fullmatch('[A-Za-z0-9]{12,64}',voice_id):raise ElevenLabsError('Invalid voice ID')
    with _lock:
        lines=ENV.read_text(encoding='utf-8').splitlines() if ENV.is_file() else []
        lines=[l for l in lines if not re.match(r'^\s*ELEVENLABS_VOICE_ID\s*=',l)]
        lines.append('ELEVENLABS_VOICE_ID='+voice_id)
        temp=ENV.with_suffix('.env.tmp');temp.write_text('\n'.join(lines)+'\n',encoding='utf-8')
        try:os.chmod(temp,0o600)
        except OSError:pass
        temp.replace(ENV)
        os.environ['ELEVENLABS_VOICE_ID']=voice_id
    return {'selected_voice':voice_id}
