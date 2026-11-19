"""Shadow Studio local production dashboard, v0.26."""
import json, os, re, threading, uuid, io, zipfile, hashlib, subprocess, shutil as file_shutil
from urllib.parse import urlsplit
from datetime import datetime, timezone
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).resolve().parent / ".env")
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from story_engine import make_story, metadata, random_story
from renderer import line_pause, generate
from voice_engine import VOICES, available_voices, valid_voice, create_voice
from studio_tools import save_project, list_projects, get_project, clip_media, CLIPS
from gameplay_library import GameplayLibrary
from split_screen import split_screen
from video_ranking import rank_videos
from advanced_tools import scene_candidates, transcribe_local, edit_video, motion_from_image, MEDIA_ROOT
from quality_check import analyze as quality_analyze
from approval_security import approved_unchanged, file_digest
from integration_bridge import router as agent_router, DraftRequest, create_draft as stage_local_draft, list_drafts as staged_drafts
from cloud_handoff import config as cloud_config, state as cloud_state, upload as cloud_upload, auto_upload as cloud_auto_upload, HandoffError

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'outputs'; OUT.mkdir(exist_ok=True)
UPLOADS=ROOT/'uploads'; UPLOADS.mkdir(exist_ok=True)
GAMEPLAY=GameplayLibrary(ROOT/'gameplay')
app=FastAPI(title='Shadow Studio — Private Local Edition',docs_url=None,redoc_url=None)
# Local beta: NEVER expose this owner-only dashboard to a public interface.
# Reject DNS-rebinding Host headers and cross-origin state-changing requests.
# Remote/mobile access needs separate authentication before changing this policy.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])

@app.middleware('http')
async def local_dashboard_protection(request: Request, call_next):
    origin = request.headers.get('origin', '')
    if origin:
        from urllib.parse import urlsplit
        try:
            parsed = urlsplit(origin)
            permitted = (parsed.scheme == request.url.scheme and parsed.hostname == request.url.hostname and parsed.port == request.url.port)
        except ValueError:
            permitted = False
        if not permitted:
            return JSONResponse({'detail': 'Cross-site requests to the private dashboard are blocked'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cross-Origin-Resource-Policy'] = 'same-origin'
    return response
app.include_router(agent_router)
lock=threading.Lock()
_preview_lock=threading.Lock()  # Prevent concurrent paid duplicate previews.
jobs={}
active={'id':None}

def validate_rendered_video(result):
    """Fail closed for a broken video before it appears as completed or publishable."""
    report=quality_analyze(Path(result['path']))
    if not report['passed']:
        raise RuntimeError('Export failed technical checks: ' + '; '.join(report['blockers']))
    captions=Path(result['path']).parent/'captions.srt'
    if not captions.is_file() or not captions.read_text(encoding='utf-8').strip():
        raise RuntimeError('Subtitle track was not generated; video kept out of review library')
    return report

def archive_video(jobid, title, description, duration, style, resolution, voice):
    """Persist a completed render so it is still listed after a server restart."""
    meta={'id':jobid,'title':title,'description':description,'duration':round(float(duration),2),
          'style':style,'resolution':resolution,'voice':voice,
          'created_at':datetime.now(timezone.utc).isoformat(), 'review_status':'needs_review', 'review_notes':''}
    path=OUT/jobid/'metadata.json'
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    tmp.replace(path)

@app.get('/api/library')
def library():
    videos=[]
    for path in OUT.glob('*/metadata.json'):
        try:
            if not (path.parent/'short.mp4').is_file():continue
            item=json.loads(path.read_text(encoding='utf-8'))
            if not re.fullmatch('[0-9a-f]{12}',item['id']):continue
            item['video_url']='/api/video/'+item['id']
            videos.append(item)
        except (ValueError,KeyError,OSError):continue
    videos.sort(key=lambda x:x.get('created_at',''),reverse=True)
    return {'videos':videos[:100]}

@app.get('/api/library/{jobid}')
def library_item(jobid:str):
    if not re.fullmatch('[0-9a-f]{12}',jobid):raise HTTPException(400,'Invalid id')
    path=OUT/jobid/'metadata.json'
    if not path.is_file():raise HTTPException(404,'Video not found')
    item=json.loads(path.read_text(encoding='utf-8'))
    return {**item,'video_url':'/api/video/'+jobid}


# Review queue is local and never publishes directly.
VALID_REVIEW = {'needs_review', 'needs_changes', 'approved'}
class ReviewUpdate(BaseModel):
    status: str
    notes: str = Field(default='', max_length=1000)

def video_record(jobid):
    if not re.fullmatch(r'[0-9a-f]{12}',jobid):
        raise HTTPException(400,'Invalid video ID')
    path=OUT/jobid/'metadata.json'
    if not path.is_file() or not (path.parent/'short.mp4').is_file():
        raise HTTPException(404,'Completed video not found')
    try:return path,json.loads(path.read_text(encoding='utf-8'))
    except (ValueError,OSError):raise HTTPException(500,'Video metadata unreadable')

@app.get('/api/library/{jobid}/quality')
def quality_report(jobid: str):
    _, item = video_record(jobid)
    return {'id':jobid, 'title':item.get('title',''), **quality_analyze(OUT/jobid/'short.mp4')}

@app.patch('/api/library/{jobid}/review')
def review_video(jobid:str, req:ReviewUpdate, background_tasks: BackgroundTasks):
    if req.status not in VALID_REVIEW:raise HTTPException(400,'Invalid review state')
    path, item=video_record(jobid)
    if req.status=='approved':
        report=quality_analyze(OUT/jobid/'short.mp4')
        if not report['passed']:
            raise HTTPException(409,'Fix video preflight blockers before approval: '+ '; '.join(report['blockers']))
    item['review_status']=req.status
    # Changing or withdrawing approval invalidates the old exact-file signature.
    item.pop('approved_video_sha256', None)
    if req.status == 'approved':
        item['approved_video_sha256'] = file_digest(OUT/jobid/'short.mp4')
    item['review_notes']=req.notes.strip()
    item['reviewed_at']=datetime.now(timezone.utc).isoformat()
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(item,ensure_ascii=False,indent=2),encoding='utf-8')
    tmp.replace(path)
    if req.status=='approved':
        try:
            cloud=cloud_config()
            if cloud['enabled'] and cloud['auto_upload']:
                background_tasks.add_task(cloud_auto_upload, jobid)
        except HandoffError:pass
    return {**item,'video_url':'/api/video/'+jobid}

@app.post('/api/publishing-drafts', status_code=201)
def draft_from_owner(req: DraftRequest):
    # Dashboard only stages local metadata; this does not connect to or upload to YouTube.
    return stage_local_draft(req)

@app.get('/api/publishing-drafts')
def drafts_for_owner():
    return staged_drafts()


@app.get('/api/cloud-handoff/config')
def cloud_handoff_config():
    try:
        c=cloud_config()
        return {'configured':c['enabled'],'auto_upload_on_approval':c['enabled'] and c['auto_upload'],
                'instructions':'Private cloud upload only. ChatGPT / vidIQ draft initiation and publishing are separate.'}
    except HandoffError as exc:raise HTTPException(400,str(exc))

@app.get('/api/cloud-handoff/{jobid}/status')
def cloud_handoff_status(jobid:str):
    try:return cloud_state(jobid)
    except HandoffError as exc:raise HTTPException(400,str(exc))

@app.post('/api/cloud-handoff/{jobid}')
def cloud_handoff_send(jobid:str):
    try:return cloud_upload(jobid)
    except HandoffError as exc:
        raise HTTPException(409,str(exc))

@app.get('/api/chatgpt-handoff/{jobid}')
def chatgpt_handoff(jobid: str):
    """Export one human-approved Short for manual attachment in ChatGPT.

    This endpoint never transmits a file off the computer and does not grant ChatGPT access.
    """
    _, item = video_record(jobid)
    if not approved_unchanged(item, OUT/jobid/'short.mp4'):
        raise HTTPException(409, 'Video has changed since approval or has an older unverified approval. Review and approve it again.')
    video = OUT / jobid / 'short.mp4'
    result = quality_analyze(video)
    if not result['passed']:
        raise HTTPException(409, 'Video failed current technical quality check')
    if video.stat().st_size > 200 * 1024 * 1024:
        raise HTTPException(413, 'MP4 exceeds 200 MB; download video separately')
    digest=file_digest(video)
    captions=OUT/jobid/'captions.srt'
    manifest = {
        'schema':'shadow-studio-handoff/v1', 'id':jobid,
        'title':item.get('title',''), 'description':item.get('description',''),
        'desired_platform':'YouTube Shorts', 'channel':'ShadowChronicles',
        'requested_action':'Prepare a vidIQ publishing draft only, then await my approval',
        'approved_in_shadow_studio':True,
        'not_authorized_for_public_publishing':True,
        'file':'short.mp4','sha256':digest,'subtitles_file':'captions.srt' if captions.is_file() else None,
        'technical_quality':result,
    }
    instructions=(
        'SHADOW STUDIO → CHATGPT + vidIQ HANDOFF\n\n'
        '1. Attach short.mp4 from this ZIP to a ChatGPT conversation.\n'
        '2. Paste REQUEST_FOR_CHATGPT.txt in the same chat.\n'
        '3. ChatGPT can import the MP4 to vidIQ and prepare a YouTube draft.\n'
        '4. Review exact title, description, video and public/private setting.\n'
        '5. Approve publishing separately; this export is NOT publishing permission.\n'
    )
    request=(
        f'Please import the attached MP4 into vidIQ for my ShadowChronicles YouTube channel '
        f'and create a publishing draft (do not publish).\n\n'
        f'Title: {item.get("title", "")}\n\n'
        f'Description:\n{item.get("description", "")}\n\n'
        'Show me the draft, including visibility, for final approval. '
        'The approval inside Shadow Studio is NOT authorization to publish publicly.'
    )
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        archive.write(video, 'short.mp4')
        if captions.is_file():archive.write(captions,'captions.srt')
        archive.writestr('handoff.json', json.dumps(manifest,indent=2,ensure_ascii=False))
        archive.writestr('REQUEST_FOR_CHATGPT.txt',request)
        archive.writestr('README_FIRST.txt',instructions)
    buffer.seek(0)
    return StreamingResponse(buffer,media_type='application/zip',headers={
        'Content-Disposition':f'attachment; filename="shadow-chatgpt-handoff-{jobid}.zip"',
        'Cache-Control':'no-store'})

@app.get('/api/publishing-kit')
def publishing_kit():
    approved=[]
    for path in OUT.glob('*/metadata.json'):
        try:
            item=json.loads(path.read_text(encoding='utf-8'))
            jid=item.get('id','')
            if re.fullmatch('[a-f0-9]{12}', jid) and approved_unchanged(item, path.parent/'short.mp4'):
                # Recheck technical quality at export time rather than trusting past metadata.
                if quality_analyze(path.parent/'short.mp4')['passed']:
                    approved.append((item,path.parent/'short.mp4'))
        except (ValueError,OSError):continue
    if not approved:raise HTTPException(404,'Approve at least one video in the Library first')
    if len(approved)>20:raise HTTPException(400,'Too many approved videos; please archive older exports')
    mem=io.BytesIO()
    manifest=[]
    with zipfile.ZipFile(mem,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as z:
        for item,file in sorted(approved,key=lambda x:x[0].get('created_at','')):
            jid=item['id']
            z.write(file,f'{jid}/short.mp4')
            captionfile=file.parent/'captions.srt'
            if captionfile.is_file():z.write(captionfile,f'{jid}/captions.srt')
            z.writestr(f'{jid}/youtube.txt',f"TITLE: {item.get('title','')}\n\nDESCRIPTION:\n{item.get('description','')}\n")
            manifest.append({'id':jid,'title':item.get('title',''),'review_status':'approved'})
        z.writestr('UPLOAD_CHECKLIST.txt','1. Review every MP4.\n2. Confirm voice and gameplay rights.\n3. Upload approved video in YouTube Studio or vidIQ.\n4. Confirm title, description and visibility.\n5. Publish only after your own final approval.\n')
        z.writestr('manifest.json',json.dumps(manifest,indent=2))
    mem.seek(0)
    return StreamingResponse(mem,media_type='application/zip',headers={'Content-Disposition':'attachment; filename="shadow-studio-approved-shorts.zip"'})


STARTER_STORIES=[
 {'title':'My Best Friend Sent Me a Photo From Tomorrow 😳 #Shorts','lines':[
  {'speaker':'Me','text':'My best friend sent me a photo of my car in a ditch. The timestamp said tomorrow, 7:14 PM.'},
  {'speaker':'Friend','text':'Please tell me you are not driving tonight.'},
  {'speaker':'Me','text':'I laughed until I noticed the scratch on the rear bumper.'},
  {'speaker':'Friend','text':'What scratch?'},
  {'speaker':'Me','text':'The one I made an hour ago. Nobody else had seen it yet.'},
  {'speaker':'Friend','text':'Then stay home. Seriously.'},
  {'speaker':'Me','text':'I handed my keys to my brother and told him not to touch the car.'},
  {'speaker':'Friend','text':'And that fixed it, right?'},
  {'speaker':'Me','text':'At 7:14 tomorrow, my phone rang. My brother had taken the keys while I was asleep.'},
  {'speaker':'Friend','text':'Was he okay?'},
  {'speaker':'Me','text':'He was fine. But the photo had changed. Now I was standing beside the car.'},
  {'speaker':'Me','text':'Would you believe the next picture?'}]},
 {'title':'The Wedding Seat With My Name on It 💍 #Shorts','lines':[
  {'speaker':'Me','text':'I got to my sister’s wedding and found my place card at the back of the room.'},
  {'speaker':'Friend','text':'That is strange. You helped plan the whole thing.'},
  {'speaker':'Me','text':'Then I saw another place card. My name was on the seat next to the groom.'},
  {'speaker':'Friend','text':'They accidentally printed your name twice?'},
  {'speaker':'Me','text':'That’s what I thought. But the second card had my handwriting on it.'},
  {'speaker':'Friend','text':'Wait. Did you write it?'},
  {'speaker':'Me','text':'No. And the back said, “If he says the vows, show her the envelope.”'},
  {'speaker':'Friend','text':'What envelope?'},
  {'speaker':'Me','text':'The one taped under the chair. Inside was a receipt for two wedding rings.'},
  {'speaker':'Friend','text':'That sounds normal.'},
  {'speaker':'Me','text':'Not when the receipt had my sister’s name crossed out. Another name was written underneath.'},
  {'speaker':'Me','text':'Do I tell her before the ceremony starts?'}]},
 {'title':'My Roommate Was Texting From Our Empty House 📱 #Shorts','lines':[
  {'speaker':'Me','text':'My roommate texted me, “I left the kitchen light on. Can you turn it off?”'},
  {'speaker':'Friend','text':'Okay, so?'},
  {'speaker':'Me','text':'We were both three hours away on a road trip.'},
  {'speaker':'Friend','text':'Maybe it was a scheduled message.'},
  {'speaker':'Me','text':'Then a second text came through: “Actually, don’t go inside.”'},
  {'speaker':'Friend','text':'Did somebody break in?'},
  {'speaker':'Me','text':'I checked the doorbell camera. Someone in my exact jacket was unlocking the door.'},
  {'speaker':'Friend','text':'Could that have been old footage?'},
  {'speaker':'Me','text':'The time was live. And the person looked straight at the camera.'},
  {'speaker':'Friend','text':'Did you recognize them?'},
  {'speaker':'Me','text':'The camera sent one final alert: “Known face detected: You.”'},
  {'speaker':'Me','text':'What would you do next?'}]}
]
@app.get('/api/starter-stories')
def starter_stories():
    return {'stories':STARTER_STORIES}

class StoryRequest(BaseModel):
    idea:str=Field(default="",max_length=350)
    genre:str|None=None
    style:str='messages'
    target:int=45

class RenderRequest(BaseModel):
    story:dict
    style:str='messages'
    background_name:str|None=None
    voice:str="en-US-AriaNeural"
    voice_rate:int=Field(default=0,ge=-30,le=30)
    second_voice:str|None=None
    resolution:str="720p"
    gameplay_id:str|None=None

def safe_style(s):
    if s not in ('messages','reddit'):raise HTTPException(400,'Choose messages or reddit style')
    return s

@app.get('/',response_class=HTMLResponse)
def home():
    return HTML

class ElevenConnectRequest(BaseModel):
    api_key:str=Field(min_length=15,max_length=256)

class ElevenChooseRequest(BaseModel):
    voice_id:str

@app.post('/api/elevenlabs/connect')
def eleven_connect(req:ElevenConnectRequest):
    from elevenlabs_connection import connect,ElevenLabsError
    try:return connect(req.api_key)
    except ElevenLabsError as exc:raise HTTPException(400,str(exc)) from exc

@app.get('/api/elevenlabs/voices')
def eleven_voices():
    from elevenlabs_connection import list_voices,ElevenLabsError
    try:return {'voices':list_voices(),'selected_voice':os.environ.get('ELEVENLABS_VOICE_ID','')}
    except ElevenLabsError as exc:raise HTTPException(400,str(exc)) from exc

@app.post('/api/elevenlabs/choose')
def eleven_choose(req:ElevenChooseRequest):
    from elevenlabs_connection import choose_voice,ElevenLabsError
    try:
        from elevenlabs_connection import list_voices
        if req.voice_id not in {v['id'] for v in list_voices()}:raise ElevenLabsError('Voice is not accessible from this account')
        return choose_voice(req.voice_id)
    except ElevenLabsError as exc:raise HTTPException(400,str(exc)) from exc

@app.get('/api/updater/check')
def update_check():
    from update_manager import check, UpdateError
    try:return check()
    except UpdateError as exc:raise HTTPException(503,str(exc)) from exc

@app.get('/api/health')
def health():
    import shutil
    return {'ok':True,'ffmpeg':bool(shutil.which('ffmpeg')),'ffprobe':bool(shutil.which('ffprobe')),'ollama_model':bool(os.getenv('SHADOW_OLLAMA_MODEL')),'private':'localhost-only'}

@app.get('/api/launch-readiness')
def launch_readiness():
    """Non-destructive readiness check. Voice/network providers require a live preview test."""
    import shutil, importlib.util, subprocess
    tools={name:bool(shutil.which(name)) for name in ('ffmpeg','ffprobe')}
    versions={}
    if tools['ffmpeg']:
        try:
            proc=subprocess.run(['ffmpeg','-version'],text=True,capture_output=True,timeout=5)
            versions['ffmpeg']=proc.stdout.splitlines()[0][:120] if proc.returncode==0 else 'unavailable'
        except (OSError,subprocess.TimeoutExpired):versions['ffmpeg']='unavailable'
    neural=importlib.util.find_spec('edge_tts') is not None
    voice_options=[x['name'] for x in available_voices()]
    completed=0;approved=0;review=0
    for path in OUT.glob('*/metadata.json'):
        try:
            if not (path.parent/'short.mp4').is_file():continue
            item=json.loads(path.read_text(encoding='utf-8'))
            completed+=1
            if item.get('review_status')=='approved':approved+=1
            else:review+=1
        except (ValueError,OSError):pass
    tests=[
        {'name':'FFmpeg installed','passed':tools['ffmpeg'],'required':True,'detail':'Required to render and export MP4s'},
        {'name':'FFprobe installed','passed':tools['ffprobe'],'required':True,'detail':'Required to inspect video exports'},
        {'name':'Natural voice dependency','passed':neural or bool(configured_premium_voice()),'required':False,'detail':'A live voice preview is still required to verify provider access'},
    ]
    return {'ready_to_attempt_render':all(x['passed'] for x in tests if x['required']),
            'checks':tests,'voices':voice_options,'videos':{'completed':completed,'approved':approved,'awaiting_review':review},
            'ffmpeg_version':versions.get('ffmpeg','Not detected'),
            'notes':['Preview your chosen voice before batch rendering; network availability and quotas cannot be confirmed offline.',
                     'Review every video and inspect its quality report before approving.',
                     'Publishing still needs explicit approval in ChatGPT + vidIQ.']}

def configured_premium_voice():
    return any(v.get('provider')=='premium' for v in available_voices())

@app.post('/api/story')
def story(req:StoryRequest):
    safe_style(req.style)
    try:
        if req.genre is not None:
            return {'story':random_story(req.genre, req.target)}
        if len(req.idea.strip()) < 3:
            raise ValueError('Enter a story idea or choose a genre')
        return {'story':make_story(req.idea,req.style,req.target)}
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc

@app.post('/api/upload-background')
async def upload_background(file:UploadFile=File(...)):
    filename=file.filename or ''
    if Path(filename).suffix.lower() not in ('.mp4','.mov','.mkv','.webm'):
        raise HTTPException(400,'Upload a video file (MP4, MOV, MKV, WEBM)')
    name='background_'+uuid.uuid4().hex[:12]+Path(filename).suffix.lower()
    path=UPLOADS/name; size=0
    try:
        with path.open('wb') as dst:
            while chunk:=await file.read(1024*1024):
                size+=len(chunk)
                if size>200*1024*1024: raise HTTPException(413,'Background limit is 200 MB')
                dst.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {'name':name,'size':size}

@app.get('/api/gameplay')
def gameplay_list():
    return {'clips':GAMEPLAY.list_clips()}

@app.post('/api/gameplay')
async def gameplay_upload(file:UploadFile=File(...), tag:str=Form('general')):
    if Path(file.filename or '').suffix.lower() not in ('.mp4','.mov','.mkv','.webm'):
        raise HTTPException(400,'Upload a gameplay video file')
    filename='gameplay_'+uuid.uuid4().hex[:12]+Path(file.filename).suffix.lower()
    target=GAMEPLAY.root/filename
    total=0
    try:
        with target.open('wb') as out:
            while chunk:=await file.read(1024*1024):
                total+=len(chunk)
                if total>500*1024*1024:raise HTTPException(413,'Gameplay upload limit is 500MB')
                out.write(chunk)
        item=GAMEPLAY.register(target,tag)
        return item
    except Exception:
        target.unlink(missing_ok=True)
        raise

class VoicePreviewRequest(BaseModel):
    voice:str='en-US-AriaNeural'
    rate:int=Field(default=0,ge=-30,le=30)

@app.get('/api/voices')
def voices():
    import importlib.util
    choices=VOICES.copy()
    premium_error=None
    if os.getenv('ELEVENLABS_API_KEY','').strip():
        try:
            from elevenlabs_connection import list_voices
            choices=[{'id':'elevenlabs:'+v['id'],'name':v['name']+' — ElevenLabs','provider':'premium'} for v in list_voices()]+choices
        except Exception:
            premium_error='Could not retrieve ElevenLabs voices. Check the connection in Publishing.'
            choices=available_voices()
    return {'voices':choices, 'neural_available':importlib.util.find_spec('edge_tts') is not None,
            'premium_configured':any(v['provider']=='premium' for v in choices),
            'premium_error':premium_error,
            'message':'Selecting an ElevenLabs voice for previews or rendering may use paid API credits.'}

@app.post('/api/voice-preview')
def voice_preview(req:VoicePreviewRequest):
    if not valid_voice(req.voice):raise HTTPException(400,'Invalid voice')
    # A repeat preview of the same voice and rate should not charge another
    # premium synthesis request. Files are stored only on the owner's machine.
    import hashlib
    preview_text = 'I knew something was wrong the moment I opened that message.'
    fingerprint = hashlib.sha256((req.voice+'|'+str(req.rate)+'|'+preview_text).encode()).hexdigest()[:24]
    folder = OUT / 'voice_previews'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (fingerprint + '.wav')
    # Lock re-checks the cache so concurrent requests never synthesize the same
    # paid preview twice. A response is published only after the WAV is complete.
    with _preview_lock:
        if not path.is_file() or path.stat().st_size < 5000:
            temp_path = folder / (fingerprint + '_' + uuid.uuid4().hex[:8] + '.wav')
            try:
                create_voice(preview_text, temp_path, req.voice, req.rate)
                if not temp_path.is_file() or temp_path.stat().st_size < 5000:
                    raise RuntimeError('Voice provider produced no valid preview audio')
                import wave
                try:
                    with wave.open(str(temp_path), 'rb') as audio:
                        if audio.getnchannels() < 1 or audio.getnframes() < audio.getframerate() // 4:
                            raise RuntimeError('Provider returned a damaged or very short voice preview')
                except (wave.Error, EOFError) as exc:
                    raise RuntimeError('Provider returned a damaged WAV preview') from exc
                temp_path.replace(path)
            except Exception as e:
                temp_path.unlink(missing_ok=True)
                raise HTTPException(503,str(e)[-300:])
    return FileResponse(path,media_type='audio/wav',filename='shadow-voice-preview.wav')

@app.post('/api/render')
def render(req:RenderRequest):
    safe_style(req.style)
    if req.resolution not in ('720p','1080p'): raise HTTPException(400,'Choose 720p or 1080p')
    if not valid_voice(req.voice):raise HTTPException(400,'Invalid voice')
    if req.second_voice and not valid_voice(req.second_voice):raise HTTPException(400,'Invalid second voice')
    data=req.story
    if not isinstance(data,dict) or not isinstance(data.get('lines'),list):raise HTTPException(400,'Story lines missing')
    lines=data['lines']
    if not 2<=len(lines)<=24:raise HTTPException(400,'Use 2 to 24 story lines')
    clean=[]
    for x in lines:
        if not isinstance(x,dict):raise HTTPException(400,'Invalid story line')
        speaker=str(x.get('speaker','Narrator')).strip()[:24]
        message=str(x.get('text','')).strip()[:240]
        if not message:raise HTTPException(400,'Empty story line')
        try:
            pause = line_pause(x)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        clean.append({'speaker':speaker or 'Narrator','text':message,'pause_before':pause})
    title=str(data.get('title','Original Story'))[:95]
    background=None
    if req.background_name and req.gameplay_id:raise HTTPException(400,'Choose a gameplay library clip or a one-time upload, not both')
    selected_gameplay=None
    if req.gameplay_id:
        try:
            selected_gameplay=GAMEPLAY.pick(req.gameplay_id)
            background=selected_gameplay['path']
        except ValueError as exc:raise HTTPException(400,str(exc))
    if req.background_name:
        if not re.fullmatch(r'background_[0-9a-f]{12}\.(mp4|mov|mkv|webm)',req.background_name):raise HTTPException(400,'Invalid background')
        background=UPLOADS/req.background_name
        if not background.is_file():raise HTTPException(404,'Background not found')
    with lock:
        if active['id'] and jobs.get(active['id'],{}).get('state')=='running':raise HTTPException(409,'A video is already rendering')
        jobid=uuid.uuid4().hex[:12]
        jobs[jobid]={'id':jobid,'state':'running','percent':1,'message':'Starting render','title':title,'description':metadata({'title':title,'lines':clean})['description']}
        active['id']=jobid
    def run_job():
        def callback(p,msg):
            with lock:jobs[jobid].update(percent=p,message=msg)
        try:
            result=generate({'title':title,'lines':clean},OUT/jobid,style=req.style,background=background,progress=callback,voice=req.voice,second_voice=req.second_voice,voice_rate=req.voice_rate,resolution=req.resolution,gameplay_start=selected_gameplay['cursor'] if selected_gameplay else 0)
            callback(96, 'Verifying finished video and audio')
            validate_rendered_video(result)
            archive_video(jobid,title,jobs[jobid]['description'],result['duration'],req.style,req.resolution,req.voice)
            if selected_gameplay:GAMEPLAY.mark_used(selected_gameplay['id'],result['duration'])
            with lock: jobs[jobid].update(state='complete',message='Ready to review',percent=100,duration=result['duration'],resolution=req.resolution,fps=30,video_url='/api/video/'+jobid)
        except Exception as e:
            with lock: jobs[jobid].update(state='failed',message=str(e)[-650:])
    threading.Thread(target=run_job,daemon=True).start()
    return {'id':jobid}

@app.get('/api/job/{jobid}')
def job(jobid:str):
    with lock: data=dict(jobs[jobid]) if jobid in jobs else None
    if not data:raise HTTPException(404,'Unknown job')
    return data

@app.get('/api/subtitles/{jobid}')
def subtitles(jobid: str):
    if not re.fullmatch(r'[0-9a-f]{12}',jobid):
        raise HTTPException(400, 'Invalid video ID')
    video_record(jobid)
    subtitles_file = OUT/jobid/'captions.srt'
    if not subtitles_file.is_file():
        raise HTTPException(404, 'Subtitles unavailable for this video')
    return FileResponse(subtitles_file,media_type='application/x-subrip',filename=f'shadowchronicles_{jobid}.srt',headers={'Cache-Control':'no-store'})

@app.get('/api/video/{jobid}')
def video(jobid:str):
    if not re.fullmatch(r'[a-f0-9]{12}',jobid):raise HTTPException(400,'Invalid id')
    path=OUT/jobid/'short.mp4'
    if not path.is_file():raise HTTPException(404,'Video not ready')
    return FileResponse(path,media_type='video/mp4',filename='shadowchronicles_'+jobid+'.mp4')


class ProjectRequest(BaseModel):
    title:str='Untitled'
    lines:list[dict]
    style:str='messages'
    voice:str='en-US-AriaNeural'
    voice_rate:int=0
    resolution:str='720p'

@app.post('/api/projects')
def project_save(req:ProjectRequest):
    try:return save_project(req.model_dump())
    except ValueError as e:raise HTTPException(400,str(e))

@app.get('/api/projects')
def project_list():return {'projects':list_projects()}

@app.get('/api/projects/{projectid}')
def project_get(projectid:str):
    try:return get_project(projectid)
    except (ValueError,FileNotFoundError):raise HTTPException(404,'Project not found')

@app.get('/api/jobs')
def all_jobs():
    with lock:return {'jobs':sorted((dict(x) for x in jobs.values()),key=lambda d:d['id'],reverse=True)[:50]}

class BatchRequest(BaseModel):
    ideas:list[str]=Field(min_length=1,max_length=8)
    style:str='messages'
    target:int=45
    voice:str='en-US-AriaNeural'
    voice_rate:int=Field(default=0,ge=-30,le=30)
    resolution:str='720p'
    gameplay_id:str|None=None

@app.post('/api/batch')
def batch_render(req:BatchRequest):
    safe_style(req.style)
    if not valid_voice(req.voice) or req.resolution not in ('720p','1080p'):
        raise HTTPException(400,'Invalid voice or resolution')
    with lock:
        if active['id'] and jobs.get(active['id'],{}).get('state') in ('running','queued'):
            raise HTTPException(409,'A render is already running')
        batchid=uuid.uuid4().hex[:12]
        active['id']=batchid
        jobs[batchid]={'id':batchid,'state':'running','percent':1,'message':'Preparing batch','title':'Batch render'}
    def work():
        ids=[]
        try:
            for n,idea in enumerate(req.ideas):
                story=make_story(idea,req.style,req.target)
                jobid=uuid.uuid4().hex[:12]
                with lock:
                    jobs[jobid]={'id':jobid,'state':'running','percent':0,'message':'Preparing video','title':story['title'],'description':metadata(story)['description']}
                    jobs[batchid].update(message=f'Rendering {n+1} of {len(req.ideas)}',percent=round(n/len(req.ideas)*100))
                def progress(p,m):
                    with lock:jobs[jobid].update(percent=p,message=m)
                clip=GAMEPLAY.pick(req.gameplay_id) if req.gameplay_id else None
                result=generate(story,OUT/jobid,style=req.style,voice=req.voice,voice_rate=req.voice_rate,resolution=req.resolution,progress=progress,background=clip['path'] if clip else None,gameplay_start=clip['cursor'] if clip else 0)
                validate_rendered_video(result)
                if clip:GAMEPLAY.mark_used(clip['id'],result['duration'])
                archive_video(jobid,story['title'],jobs[jobid]['description'],result['duration'],req.style,req.resolution,req.voice)
                with lock:jobs[jobid].update(state='complete',percent=100,message='Ready',duration=result['duration'],video_url='/api/video/'+jobid)
                ids.append(jobid)
            with lock:jobs[batchid].update(state='complete',percent=100,message=f'{len(ids)} videos ready',video_ids=ids)
        except Exception as e:
            with lock:
                if 'jobid' in locals() and jobs.get(jobid,{}).get('state')=='running':
                    jobs[jobid].update(state='failed',message=str(e)[-400:])
                jobs[batchid].update(state='failed',message=str(e)[-400:],video_ids=ids)
        finally:
            with lock:active['id']=None
    threading.Thread(target=work,daemon=True).start()
    return {'id':batchid}

# Curated-story batch: no template substitution; renders the exact original scripts.
class ReadyBatchRequest(BaseModel):
    story_indices: list[int] = Field(default_factory=lambda:[0,1,2],min_length=1,max_length=3)
    voice: str='en-US-AriaNeural'
    voice_rate: int=Field(default=0,ge=-30,le=30)
    resolution: str='720p'
    style: str='messages'
    gameplay_id: str|None=None

@app.post('/api/batch-ready')
def batch_ready(req:ReadyBatchRequest):
    safe_style(req.style)
    if not valid_voice(req.voice) or req.resolution not in ('720p','1080p'):
        raise HTTPException(400,'Invalid voice or resolution')
    if len(set(req.story_indices))!=len(req.story_indices) or any(i<0 or i>=len(STARTER_STORIES) for i in req.story_indices):
        raise HTTPException(400,'Choose distinct starter stories')
    with lock:
        if active['id'] and jobs.get(active['id'],{}).get('state') in ('running','queued'):
            raise HTTPException(409,'A render is already running')
        batchid=uuid.uuid4().hex[:12]
        active['id']=batchid
        jobs[batchid]={'id':batchid,'state':'running','percent':1,'message':'Preparing three original stories','title':'Ready stories batch'}
    def work():
        completed=[]
        try:
            for n,idx in enumerate(req.story_indices):
                source=STARTER_STORIES[idx]
                story={'title':source['title'],'lines':[dict(x) for x in source['lines']]}
                jobid=uuid.uuid4().hex[:12]
                with lock:
                    jobs[jobid]={'id':jobid,'state':'running','percent':0,'message':'Starting narration','title':story['title'],'description':metadata(story)['description']}
                    jobs[batchid].update(message=f'Rendering story {n+1} of {len(req.story_indices)}',percent=round(n/len(req.story_indices)*100))
                def progress(p,m):
                    with lock:jobs[jobid].update(percent=p,message=m)
                gameplay=GAMEPLAY.pick(req.gameplay_id) if req.gameplay_id else None
                result=generate(story,OUT/jobid,style=req.style,voice=req.voice,voice_rate=req.voice_rate,resolution=req.resolution,progress=progress,background=gameplay['path'] if gameplay else None,gameplay_start=gameplay['cursor'] if gameplay else 0)
                validate_rendered_video(result)
                if gameplay:GAMEPLAY.mark_used(gameplay['id'],result['duration'])
                archive_video(jobid,story['title'],jobs[jobid]['description'],result['duration'],req.style,req.resolution,req.voice)
                with lock:jobs[jobid].update(state='complete',percent=100,message='Ready for review',duration=result['duration'],video_url='/api/video/'+jobid)
                completed.append(jobid)
            with lock:jobs[batchid].update(state='complete',percent=100,message=f'{len(completed)} original Shorts ready for review',video_ids=completed)
        except Exception as e:
            with lock:
                if 'jobid' in locals() and jobs.get(jobid,{}).get('state')=='running':jobs[jobid].update(state='failed',message=str(e)[-450:])
                jobs[batchid].update(state='failed',message=str(e)[-450:],video_ids=completed)
        finally:
            with lock:active['id']=None
    threading.Thread(target=work,daemon=True).start()
    return {'id':batchid,'count':len(req.story_indices)}

@app.get('/api/metadata/{jobid}')
def job_metadata(jobid:str):
    with lock:d=jobs.get(jobid)
    if not d:raise HTTPException(404,'Not found')
    return {'title':d.get('title'),'description':d.get('description'),'video_url':d.get('video_url')}

class ClipRequest(BaseModel):
    background_name:str
    start:float=Field(ge=0)
    end:float=Field(gt=0)
    orientation:str='vertical'

@app.post('/api/clip')
def make_clip(req:ClipRequest):
    if not re.fullmatch(r'background_[0-9a-f]{12}\.(mp4|mov|mkv|webm)',req.background_name):
        raise HTTPException(400,'Invalid uploaded file')
    source=UPLOADS/req.background_name
    if not source.is_file():raise HTTPException(404,'File unavailable')
    try:dest=clip_media(source,req.start,req.end,req.orientation)
    except (ValueError,RuntimeError) as e:raise HTTPException(400,str(e))
    return {'download_url':'/api/clip/'+dest.stem,'id':dest.stem}

@app.get('/api/clip/{clipid}')
def download_clip(clipid:str):
    if not re.fullmatch('[a-f0-9]{12}',clipid):raise HTTPException(400,'Invalid id')
    f=CLIPS/(clipid+'.mp4')
    if not f.is_file():raise HTTPException(404,'Clip missing')
    return FileResponse(f,media_type='video/mp4',filename='shadow_clip_'+clipid+'.mp4')


# Split Screen: private, local, owner-uploaded footage only. Does not publish.
class SplitRequest(BaseModel):
    top_name: str
    bottom_name: str
    seconds: int = Field(default=15, ge=3, le=120)
    layout: str = 'stacked'

@app.post('/api/split-screen')
def split_tool(req: SplitRequest):
    pattern = r'background_[a-f0-9]{12}\.(mp4|mov|mkv|webm)'
    if not re.fullmatch(pattern, req.top_name) or not re.fullmatch(pattern,req.bottom_name):
        raise HTTPException(400, 'Choose two videos uploaded through the workspace')
    try:
        result = split_screen(UPLOADS/req.top_name, UPLOADS/req.bottom_name, CLIPS, req.seconds, req.layout)
    except ValueError as e:
        raise HTTPException(400,str(e))
    except (RuntimeError,OSError) as e:
        raise HTTPException(422, 'Video render failed: '+str(e)[-400:])
    return {'id':result.stem,'video_url':'/api/clip/'+result.stem,'duration':req.seconds,'layout':req.layout}

# Video Ranking: ranked sequence of footage uploaded by the owner. No publishing.
class RankClip(BaseModel):
    name:str
    title:str=Field(default='',max_length=50)

class RankRequest(BaseModel):
    clips:list[RankClip]=Field(min_length=2,max_length=5)
    seconds_each:int=Field(default=4,ge=2,le=12)

@app.post('/api/video-ranking')
def ranking_tool(req:RankRequest):
    pattern=r'background_[a-f0-9]{12}\.(mp4|mov|mkv|webm)'
    entries=[]
    for item in req.clips:
        if not re.fullmatch(pattern,item.name):raise HTTPException(400,'Invalid uploaded video name')
        entries.append((UPLOADS/item.name,item.title))
    try:result=rank_videos(entries,CLIPS,req.seconds_each)
    except ValueError as e:raise HTTPException(400,str(e))
    except (RuntimeError,OSError) as e:raise HTTPException(422,'Ranking render failed: '+str(e)[-400:])
    return {'id':result.stem,'video_url':'/api/clip/'+result.stem,'duration':len(entries)*req.seconds_each}


# Advanced local tools: no third-party media scraping or public publishing.
class AdvancedSource(BaseModel):
    background_name: str

def owned_source(name: str):
    if not re.fullmatch(r'background_[0-9a-f]{12}\.(mp4|mov|mkv|webm)', name):
        raise HTTPException(400, 'Upload media using the dashboard first')
    f=UPLOADS/name
    if not f.is_file():raise HTTPException(404,'Uploaded media not found')
    return f

@app.post('/api/scene-suggestions')
def suggest_scenes(req: AdvancedSource):
    try:return scene_candidates(owned_source(req.background_name))
    except Exception as e:raise HTTPException(422,str(e)[-400:])

class TranscribeRequest(AdvancedSource):
    model:str='tiny'


class UrlImportRequest(BaseModel):
    url:str=Field(min_length=12,max_length=2048)
    rights_confirmed:bool=False

@app.post('/api/import-video-url')
def import_video_url(req:UrlImportRequest):
    """Import owner-authorized YouTube or Twitch footage using optional yt-dlp.
    This owner-only localhost service never accepts arbitrary hostnames or file URLs.
    """
    if not req.rights_confirmed:
        raise HTTPException(400,'Confirm that you own or have permission to use this footage')
    try:parsed=urlsplit(req.url)
    except ValueError:raise HTTPException(400,'Invalid video URL')
    domain=(parsed.hostname or '').lower().rstrip('.')
    accepted={'youtube.com','www.youtube.com','m.youtube.com','youtu.be','twitch.tv','www.twitch.tv','clips.twitch.tv'}
    if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443) or domain not in accepted:
        raise HTTPException(400,'Only public HTTPS YouTube or Twitch links are supported')
    if not file_shutil.which('yt-dlp'):
        try:import yt_dlp
        except ImportError:raise HTTPException(503,'Install the optional URL importer: .venv\\Scripts\\python.exe -m pip install yt-dlp')
        command=[__import__('sys').executable,'-m','yt_dlp']
    else:command=['yt-dlp']
    identifier=uuid.uuid4().hex[:12]
    filename='background_'+identifier
    target=UPLOADS/(filename+'.%(ext)s')
    args=command+['--no-playlist','--no-progress','--no-warnings','--no-part','--max-filesize','250M',
                  '--socket-timeout','15','--retries','1','--merge-output-format','mp4',
                  '-f','bestvideo[height<=1080]+bestaudio/best[height<=1080]/best',
                  '-o',str(target),'--',req.url]
    try:proc=subprocess.run(args,capture_output=True,text=True,timeout=180)
    except subprocess.TimeoutExpired:raise HTTPException(422,'Video download timed out. Try a shorter clip or upload the file.')
    if proc.returncode:
        raise HTTPException(422,'This video could not be imported. It might be restricted, live, private, or blocked by the platform.')
    files=[p for p in UPLOADS.glob(filename+'.*') if p.suffix.lower() in ('.mp4','.mkv','.webm','.mov')]
    if len(files)!=1:raise HTTPException(422,'The source did not produce a supported video file')
    f=files[0]
    if f.stat().st_size>250_000_000:
        f.unlink(missing_ok=True)
        raise HTTPException(413,'Imported file exceeds the 250 MB limit')
    return {'name':f.name,'source':'authorized URL','size':f.stat().st_size}

class AutoClipRequest(AdvancedSource):
    length:int=Field(default=8,ge=3,le=30)
    count:int=Field(default=3,ge=1,le=5)

@app.post('/api/auto-clips')
def export_auto_clips(req:AutoClipRequest):
    source=owned_source(req.background_name)
    if not ((req.count==1 and 3<=req.length<=30) or (req.count==3 and 3<=req.length<=10)):
        raise HTTPException(400,'Choose one clip of 3–30 seconds or three clips of 3–10 seconds')
    try:
        info=scene_candidates(source)
        duration=float(info['duration'])
        if duration<3.1:raise ValueError('Footage must be at least 3 seconds long')
        span=min(float(req.length),duration-0.05)
        last=max(0.0,duration-span)
        # Even when scene detection is sparse, export the requested number.
        if req.count==1:
            scenes=[float(x) for x in info.get('scene_changes',[]) if 0<=float(x)<=last]
            chosen=[scenes[0] if scenes else 0.0]
        else:
            # Evenly spaced fallback avoids returning fewer than requested.
            chosen=[last*i/2 for i in range(3)]
            if last<0.1:
                raise ValueError('Source must be longer than the selected clip duration to export three distinct clips')
        result=[]
        for t in chosen:
            f=clip_media(source,t,t+span,'vertical')
            result.append({'start':round(t,2),'end':round(t+span,2),'video_url':'/api/clip/'+f.stem})
        return {'clips':result,'method':info['method'],'note':'Scene-assisted for single clip; evenly spaced for three clips. Not AI highlight ranking.'}
    except (ValueError,RuntimeError,OSError) as exc:raise HTTPException(422,str(exc)[-450:])

@app.post('/api/transcribe')
def transcribe(req: TranscribeRequest):
    try:return transcribe_local(owned_source(req.background_name),req.model)
    except Exception as e:raise HTTPException(422,str(e)[-400:])

class EditorRequest(AdvancedSource):
    start:float=Field(default=0,ge=0)
    end:float=Field(gt=0)
    caption:str=Field(default='',max_length=150)
    mute:bool=False

@app.post('/api/simple-editor')
def simple_editor(req: EditorRequest):
    try:f=edit_video(owned_source(req.background_name),req.start,req.end,req.caption,req.mute)
    except Exception as e:raise HTTPException(422,str(e)[-400:])
    return {'id':f.stem,'video_url':'/api/advanced-video/'+f.stem}

@app.post('/api/upload-image')
async def upload_image(file:UploadFile=File(...)):
    from PIL import Image
    if Path(file.filename or '').suffix.lower() not in ('.png','.jpg','.jpeg','.webp'):
        raise HTTPException(400,'Upload JPG, PNG, or WEBP')
    b=await file.read(8*1024*1024+1)
    if len(b)>8*1024*1024:raise HTTPException(413,'Image too large (8 MB maximum)')
    name=uuid.uuid4().hex[:12]
    try:
        from io import BytesIO
        im=Image.open(BytesIO(b));im.verify()
        im=Image.open(BytesIO(b)).convert('RGB')
        im.thumbnail((2048,2048))
        im.save(MEDIA_ROOT/(name+'.jpg'),quality=88)
    except Exception:raise HTTPException(400,'Invalid image file')
    return {'image_id':name}

class ImageMotion(BaseModel):
    image_id:str
    seconds:int=Field(default=8,ge=2,le=30)

@app.post('/api/image-motion')
def image_motion(req:ImageMotion):
    if not re.fullmatch('[0-9a-f]{12}',req.image_id):raise HTTPException(400,'Invalid image id')
    src=MEDIA_ROOT/(req.image_id+'.jpg')
    if not src.is_file():raise HTTPException(404,'Image not found')
    try:f=motion_from_image(src,req.seconds)
    except Exception as e:raise HTTPException(422,str(e)[-400:])
    return {'id':f.stem,'video_url':'/api/advanced-video/'+f.stem, 'kind':'image animation (not AI synthesis)'}

@app.get('/api/advanced-video/{video_id}')
def advanced_video(video_id:str):
    if not re.fullmatch('[0-9a-f]{12}',video_id):raise HTTPException(400,'Invalid id')
    f=MEDIA_ROOT/(video_id+'.mp4')
    if not f.is_file():raise HTTPException(404,'Not found')
    return FileResponse(f,media_type='video/mp4',filename='shadow_studio_'+video_id+'.mp4')

@app.get('/brand-logo')
def brand_logo():
    return FileResponse(ROOT/'brand-logo.jpg',media_type='image/jpeg')


# Optional local image synthesis. Fixed loopback endpoint avoids arbitrary URL requests.
class AIImageRequest(BaseModel):
    prompt:str=Field(min_length=5,max_length=700)
    negative_prompt:str=Field(default='',max_length=500)

@app.post('/api/ai-image')
def ai_image(req:AIImageRequest):
    import base64,requests
    from io import BytesIO
    from PIL import Image
    try:
        response=requests.post('http://127.0.0.1:7860/sdapi/v1/txt2img',json={
            'prompt':req.prompt,'negative_prompt':req.negative_prompt,
            'steps':20,'width':512,'height':768,'batch_size':1,'n_iter':1,
        },timeout=175)
        response.raise_for_status()
        images=response.json().get('images') or []
        if not images:raise ValueError('The local model returned no images')
        data=base64.b64decode(images[0].split(',')[-1],validate=True)
        if len(data)>12*1024*1024:raise ValueError('Generated image too large')
        im=Image.open(BytesIO(data)).convert('RGB')
        im.thumbnail((1536,1536))
        id=uuid.uuid4().hex[:12];im.save(MEDIA_ROOT/(id+'.jpg'),quality=90)
        return {'image_id':id,'image_url':'/api/generated-image/'+id}
    except (requests.exceptions.RequestException,ValueError,KeyError,OSError) as e:
        raise HTTPException(503,'Local Stable Diffusion server unavailable or failed: '+str(e)[-250:])

@app.get('/api/generated-image/{image_id}')
def generated_image(image_id:str):
    if not re.fullmatch('[0-9a-f]{12}',image_id):raise HTTPException(400,'Invalid image')
    file=MEDIA_ROOT/(image_id+'.jpg')
    if not file.is_file():raise HTTPException(404,'Image missing')
    return FileResponse(file,media_type='image/jpeg')

@app.get('/api/integrations')
def integration_status():
    from integration_checks import integrations
    return integrations(probe=False)

@app.post('/api/integrations/check')
def integration_live_check():
    # Owner-initiated; no paid generation, uploads, or YouTube actions.
    from integration_checks import integrations
    return integrations(probe=True)

@app.get('/api/production-capabilities')
def capabilities():
    import importlib.util
    return {'scene_suggestions':True,'simple_editor':True,'transcription_installed':bool(importlib.util.find_spec('faster_whisper')),
            'image_motion':True,'ai_image_generation':'optional local Stable Diffusion WebUI server on 127.0.0.1:7860',
            'ai_video_generation':'not yet implemented', 'publishing':'approved upload kit and token-protected local drafts',
            'chatgpt':'owner token-protected agent API available when configured; direct ChatGPT connector not installed'}

HTML=r'''<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Shadow Studio — Private</title><style>
:root{font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#e9eef9;background:#090d16}*{box-sizing:border-box}body{margin:0}header{padding:23px max(20px,calc((100vw - 1100px)/2));border-bottom:1px solid #293043;background:#101624}h1{margin:0;font-size:26px}header p{margin:5px 0 0;color:#9fabbd}.tag{color:#8dc8ff;font-size:12px;font-weight:700}main{max-width:1100px;margin:28px auto;padding:0 18px;display:grid;grid-template-columns:1fr 1fr;gap:24px}.panel{background:#131b2b;border:1px solid #303b50;border-radius:16px;padding:22px}h2{font-size:18px;margin:0 0 18px}label{display:block;color:#aebbd1;font-size:13px;margin:14px 0 7px}textarea,input,select{width:100%;color:#f2f7ff;background:#0b1120;border:1px solid #374760;border-radius:9px;padding:12px;font:inherit}textarea{min-height:92px;resize:vertical}button,.button{background:#4d90ee;color:#fff;border:0;border-radius:9px;padding:12px 16px;font:inherit;font-weight:700;cursor:pointer;text-decoration:none;display:inline-block}button:disabled{opacity:.5;cursor:not-allowed}.subtle{background:#263b5e}.row{display:flex;gap:10px;flex-wrap:wrap;margin-top:15px}.muted{color:#aab8ce;font-size:13px;line-height:1.55}.line{display:flex;gap:8px;margin:9px 0}.line input:first-child{width:100px;flex-shrink:0}.line input:nth-child(2){flex:1}.line button{background:#3a2635;padding:8px}#lines{max-height:430px;overflow:auto}#status{margin-top:16px;color:#b9cfff}progress{width:100%;height:12px;margin-top:12px}video{width:100%;max-height:540px;background:#000;border-radius:10px}#videoSection{display:none}#editor{display:none}.help{background:#172238;border-left:3px solid #5e9cff;padding:10px 14px;border-radius:6px;margin-top:16px}header{background:radial-gradient(circle at 95% 10%,#203765 0%,#101624 45%);border-bottom:1px solid #334562}header .tag{letter-spacing:.12em}header h1{font-size:31px;letter-spacing:-.04em}nav{display:flex;flex-wrap:wrap;gap:10px;margin-top:22px}nav a{font-size:13px;font-weight:650;color:#d9eaff;text-decoration:none;padding:9px 14px;border:1px solid #354663;border-radius:100px;background:#1c2b45}nav a:hover{background:#365b93}.panel{box-shadow:0 12px 32px #05091524}button:hover:not(:disabled),.button:hover{filter:brightness(1.14)}button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible,a:focus-visible{outline:2px solid #9bc6ff;outline-offset:2px}.library-panel{max-width:1100px;margin:0 auto 25px}.section-head{display:flex;align-items:start;justify-content:space-between;gap:14px;flex-wrap:wrap}.eyebrow{font-size:11px;font-weight:750;color:#88bfff;letter-spacing:.16em}.library-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px;margin-top:16px}.video-tile{background:linear-gradient(145deg,#263e68,#111b32);text-align:left;border:1px solid #35537e;min-height:148px;display:flex;flex-direction:column;align-items:flex-start;justify-content:space-between;gap:8px}.video-tile strong{font-size:14px;line-height:1.4}.video-tile small{font-size:12px;color:#aebfda}.tile-icon{font-size:26px}.video-tile:hover{border-color:#86bfff}#libraryDetails{margin-top:24px;padding-top:22px;border-top:1px solid #34415a;max-width:540px}#libraryDetails[hidden]{display:none}#libraryDetails h3{font-size:16px} @media(max-width:760px){
 main{grid-template-columns:minmax(0,1fr);margin:12px auto;gap:14px;padding:0 12px}
 header{padding:18px 14px}header h1{font-size:26px}nav{gap:7px;margin-top:16px}
 nav a{font-size:12px;padding:9px 11px}.panel{padding:16px;border-radius:14px}
 .line{flex-wrap:wrap;padding:8px 0;border-bottom:1px solid #304057}
 .line input:first-child{width:33%;flex:1}.line input:nth-child(2){width:100%;flex:auto;order:2}
 .line button{margin-left:auto;order:1}.row button{flex:1;min-width:130px}
 .library-panel,#automation-panel{margin-left:12px!important;margin-right:12px!important}
 .library-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
 .video-tile{min-height:135px;padding:12px}.video-tile strong{font-size:12px}
 .section-head{align-items:stretch}#videoSection video,#libraryPlayer{max-height:68vh}
 textarea,input,select{font-size:16px}body{overflow-x:hidden}
}
@media(max-width:380px){.library-grid{grid-template-columns:1fr}header h1{font-size:24px}}
.voice-note{font-size:12px;color:#9cd4ff;margin-top:8px}
#renderBtn{background:linear-gradient(110deg,#4e94f5,#8067ff)}
[data-view-panel][hidden]{display:none!important}
nav .tab{font-size:13px;font-weight:700;background:#1d2940;color:#bbcae1;border:1px solid #34455f;border-radius:11px;padding:11px 17px}
nav .tab.active{background:#4e8cf1;color:#fff;border-color:#79acff}
nav .tab:hover{background:#35527e}
.assist-panel{width:calc(100% - 28px)}
main[data-view-panel="create"]{align-items:start}
@media(max-width:760px){nav{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}nav .tab{font-size:12px;padding:13px 8px} .assist-panel{width:calc(100% - 24px);margin-left:12px!important;margin-right:12px!important}}
/* v0.11 focused production styling */
body{background:radial-gradient(circle at 12% -4%,#192f55 0%,transparent 34%),#090d16}
header{padding-top:30px;padding-bottom:25px;background:linear-gradient(105deg,#141c31 0%,#142741 55%,#101726 100%)}
header h1{font-size:34px;letter-spacing:-.045em}
.panel{background:linear-gradient(155deg,#162239,#111a2b);border-color:#33445e;box-shadow:0 14px 45px #0003}
nav{gap:8px}nav .tab{border-radius:12px}
button,.button{transition:transform .16s ease,filter .16s ease}button:hover:not(:disabled){transform:translateY(-1px)}
#libraryGrid .video-tile{background:linear-gradient(155deg,#263b65,#121d34);border-radius:14px}
#libraryDetails{background:#0c1424;padding:18px;border:1px solid #314158;border-radius:14px}
@media(max-width:760px){header h1{font-size:27px}}
.split-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:700px){.split-grid{grid-template-columns:1fr}}

/* ShadowChronicles brand system: midnight black, merlot, crimson and soft ivory. */
:root{color-scheme:dark;background:#08080b;color:#f7eef1;--ink:#08080b;--panel:#151016;--panel2:#1d131c;--line:#38232c;--accent:#bd264c;--accent2:#e14366;--muted:#ac969e;--glow:rgba(191,33,74,.16)}
body{background:radial-gradient(ellipse 70% 28% at 90% 0%,#38111e 0%,transparent 75%),#08080b;color:#f7eef1}
header{background:linear-gradient(105deg,rgba(12,9,13,.96),rgba(40,11,22,.95))!important;border-bottom:1px solid #592638!important;position:relative;overflow:hidden;padding-top:27px;padding-bottom:22px}
header:after{content:'';position:absolute;right:-60px;top:-140px;width:420px;height:420px;border-radius:50%;border:1px solid rgba(217,52,92,.22);box-shadow:0 0 0 45px rgba(167,29,66,.035),0 0 0 90px rgba(167,29,66,.025);pointer-events:none}
.brandrow{display:flex;align-items:center;gap:18px;position:relative;z-index:1}.brandmark{width:78px;height:78px;border-radius:16px;object-fit:cover;object-position:center;border:1px solid #693044;box-shadow:0 10px 30px #0009}.brandrow h1{font-size:clamp(27px,4vw,40px);letter-spacing:-.035em;line-height:1.1;font-weight:850}.brandrow h1 span{color:#e65379}.brandtag{letter-spacing:.34em!important;font-size:10px!important;color:#ba7e90!important;margin:10px 0 0!important}header>.tag{color:#d1849a!important;font-size:10px!important;letter-spacing:.21em}
header>p{max-width:560px;margin-top:17px!important;color:#bbaab0!important;font-size:13px}
nav{z-index:1;position:relative}nav .tab{background:#1a1119!important;border:1px solid #3e2731!important;color:#c5b1ba!important;border-radius:10px!important;transition:all .2s}nav .tab.active{background:linear-gradient(115deg,#aa1e45,#cf3159)!important;color:#fff!important;border-color:#dc4e70!important;box-shadow:0 6px 20px #ac204633}nav .tab:hover{background:#4b1d30!important}
.panel,.tool-card{background:linear-gradient(160deg,#1b141b,#100e13)!important;border:1px solid #3d2732!important;box-shadow:0 12px 35px #0006!important;border-radius:18px}
h2{letter-spacing:-.025em;font-weight:760}h3{letter-spacing:-.017em}
.eyebrow,.tag{color:#dc6b8c!important}.muted,.voice-note{color:#b9a4ae!important}
input,textarea,select{background:#100e13!important;border:1px solid #553341!important;color:#f5edf1!important;border-radius:10px}input:focus,textarea:focus,select:focus{border-color:#d83b68!important;outline:2px solid #e44d7740!important;outline-offset:1px}
button,.button{background:linear-gradient(125deg,#a61d45,#d3335e)!important;color:white!important;border:1px solid #d34469!important;box-shadow:0 4px 14px #9d1d3830}button.subtle,.subtle{background:#33202b!important;border:1px solid #5a3544!important;box-shadow:none}
button:hover:not(:disabled),.button:hover{filter:brightness(1.16)}.help{background:#261721!important;border-left:3px solid #b82b52!important;color:#ead7df!important}
.video-tile{background:linear-gradient(145deg,#452032,#190f19)!important;border:1px solid #653249!important}.video-tile:hover{border-color:#e25279!important}.video-tile small{color:#bca8b1!important}
#renderBtn{background:linear-gradient(120deg,#a91942,#ed426b)!important}
progress{accent-color:#d1325b}a{color:#ef7d9b}a.button{color:white}
.tool-intro{padding-bottom:18px}.tool-intro h2{font-size:29px;margin:8px 0}.studio-tool-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.tool-card{padding:20px;min-width:0}.tool-card h3{font-size:17px;margin:5px 0}.tool-symbol{font-size:30px;color:#ea6386}.tool-feedback{font-size:12px;color:#f0bed0;margin-top:13px;white-space:pre-wrap;overflow-wrap:anywhere}.tool-feedback video{max-height:300px;margin-top:12px}.mini-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.checkboxline{display:flex;align-items:center;gap:10px}.checkboxline input{width:auto}.tool-card>button{margin-top:14px}.integration-roadmap{display:flex;justify-content:space-between;align-items:center;gap:18px;background:#210f1a;border:1px dashed #78415a;padding:22px;border-radius:16px;margin:20px 0 28px}.integration-roadmap h3{margin:6px 0}.roadmap-pill{font-size:10px;letter-spacing:.13em;font-weight:850;border:1px solid #8c5470;background:#3c2433;color:#efc0d1;border-radius:100px;padding:8px 12px;white-space:nowrap}
@media(max-width:760px){.brandmark{width:54px;height:54px}.brandrow{gap:12px}.brandrow h1{font-size:26px}.studio-tool-grid{grid-template-columns:1fr}.integration-roadmap{flex-direction:column;align-items:start}nav{flex-wrap:nowrap;overflow-x:auto;padding-bottom:10px}nav .tab{flex-shrink:0;font-size:12px!important}}

/* v0.21 — Crimson Pro shell: polished visual system, preserved existing tools. */
:root{--base:#09090d;--card:#17131a;--card2:#1d1720;--stroke:#332630;--rose:#e94c76;--soft:#cbb6be;--white:#fbf5f7;font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
html{scroll-behavior:smooth}body{min-height:100vh;background:radial-gradient(ellipse 55% 32% at 91% -4%,#341522 0,transparent 76%),#0a090d!important;color:var(--white);line-height:1.5}
header{width:248px;min-height:100vh;position:fixed!important;top:0;left:0;z-index:9;overflow-y:auto!important;overflow-x:hidden!important;padding:29px 19px!important;border-right:1px solid #382932!important;border-bottom:0!important;background:linear-gradient(180deg,#1a1119,#0d0b10 65%)!important;box-shadow:12px 0 54px #0003}
header:after{opacity:.27;right:-220px;top:15px}
header .tag{font-size:9px;line-height:1.7;letter-spacing:.12em;color:#d97d98!important;opacity:.95}
.brandrow{gap:10px;margin-top:21px;align-items:center}.brandmark{width:50px!important;height:50px!important;border-radius:13px!important;flex-shrink:0;box-shadow:0 6px 24px #ab1c433a}.brandrow h1{font-size:22px!important;line-height:1.05!important;letter-spacing:-.07em!important}.brandtag{font-size:9px!important;letter-spacing:.19em!important}
header>p{font-size:12px!important;line-height:1.65;color:#a58f99!important;margin:20px 2px 26px!important}
nav{display:flex!important;flex-direction:column;gap:8px!important;margin-top:18px!important}nav .tab{width:100%;min-height:46px;text-align:left;padding:12px 13px!important;font-size:12px!important;font-weight:650!important;white-space:normal!important;background:transparent!important;box-shadow:none!important;border-color:transparent!important;color:#b9a5af!important}nav .tab.active{background:linear-gradient(100deg,#671c36,#a3274d)!important;border:1px solid #ad3758!important;color:#fff!important;box-shadow:inset 3px 0 #ff648c,0 7px 20px #0005!important}nav .tab:hover:not(.active){background:#2a1b27!important}
main[data-view-panel="create"]{margin:0 0 0 248px!important;max-width:none!important;padding:36px clamp(22px,4.3vw,70px) 65px!important;gap:21px!important;grid-template-columns:minmax(0,1.15fr) minmax(330px,.85fr)!important}
body>section[data-view-panel],body>div[data-view-panel]{margin-left:calc(248px + clamp(22px,4.3vw,70px))!important;margin-right:clamp(22px,4.3vw,70px)!important;max-width:none!important;width:auto!important}
.panel,.tool-card{background:linear-gradient(157deg,#1b151d,#100e13)!important;border:1px solid #392b34!important;border-radius:19px!important;box-shadow:0 15px 50px #0004!important;padding:25px!important}
.panel h2{font-size:20px;letter-spacing:-.04em;margin-bottom:14px}h3{letter-spacing:-.035em}
label{font-size:12px!important;font-weight:680!important;color:#c6b1ba!important;margin-top:19px!important}
input,select,textarea{border:1px solid #493341!important;background:#120f15!important;border-radius:11px!important;padding:13px 14px!important;color:#faf5f8!important}textarea{line-height:1.55}
button,.button{border-radius:11px!important;font-size:13px!important;letter-spacing:.003em;padding:12px 16px!important;box-shadow:0 6px 20px #9f18412b!important}button.subtle{background:#2c202c!important;border-color:#4d3541!important}
button:focus-visible,a:focus-visible{outline:2px solid #ff9ab5!important;outline-offset:3px}
.help{border-radius:11px!important;background:#261923!important;border:1px solid #49313e!important;border-left:3px solid #d53b68!important;padding:17px!important}
.muted{color:#b3a1a9!important;font-size:12.5px!important}.eyebrow{font-size:10px!important;font-weight:850;letter-spacing:.2em}.row{gap:10px}.line input:nth-child(2){min-width:0}
#videoSection video,#libraryPlayer,video{border:1px solid #4c3341;border-radius:15px!important;max-height:68vh;background:#030304}
#libraryGrid .video-tile{background:linear-gradient(145deg,#321b2b,#171019)!important;border:1px solid #523448!important;min-height:165px;transition:transform .18s,border .18s}#libraryGrid .video-tile:hover{transform:translateY(-3px);border-color:#ef7098!important}
.tool-card{padding:22px!important}.tool-symbol{font-size:28px!important}.studio-tool-grid{gap:16px!important}
.launch-hero{grid-column:1/-1;border:1px solid #553345;border-radius:22px;background:radial-gradient(circle at 90% 15%,#61213d 0,transparent 40%),linear-gradient(125deg,#261521,#130e16 65%);padding:28px 31px;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:24px;align-items:center;position:relative;overflow:hidden;box-shadow:0 19px 57px #0006}
.launch-hero:after{content:"";pointer-events:none;position:absolute;width:240px;height:240px;border-radius:50%;border:1px solid #b73e6a30;top:-135px;right:-45px;box-shadow:0 0 0 48px #b73e6a10,0 0 0 95px #b73e6a08}
.launch-hero h2{font-size:clamp(25px,3.25vw,38px);line-height:1.12;letter-spacing:-.06em;margin:9px 0 12px}.launch-hero p{font-size:13px;line-height:1.8;color:#c6abb7;max-width:620px;margin:0}.launch-hero .hero-actions{display:flex;gap:9px;flex-wrap:wrap;justify-content:flex-end;z-index:1}
.launch-metrics{grid-column:1/-1;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.metric{padding:17px 19px;border:1px solid #372630;background:#151118;border-radius:15px}.metric strong{font-size:27px;line-height:1.1;display:block;letter-spacing:-.055em;margin-top:5px}.metric span{color:#bdabb4;font-size:11px;letter-spacing:.03em}.metric small{display:block;color:#9b8a92;font-size:11px;margin-top:6px}
.launch-checks{grid-column:1/-1;background:#151118;border:1px solid #3a2833;border-radius:15px;padding:17px 20px;display:none}.launch-checks.show{display:block}.launch-checks h3{margin:0 0 10px;font-size:14px}.check-chip{display:inline-flex;align-items:center;gap:8px;border:1px solid #473440;border-radius:9px;padding:8px 11px;margin:5px 7px 5px 0;font-size:11px}.check-chip.ok{color:#aee7bf;border-color:#355643}.check-chip.no{color:#ffb4be;border-color:#69364a}
@media(max-width:1100px){header{width:210px}main[data-view-panel="create"]{margin-left:210px!important;grid-template-columns:1fr!important;padding:25px!important}body>section[data-view-panel],body>div[data-view-panel]{margin-left:235px!important;margin-right:25px!important}.launch-hero{grid-template-columns:1fr}.launch-hero .hero-actions{justify-content:flex-start}}
@media(max-width:760px){header{width:100%!important;min-height:auto!important;position:relative!important;padding:17px 16px 12px!important;border-right:none!important;border-bottom:1px solid #452a39!important}.brandrow{margin-top:9px}.brandmark{width:43px!important;height:43px!important}.brandrow h1{font-size:22px!important}header .tag{font-size:9px}header>p{margin:10px 0!important;font-size:11px!important}nav{display:flex!important;flex-direction:row;flex-wrap:nowrap!important;gap:7px!important;overflow-x:auto!important;margin:12px -4px 0!important;padding:0 4px 9px!important;scrollbar-width:thin}nav .tab{width:auto!important;min-height:39px;flex-shrink:0;white-space:nowrap!important;text-align:center;padding:10px 11px!important;font-size:11px!important}main[data-view-panel="create"]{margin-left:0!important;padding:17px 13px 40px!important;gap:13px!important}body>section[data-view-panel],body>div[data-view-panel]{margin:0 13px 25px!important;width:auto!important}.panel,.tool-card{padding:18px!important;border-radius:15px!important}.launch-hero{padding:22px 18px;gap:13px;border-radius:16px}.launch-hero h2{font-size:28px}.launch-metrics{gap:8px}.metric{padding:13px 10px}.metric strong{font-size:22px}.metric small{font-size:10px}.studio-tool-grid{grid-template-columns:1fr!important}}
@media(max-width:400px){.launch-metrics{grid-template-columns:1fr 1fr}.metric:last-child{grid-column:1/-1}.row button{min-width:110px}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
.integration-health-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin-top:18px}.integration-health-card{background:linear-gradient(145deg,#20121d,#111019);border:1px solid #614056;border-radius:14px;padding:16px}.integration-health-card h3{font-size:14px;margin:0 0 10px;color:#fff}.integration-health-card p{font-size:12px;color:#d5b6c2;line-height:1.5}.integration-health-card small{font-size:12px;color:#cf8ca3}.integration-health-state{font-size:11px;color:#f9c7d8;font-weight:750;letter-spacing:.04em}</style></head><body>
<header><div class="tag">SHADOWCHRONICLES • PRIVATE WORKSPACE • v0.32</div><div class="brandrow"><img src="/brand-logo" alt="ShadowChronicles logo" class="brandmark"><div><h1>SHADOW <span>STUDIO</span></h1><p class="brandtag">THE CONTENT ENGINE</p></div></div><p>Your creative command center. Draft, produce, review and prepare Shorts — all in one place.</p><nav aria-label="Workspace sections"><button type="button" class="tab active" data-view="create" onclick="switchView('create')">✦ &nbsp; Create</button><button type="button" class="tab" data-view="library" onclick="switchView('library')">▣ &nbsp; Review library</button><button type="button" class="tab" data-view="automation" onclick="switchView('automation')">◷ &nbsp; Automation</button><button type="button" class="tab" data-view="tools" onclick="switchView('tools')">◈ &nbsp; Tools & Editor</button><button type="button" class="tab" data-view="assistant" onclick="switchView('assistant')">↗ &nbsp; Publishing</button></nav></header><main data-view-panel="create"><section class="launch-hero" aria-label="Production overview"><div><span class="eyebrow">SHADOWCHRONICLES / CREATOR DESK</span><h2>Make something<br><span style="color:#fc6993">worth watching.</span></h2><p>Everything you need to produce your next short-form story: original scripts, natural voices, your gameplay, and a controlled review-to-publishing workflow.</p></div><div class="hero-actions"><button type="button" onclick="document.getElementById('idea').focus()">+ New story</button><button type="button" class="subtle" onclick="switchView('library')">Review videos →</button></div></section><div class="launch-metrics"><div class="metric"><span>RENDERED SHORTS</span><strong id="metricRendered">—</strong><small>Stored on this computer</small></div><div class="metric"><span>APPROVED</span><strong id="metricApproved">—</strong><small>Passed review checks</small></div><div class="metric"><span>AWAITING REVIEW</span><strong id="metricPending">—</strong><small>Ready for your decision</small></div></div><section class="launch-checks" id="launchChecks"><h3>Production system check</h3><div id="launchCheckItems" aria-live="polite"></div><p class="muted" id="launchCheckNote"></p><button class="subtle" onclick="checkProductionReadiness()">↻ Run checks again</button></section><section class="panel" id="create-panel"><span class="eyebrow">01 / STORY WORKSHOP</span><h2>Create your story</h2><div class="help"><strong>Quick start — three ready-to-edit originals</strong><p class="muted">Want to upload multiple Shorts tonight? Load a complete story below, preview a neural voice, then render.</p><div id="starterList" class="row"></div></div><label for="idea">Story idea</label><textarea id="idea">My friend sent me a screenshot that changed everything about my relationship</textarea><label for="style">Video style</label><select id="style"><option value="messages">Animated text message story</option><option value="reddit">Reddit-style story</option></select><label for="target">Target story length (approximate)</label><select id="target"><option value="30">30 seconds</option><option value="45" selected>45 seconds</option><option value="60">60 seconds</option></select><label for="genre">Genre for random stories</label><select id="genre"><option value="random">Random</option><option value="horror">Horror</option><option value="drama">Drama</option><option value="revenge">Revenge</option><option value="mystery">Mystery</option><option value="comedy">Comedy</option></select><div class="row"><button onclick="createStory()" id="storyBtn">Generate from idea</button><button class="subtle" onclick="createStory(true)" id="randomStoryBtn">Generate random story</button></div><p class="muted">Random stories remix original offline outlines. Generate from idea uses local Ollama when configured, with a template fallback. Actual video length depends on the voice and pauses.</p><div id="editor"><h2>2. Review & edit script</h2><label for="title">Video title</label><input id="title" maxlength="95"><div id="lines"></div><button class="subtle" onclick="addLine()">+ Add dialogue line</button><div class="row"><button class="subtle" onclick="saveProject()">Save project</button><button class="subtle" onclick="refreshProjects()">Refresh saved projects</button></div><label>Saved projects</label><select id="projects" onchange="loadProject()"><option value="">Choose a project...</option></select></div></section><section class="panel" id="production-panel"><span class="eyebrow">02 / PRODUCTION SETTINGS</span><h2>Produce your video</h2><label for="voice">Narrator voice</label><select id="voice"><option value="en-US-AriaNeural">Aria — conversational woman (US)</option><option value="en-US-JennyNeural">Jenny — friendly woman (US)</option><option value="en-US-MichelleNeural">Michelle — warm woman (US)</option><option value="en-US-GuyNeural">Guy — storyteller man (US)</option><option value="en-US-DavisNeural">Davis — deeper man (US)</option><option value="en-US-ChristopherNeural">Christopher — announcer man (US)</option><option value="en-GB-SoniaNeural">Sonia — woman (British)</option><option value="en-GB-RyanNeural">Ryan — man (British)</option><option value="windows-default">Windows voice — offline fallback</option></select><p class="muted">For free tests, select Aria, Jenny, or Michelle and preview before rendering. Matilda is your preferred vidIQ voice, but does not automatically transfer to this local app. An authorized ElevenLabs API key is required for premium voices; those requests may be billed separately.</p><label for="secondVoice">Second character voice</label><select id="secondVoice"><option value="en-US-GuyNeural">Guy — storyteller man (US)</option><option value="en-US-DavisNeural">Davis — deeper man (US)</option><option value="en-US-JennyNeural">Jenny — friendly woman (US)</option><option value="en-US-AriaNeural">Aria — conversational woman (US)</option></select><p class="muted">The first speaker uses the main voice; every other speaker uses this second voice. Choose the same voice only if you want identical narration.</p><label for="voiceRate">Speaking speed</label><select id="voiceRate"><option value="-15">Slower</option><option value="0" selected>Normal</option><option value="10">A little faster</option><option value="20">Fast</option></select><div class="row"><button class="subtle" id="previewVoiceBtn" onclick="previewVoice()">▶ Preview main voice</button><button class="subtle" onclick="previewVoice(true)">▶ Preview second voice</button></div><audio id="voicePreview" controls preload="none" style="display:none;width:100%;margin-top:12px"></audio><div class="voice-note" id="voiceAvailability">Voice studio: loading...</div><p class="muted">Natural voices are the default and require internet. If a natural voice is unavailable, the render stops with an error instead of silently switching to a robotic voice. Always preview before rendering.</p><label for="resolution">Export resolution (30 FPS)</label><select id="resolution"><option value="720p" selected>720 × 1280 — faster</option><option value="1080p">1080 × 1920 — sharper export</option></select><p class="muted">Current designs are drawn at 540 × 960 and upscaled, so 1080p does not add source detail.</p><label for="gameplayChoice">Gameplay library (only clips you own or may use)</label><select id="gameplayChoice"><option value="">No saved gameplay</option><option value="auto">Automatic — rotate saved clips</option></select><div class="row"><button class="subtle" onclick="refreshGameplay()">Refresh gameplay library</button></div><label for="gameplayFile">Add gameplay to library</label><input id="gameplayFile" type="file" accept="video/mp4,video/quicktime,video/webm,video/x-matroska"><label for="gameplayTag">Game or category</label><input id="gameplayTag" placeholder="Minecraft parkour, GTA driving, etc."><div class="row"><button class="subtle" onclick="saveGameplay()" id="gameplaySave">Save gameplay</button></div><p class="muted" id="gameplayStatus">Saved footage is kept on this PC and reused across Shorts. Dialogue appears above gameplay, which fills the lower 58% of the video.</p><label for="background">Or upload gameplay for just this video</label><input id="background" type="file" accept="video/mp4,video/quicktime,video/webm,video/x-matroska"><p class="muted">Leave both blank for the original dark story layout. Saved footage takes priority over the one-time upload.</p><button onclick="renderVideo()" id="renderBtn" disabled>Render MP4</button><div id="status">Generate a script to get started.</div><progress id="progress" max="100" value="0" style="display:none"></progress><div id="videoSection"><h2>4. Preview & download</h2><video id="preview" controls playsinline></video><div class="row"><a class="button" id="download" download>Download MP4</a><a class="button subtle" id="downloadCaptions" download>Download timed captions</a></div><label for="description">YouTube description (copy after reviewing)</label><textarea id="description" rows="7" readonly></textarea><div class="row"><button class="subtle" onclick="navigator.clipboard.writeText(document.getElementById('description').value)">Copy description</button></div><div class="help muted">Publishing requires separate approval. Inspect the finished video and upload it to YouTube Studio, or use your connected vidIQ workflow after approval.</div></div><div class="help muted" id="health">Checking FFmpeg...</div></section></main><section class="panel" id="integration-center" data-view-panel="assistant" hidden style="max-width:1000px;margin:24px auto"><span class="eyebrow">CONNECTIVITY / PRIVATE BETA</span><h2>Integration health center</h2><p class="muted">See which tools are configured, then run a read-only connection test. No keys are shown, no credits are used, no files are sent and nothing gets published. A green connection does not guarantee successful generation.</p><div class="row"><button type="button" class="subtle" onclick="refreshIntegrationStatus(false)">Refresh setup</button><button type="button" onclick="refreshIntegrationStatus(true)">Test provider connections</button></div><div id="integrationStatus" class="integration-health-grid" role="status" aria-live="polite">Checking available integrations…</div><section class="help" style="margin-top:18px"><h3>Connect ElevenLabs</h3><p class="muted">Use your own ElevenLabs API key. We verify it with ElevenLabs first, then store it only in the local .env file. Previewing or rendering premium narration may consume ElevenLabs credits.</p><label for="elevenKey">ElevenLabs API key</label><input id="elevenKey" type="password" autocomplete="off" placeholder="Paste your ElevenLabs API key"><button type="button" onclick="connectElevenLabs()">Connect ElevenLabs</button><div class="row"><select id="elevenVoices" aria-label="ElevenLabs voice"><option value="">Connect account to list voices</option></select><button type="button" class="subtle" onclick="saveElevenVoice()">Use selected voice</button></div><p id="elevenFeedback" class="muted">Not tested with your account yet.</p></section><section class="help" style="margin-top:18px"><h3>App Updates</h3><p class="muted">Check for verified updates without downloading a ZIP manually. Configure SHADOW_UPDATE_FEED once in .env; close the app and run update_windows.bat to install. Your work stays on this PC.</p><button type="button" class="subtle" onclick="checkShadowUpdates()">Check for updates</button><p id="shadowUpdateStatus" class="muted">Update source not yet checked.</p></section></section><section class="panel library-panel" id="library-panel" data-view-panel="library"><div class="section-head"><div><span class="eyebrow">YOUR WORKSPACE</span><h2>Review Queue & Video Library</h2><p class="muted">Review videos, approve the ones you want to publish, and download an upload kit containing MP4s and descriptions.</p></div><button class="subtle" onclick="refreshLibrary()">↻ Refresh library</button></div><div id="libraryEmpty" class="muted">Loading your completed videos…</div><div id="libraryGrid" class="library-grid"></div><div class="row"><a class="button" href="/api/publishing-kit" download>↓ Download approved upload kit</a></div><div id="libraryDetails" hidden><h3 id="libraryTitle"></h3><video id="libraryPlayer" controls playsinline preload="metadata"></video><label for="reviewState">Review decision</label><select id="reviewState"><option value="needs_review">Needs review</option><option value="needs_changes">Needs changes</option><option value="approved">Approved for upload kit</option></select><label for="reviewNotes">Review notes</label><textarea id="reviewNotes" rows="2" placeholder="Optional notes before publishing..."></textarea><div class="row"><button class="subtle" onclick="runQualityCheck()">Run video quality check</button><button id="saveReviewBtn" onclick="saveReview()">Save review decision</button></div><div class="help" id="qualityPanel" role="status">Before approving, run a technical check. A passing check does not replace watching the complete video.</div><p class="muted" id="reviewFeedback">Approval only adds the video to your upload kit. Nothing is sent to YouTube.</p><label for="libraryDescription">YouTube description</label><textarea id="libraryDescription" readonly rows="5"></textarea><div class="row"><a id="libraryDownload" class="button" download>Download video</a><a id="subtitleDownload" class="button subtle" download hidden>↓ Download captions (.srt)</a><a id="handoffDownload" class="button" download>↓ ChatGPT + vidIQ handoff ZIP</a><button class="subtle" id="cloudHandoffBtn" onclick="createCloudHandoff()">☁ Generate private video link</button><button class="subtle" onclick="navigator.clipboard.writeText(el('libraryDescription').value)">Copy description</button></div><div id="cloudHandoffResult" class="help muted" hidden></div></div></section><section class="panel" data-view-panel="tools" id="split-panel" style="max-width:860px;margin:24px auto" hidden>

<div class="tool-intro"><span class="eyebrow">PRODUCTION SUITE / 04</span><h2>Cut. Caption. Create.</h2><p class="muted">A focused set of real tools for your own clips. Upload a source once per tool; every export remains on your computer. AI features that require extra engines are clearly labeled.</p></div>
<div class="studio-tool-grid">
<section class="tool-card"><div class="tool-symbol">✦</div><h3>Auto Clip Suggestions</h3><p class="muted">Detect scene changes and suggest short-form cut points; no promise of AI viral rankings.</p><label>Source video</label><input id="sceneFile" type="file" accept="video/*"><label for="sceneUrl">Or YouTube / Twitch video URL (with permission)</label><input type="url" id="sceneUrl" placeholder="https://www.youtube.com/watch?v=..."><label class="muted"><input type="checkbox" id="sceneRights" style="width:auto"> I own or have permission to reuse this footage</label><label for="sceneMode">Clip length</label><select id="sceneMode"><option value="single">One clip — up to 30 seconds</option><option value="triple" selected>Three clips — up to 10 seconds each</option></select><label for="sceneLength">Seconds per clip</label><input id="sceneLength" type="number" min="3" max="10" value="10"><div class="row"><button onclick="analyzeScenes()">Analyze footage</button><button class="subtle" onclick="exportAutoClips()">Export selected clips</button></div><div id="sceneOutput" class="tool-feedback"></div></section>
<section class="tool-card"><div class="tool-symbol">≋</div><h3>Video Transcriber</h3><p class="muted">Offline speech-to-text powered by optional faster-whisper. CPU mode works without NVIDIA configuration.</p><label>Video to transcribe</label><input id="transcribeFile" type="file" accept="video/*"><button onclick="transcribeVideo()">Transcribe audio</button><div id="transcriptOutput" class="tool-feedback"></div></section>
<section class="tool-card"><div class="tool-symbol">▣</div><h3>Simple Editor</h3><p class="muted">Trim, reframe vertical, add a styled caption card, and mute original audio.</p><input id="editFile" type="file" accept="video/*"><div class="mini-grid"><div><label>Start (sec)</label><input type="number" id="editStart" value="0" min="0"></div><div><label>End (sec)</label><input type="number" id="editEnd" value="10" min=".1"></div></div><label>Caption overlay</label><input id="editCaption" placeholder="The secret was in the screenshot..."><label class="checkboxline"><input type="checkbox" id="editMute"> Mute source audio</label><button onclick="editVideo()">Render edit</button><div id="editOutput" class="tool-feedback"></div></section>
<section class="tool-card"><div class="tool-symbol">◈</div><h3>Image Motion</h3><p class="muted">Animate your own AI-generated or other permitted image with subtle cinematic movement. This is an image animator, not a text-to-video AI model.</p><input id="motionFile" type="file" accept="image/*"><label>Length (seconds)</label><input type="number" id="motionSeconds" value="8" min="2" max="30"><button onclick="animateImage()">Animate image</button><div id="motionOutput" class="tool-feedback"></div></section>

<section class="tool-card"><div class="tool-symbol">✧</div><h3>AI Image Studio</h3><p class="muted">Generate original scenes using your optional local Stable Diffusion WebUI installation. Requires a separate model and its API mode; no cloud service is contacted by Shadow Studio.</p><label>Image prompt</label><textarea id="aiImagePrompt" placeholder="A noir detective in a crimson-lit alley, cinematic illustration, 9:16" rows="3"></textarea><button onclick="generateAIImage()">Generate with local AI</button><div id="aiImageOutput" class="tool-feedback"></div></section>
</div>
<div class="integration-roadmap"><div><span class="eyebrow">PLANNED INTEGRATIONS</span><h3>Connect your production stack</h3><p class="muted">AI images can use your own local Stable Diffusion installation. Advanced prompt-to-video and YouTube publishing still need separate authenticated integrations. A separate read-only MCP connector is included for approved videos but requires account authorization. No fake Generate or Publish buttons.</p></div><span class="roadmap-pill">NOT CONNECTED</span></div>
<hr style="border:0;border-top:1px solid #392632;margin:28px 0">
<span class="eyebrow">EXISTING • VIDEO TOOLS</span><h2>Split Screen Studio</h2>
<p class="muted">Combine two clips you own or have permission to use. Choose stacked or side-by-side; both clips loop to fill your selected duration. Export as vertical 720×1280 at 30 FPS. Audio is muted.</p>
<div class="split-grid"><div><label for="splitTop">First video</label><input type="file" id="splitTop" accept="video/*"></div><div><label for="splitBottom">Second video</label><input type="file" id="splitBottom" accept="video/*"></div></div>
<label for="splitLayout">Layout</label><select id="splitLayout"><option value="stacked">Stacked — top & bottom</option><option value="side_by_side">Side by side — left & right</option></select>
<label for="splitDuration">Duration (seconds, 3–120)</label><input id="splitDuration" type="number" min="3" max="120" value="15">
<div class="row"><button id="splitButton" onclick="makeSplitScreen()">Create split-screen MP4</button></div><p class="muted" id="splitStatus">Choose two files to begin. Uploads are stored locally on your PC.</p>
<div id="splitResult" hidden><video id="splitPlayer" controls playsinline preload="metadata"></video><div class="row"><a class="button" id="splitDownload" download>Download split screen</a></div></div>
<hr style="border:0;border-top:1px solid #303b50;margin:28px 0">
<span class="eyebrow">NEW • RANKING VIDEO MAKER</span><h2>Video Ranking Studio</h2>
<p class="muted">Upload 2–5 of your own clips, list them from #1 (best) downward, and export a vertical countdown. The video plays from the lowest rank up to #1. Clips loop and are muted; no copyrighted music is added.</p>
<label for="rankFiles">Choose 2–5 videos (hold Ctrl to select multiple)</label><input id="rankFiles" type="file" accept="video/*" multiple onchange="fillRanks()">
<div id="rankInputs" class="muted">Choose footage to name each rank.</div>
<label for="rankSeconds">Seconds per ranked clip</label><select id="rankSeconds"><option value="3">3 seconds</option><option value="4" selected>4 seconds</option><option value="6">6 seconds</option><option value="8">8 seconds</option></select>
<div class="row"><button id="rankButton" onclick="makeRanking()">Create ranked Short</button></div><p class="muted" id="rankStatus">Waiting for clips.</p>
<div id="rankResult" hidden><video id="rankPlayer" controls playsinline preload="metadata"></video><div class="row"><a id="rankDownload" class="button" download>Download ranking MP4</a></div></div>
</section><section class="panel" id="automation-panel" data-view-panel="automation" style="max-width:1100px;margin:0 auto 30px"><h2>5. Automation & Media Tools</h2><p class="muted">Batch-create up to 8 Shorts sequentially. Automatic gameplay rotation uses the saved library selection above. This uses your PC; neural narration requires an internet connection.</p><label>Batch story ideas — one idea per line</label><textarea id="batchIdeas" placeholder="My friend found a secret note...&#10;The wedding invitation had the wrong name..." rows="4"></textarea><div class="help"><strong>Tonight’s quick production: three complete original stories</strong><p class="muted">Uses the voice, style, export resolution and gameplay library selected in Create. Preview a neural voice first; this can take several minutes and may use paid voice credits.</p><button id="readyBatchBtn" onclick="startReadyBatch()">▶ Render all 3 ready stories</button></div><div class="row"><button onclick="startBatch()" id="batchBtn">Generate batch</button><button class="subtle" onclick="refreshJobs()">Refresh jobs</button></div><div id="batchStatus" class="muted"></div><div id="jobList"></div><hr style="border:0;border-top:1px solid #303b50;margin:26px 0"><h2 id="clipper-panel">6. Basic video clipper</h2><p class="muted">Trim a video you own or have permission to use. This is a manual clipper, not AI highlight detection.</p><label>Source video</label><input type="file" id="clipFile" accept="video/*"><label for="clipUrl">Or public YouTube / Twitch URL</label><input type="url" id="clipUrl" placeholder="https://clips.twitch.tv/..."><label class="muted"><input type="checkbox" id="clipRights" style="width:auto"> I have the right to use this footage</label><div class="row"><div><label>Start second</label><input type="number" id="clipStart" min="0" step="0.1" value="0"></div><div><label>End second</label><input type="number" id="clipEnd" min="0.1" step="0.1" value="30"></div></div><div class="row"><button onclick="createClip()" id="clipBtn">Make vertical clip</button></div><div id="clipStatus" class="muted"></div><div id="clipLink"></div><div class="help muted">Nothing is uploaded to YouTube automatically. Videos need your approval and a connected publishing integration.</div></section><section class="panel assist-panel" data-view-panel="assistant" id="assistant-panel" style="max-width:1100px;margin:0 auto 30px"><span class="eyebrow">OWNER APPROVAL</span><h2>ChatGPT + YouTube handoff</h2><p class="muted">Use ChatGPT to improve stories, write titles and descriptions, and prepare publishing drafts. With private storage configured, Shadow Studio can upload approved videos automatically and prepare a direct import link for ChatGPT + vidIQ. Final draft approval and publishing are always separate.</p><label for="assistTitle">Video title</label><input id="assistTitle" placeholder="Choose a finished video from Library, or enter a title"><label for="assistDesc">Description</label><textarea id="assistDesc" placeholder="Paste or edit the video description"></textarea><label for="assistNotes">What should ChatGPT help with?</label><select id="assistNotes"><option value="Review this video and improve its title, description, and hook">Review and optimize</option><option value="Prepare a YouTube Shorts publishing draft for ShadowChronicles and show it to me for approval">Prepare publishing draft</option><option value="Suggest improvements to the next three episodes">Plan follow-up stories</option></select><div class="row"><button onclick="copyAssistantRequest()">Copy request for ChatGPT</button><button class="subtle" onclick="switchView('library')">Choose video from Library</button></div><div class="help muted" id="assistStatus">To send a completed MP4 to ChatGPT, attach the downloaded file in your chat after copying the request. A separate read-only private MCP connector is included in v0.20. It must be configured and connected from ChatGPT on web; it does not send messages into this conversation automatically.</div><div class="help"><strong>New: video handoff to ChatGPT + vidIQ</strong><p>Open a completed video in Library, run its quality check, and mark it approved. Download its ChatGPT + vidIQ handoff ZIP, then attach the MP4 and paste the included request into this chat. ChatGPT can prepare a publishing draft; publishing still requires a separate approval.</p><p class="muted">Optional private link handoff: configure S3-compatible storage in .env, then approve a video and click Generate private video link. Or enable auto-upload on approval. No tokens are included in any exported package.</p></div><h3 style="margin-top:26px">Bring a ChatGPT story into Shadow Studio</h3><p class="muted">Ask ChatGPT to write a story as JSON using this format: <code>{"title":"...","lines":[{"speaker":"Me","text":"..."}]}</code>. Paste its response here, then import the editable dialogue into Create.</p><textarea id="assistantImport" placeholder='{"title":"My shocking story","lines":[{"speaker":"Me","text":"You won’t believe this..."}]}'></textarea><div class="row"><button class="subtle" onclick="importAssistantStory()">Import story JSON</button></div><h3 style="margin-top:26px">Publishing draft queue</h3><p class="muted">Select a finished video in Library, approve it after the quality check, then return here to stage its title and description. Nothing will upload to YouTube.</p><label for="draftVideoId">Approved video ID</label><input id="draftVideoId" placeholder="Choose a video in Library first" readonly><label for="draftPrivacy">Desired YouTube visibility</label><select id="draftPrivacy"><option value="private">Private</option><option value="unlisted">Unlisted</option><option value="public">Public — still a draft only</option></select><div class="row"><button onclick="stagePublishingDraft()">Save publishing draft</button><button class="subtle" onclick="refreshPublishingDrafts()">Refresh drafts</button></div><div class="help" id="draftFeedback">Drafts are stored locally; no platform account is connected.</div><div id="draftQueue" class="muted"></div><h3 style="margin-top:26px">Publishing safety</h3><p class="muted">Draft → Review → Explicit approval → Publish using your connected YouTube/vidIQ service. No auto-publication or stored YouTube credentials in this release.</p></section><script>



async function generateAIImage(){try{renderToolOutput('aiImageOutput','Contacting local image model...');const d=await api('/api/ai-image',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:el('aiImagePrompt').value})});const root=el('aiImageOutput');root.replaceChildren();const img=document.createElement('img');img.src=d.image_url;img.alt='Generated illustration';img.style='width:100%;max-width:320px;border-radius:12px';root.append(img);const note=document.createElement('p');note.textContent='Image generated on your computer. Download or animate it using Image Motion.';root.append(note);const a=document.createElement('a');a.href=d.image_url;a.textContent='Open generated image';a.target='_blank';root.append(a)}catch(e){renderToolOutput('aiImageOutput','Generator unavailable: '+e.message+'. Install and run Stable Diffusion WebUI with API enabled first.')}}
async function toolUpload(element){const f=el(element).files[0];if(!f)throw new Error('Choose a video file first');const form=new FormData();form.append('file',f);return await api('/api/upload-background',{method:'POST',body:form})}
function renderToolOutput(id,message,url){let root=el(id);root.replaceChildren();const p=document.createElement('p');p.textContent=message;root.append(p);if(url){const a=document.createElement('a');a.href=url;a.textContent='Download MP4';a.className='button';a.setAttribute('download','');root.append(a);const v=document.createElement('video');v.src=url;v.controls=true;v.playsInline=true;v.preload='metadata';root.append(v)}}
async function clipSource(fileId,urlId,rightsId){const f=el(fileId).files[0];if(f){const form=new FormData();form.append('file',f);return await api('/api/upload-background',{method:'POST',body:form});}const url=el(urlId).value.trim();if(!url)throw Error('Upload a video or enter a YouTube / Twitch link');return await api('/api/import-video-url',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,rights_confirmed:el(rightsId).checked})});}
async function analyzeScenes(){try{renderToolOutput('sceneOutput','Uploading and analyzing video...');const u=await clipSource('sceneFile','sceneUrl','sceneRights');const r=await api('/api/scene-suggestions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({background_name:u.name})});renderToolOutput('sceneOutput',r.scene_changes.length?'Suggested cut positions: '+r.scene_changes.map(t=>t+'s').join(', '):'No scene changes found. You may trim clips manually.')}catch(e){renderToolOutput('sceneOutput','Error: '+e.message)}}
async function exportAutoClips(){try{const mode=el('sceneMode').value;const length=Number(el('sceneLength').value);if(!Number.isInteger(length)||length<3||length>(mode==='single'?30:10))throw Error('Duration is outside the selected limit');renderToolOutput('sceneOutput','Importing and preparing your clips...');const u=await clipSource('sceneFile','sceneUrl','sceneRights');const r=await api('/api/auto-clips',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({background_name:u.name,length,count:mode==='single'?1:3})});const root=el('sceneOutput');root.replaceChildren();for(const clip of r.clips){const a=document.createElement('a');a.className='button';a.href=clip.video_url;a.download='';a.textContent='Download clip '+clip.start+'s – '+clip.end+'s';root.append(a)}const p=document.createElement('p');p.textContent=r.note;root.append(p)}catch(e){renderToolOutput('sceneOutput','Error: '+e.message)}}
async function transcribeVideo(){try{renderToolOutput('transcriptOutput','Transcribing (optional model may need installation)...');const u=await toolUpload('transcribeFile');const r=await api('/api/transcribe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({background_name:u.name,model:'tiny'})});renderToolOutput('transcriptOutput',r.text||'No speech detected')}catch(e){renderToolOutput('transcriptOutput','Error: '+e.message)}}
async function editVideo(){try{renderToolOutput('editOutput','Rendering your edit...');const u=await toolUpload('editFile');const r=await api('/api/simple-editor',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({background_name:u.name,start:Number(el('editStart').value),end:Number(el('editEnd').value),caption:el('editCaption').value,mute:el('editMute').checked})});renderToolOutput('editOutput','Edit complete. Review before sharing.',r.video_url)}catch(e){renderToolOutput('editOutput','Error: '+e.message)}}
async function animateImage(){try{renderToolOutput('motionOutput','Processing image...');const f=el('motionFile').files[0];if(!f)throw new Error('Choose an image');const form=new FormData();form.append('file',f);const u=await api('/api/upload-image',{method:'POST',body:form});const r=await api('/api/image-motion',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image_id:u.image_id,seconds:Number(el('motionSeconds').value)})});renderToolOutput('motionOutput','Animation ready (not AI video generation).',r.video_url)}catch(e){renderToolOutput('motionOutput','Error: '+e.message)}}

async function splitUpload(file){
 const data=new FormData();data.append('file',file);
 const res=await api('/api/upload-background',{method:'POST',body:data});return res.name;
}
async function makeSplitScreen(){
 const a=el('splitTop').files[0],b=el('splitBottom').files[0],button=el('splitButton'),status=el('splitStatus');
 if(!a||!b){status.textContent='Please select two video files.';return}
 button.disabled=true;el('splitResult').hidden=true;
 try{
  status.textContent='Uploading first video…';const top=await splitUpload(a);
  status.textContent='Uploading second video…';const bottom=await splitUpload(b);
  status.textContent='Rendering split-screen video…';
  const result=await api('/api/split-screen',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({top_name:top,bottom_name:bottom,seconds:Number(el('splitDuration').value),layout:el('splitLayout').value})});
  el('splitPlayer').src=result.video_url;el('splitDownload').href=result.video_url;
  el('splitResult').hidden=false;status.textContent='Ready! Review the finished clip before using it.';
 }catch(e){status.textContent='Could not create video: '+e.message}finally{button.disabled=false}
}

function fillRanks(){
 const files=[...el('rankFiles').files];const root=el('rankInputs');root.replaceChildren();
 if(files.length<2||files.length>5){root.textContent='Select between 2 and 5 videos.';return}
 files.forEach((file,index)=>{const label=document.createElement('label');label.textContent='#'+(index+1)+' — '+file.name;const input=document.createElement('input');input.className='rank-title';input.maxLength=50;input.value=file.name.replace(/\.[^.]+$/,'').slice(0,50);root.append(label,input)});
}
async function makeRanking(){
 const files=[...el('rankFiles').files],status=el('rankStatus'),btn=el('rankButton');
 if(files.length<2||files.length>5){status.textContent='Choose 2–5 clips first.';return}
 btn.disabled=true;el('rankResult').hidden=true;
 try{const titles=[...document.querySelectorAll('#rankInputs .rank-title')].map(x=>x.value.trim());const clips=[];
  for(let i=0;i<files.length;i++){status.textContent='Uploading clip '+(i+1)+' of '+files.length+'…';clips.push({name:await splitUpload(files[i]),title:titles[i]})}
  status.textContent='Rendering ranking countdown…';const d=await api('/api/video-ranking',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({clips,seconds_each:Number(el('rankSeconds').value)})});
  el('rankPlayer').src=d.video_url;el('rankDownload').href=d.video_url;el('rankResult').hidden=false;status.textContent='Ranking ready! Review before publishing.';
 }catch(e){status.textContent='Ranking error: '+e.message}finally{btn.disabled=false}
}

function switchView(view){
 for(const section of document.querySelectorAll('[data-view-panel]'))section.hidden=section.dataset.viewPanel!==view;
 for(const tab of document.querySelectorAll('nav .tab')){const selected=tab.dataset.view===view;tab.classList.toggle('active',selected);tab.setAttribute('aria-current',selected?'page':'false')}
 window.scrollTo({top:0,behavior:'smooth'});
}
async function copyAssistantRequest(){
 const title=el('assistTitle').value.trim()||'Untitled ShadowChronicles Short';
 const description=el('assistDesc').value.trim();
 const task=el('assistNotes').value;
 const message=`Shadow Studio video: ${title}\nDescription: ${description}\nTask: ${task}. I will attach the MP4 separately. Do NOT publish without showing me the final draft and receiving my explicit approval.`;
 try{await navigator.clipboard.writeText(message);el('assistStatus').textContent='Request copied. Open ChatGPT and paste it, then attach the exported MP4 for video-specific review.'}
 catch(e){el('assistStatus').textContent='Clipboard unavailable. Copy this request manually: '+message}
}
function importAssistantStory(){
 try{
  const data=JSON.parse(el('assistantImport').value);
  if(!data||typeof data.title!=='string'||!Array.isArray(data.lines)||data.lines.length<1||data.lines.length>40||data.title.length>95)throw Error('Use a title and 1–40 dialogue lines');
  for(const l of data.lines){if(typeof l.speaker!=='string'||!l.speaker.trim()||l.speaker.length>24||typeof l.text!=='string'||!l.text.trim()||l.text.length>240)throw Error('Each line needs a speaker (max 24) and text (max 240)')}
  el('title').value=data.title;el('lines').replaceChildren();data.lines.forEach(l=>addLine(l.speaker,l.text,l.pause_before));
  el('editor').style.display='block';el('renderBtn').disabled=false;
  switchView('create');setStatus('ChatGPT story imported. Review it before rendering.');
 }catch(e){el('assistStatus').textContent='Story import error: '+e.message}
}
async function refreshGameplay(){try{let r=await api('/api/gameplay');let sel=el('gameplayChoice');let current=sel.value;sel.replaceChildren(new Option('No saved gameplay',''),new Option('Automatic — rotate saved clips','auto'));for(let c of r.clips){sel.add(new Option(`${c.tag} · ${c.duration.toFixed(0)}s · used ${c.uses}x`,c.id))}if([...sel.options].some(o=>o.value===current))sel.value=current;el('gameplayStatus').textContent=`${r.clips.length} saved gameplay video(s). Auto selects the least-used footage.`;}catch(e){el('gameplayStatus').textContent='Gameplay library error: '+e.message}}
async function saveGameplay(){let f=el('gameplayFile').files[0];if(!f){el('gameplayStatus').textContent='Select a video first.';return}el('gameplaySave').disabled=true;try{let fd=new FormData();fd.append('file',f);fd.append('tag',el('gameplayTag').value||'general');el('gameplayStatus').textContent='Uploading footage...';let r=await api('/api/gameplay',{method:'POST',body:fd});await refreshGameplay();el('gameplayChoice').value=r.id;el('gameplayStatus').textContent=`Saved ${r.tag} (${Math.round(r.duration)}s). Selected for next render.`}catch(e){el('gameplayStatus').textContent='Upload error: '+e.message}finally{el('gameplaySave').disabled=false}}

let story=null;const el=id=>document.getElementById(id);const setStatus=s=>el('status').textContent=s;
async function api(path,options){const r=await fetch(path,options);let d=await r.json();if(!r.ok)throw Error(d.detail||'Request failed');return d}
async function loadStarter(i){
 try{
  const d=await api('/api/starter-stories');const x=d.stories[i];if(!x)throw Error('Not found');
  el('title').value=x.title;el('lines').replaceChildren();x.lines.forEach(l=>addLine(l.speaker,l.text,l.pause_before));
  el('editor').style.display='block';el('renderBtn').disabled=false;
  el('idea').value=x.title;setStatus('Complete original story loaded. Review it, preview the voice, and render.');
 }catch(e){setStatus('Starter story error: '+e.message)}
}
async function showStarters(){try{let d=await api('/api/starter-stories');const box=el('starterList');box.replaceChildren();d.stories.forEach((v,i)=>{const b=document.createElement('button');b.className='subtle';b.textContent='Load story '+(i+1);b.title=v.title;b.onclick=()=>loadStarter(i);box.append(b)});}catch(e){}}
async function createStory(random=false){
 const buttons=[el('storyBtn'),el('randomStoryBtn')];
 buttons.forEach(button=>button.disabled=true);
 setStatus('Creating script...');
 try{
  const request={idea:el('idea').value,style:el('style').value,target:Number(el('target').value)};
  if(random)request.genre=el('genre').value;
  const result=await api('/api/story',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});
  story=result.story;
  el('title').value=story.title;
  el('lines').replaceChildren();
  story.lines.forEach(line=>addLine(line.speaker,line.text,line.pause_before,line.alternate));
  el('editor').style.display='block';
  el('renderBtn').disabled=false;
  setStatus('Script ready. Review it before rendering.');
  return true;
 }catch(error){setStatus('Error: '+error.message);return false}
 finally{buttons.forEach(button=>button.disabled=false)}
}

function addLine(s='Me',t='',pause=0,alternate){
 const row=document.createElement('div');row.className='line';
 const speaker=document.createElement('input');speaker.value=s;speaker.maxLength=24;speaker.placeholder='Speaker';speaker.setAttribute('aria-label','Speaker');
 const dialogue=document.createElement('input');dialogue.value=t;dialogue.maxLength=240;dialogue.placeholder='Dialogue';dialogue.setAttribute('aria-label','Dialogue');
 const timing=document.createElement('label');timing.textContent='Pause before (s)';
 const seconds=document.createElement('input');seconds.type='number';seconds.min='0';seconds.max='10';seconds.step='0.1';seconds.value=pause;timing.append(seconds);
 const remove=document.createElement('button');remove.textContent='×';remove.setAttribute('aria-label','Remove line');remove.onclick=()=>row.remove();
 row.append(speaker,dialogue,timing,remove);
 if(alternate){
  const regenerate=document.createElement('button');regenerate.className='subtle';
  regenerate.textContent='Swap wording';
  let next=alternate;
  regenerate.onclick=()=>{const current=dialogue.value;dialogue.value=next;next=current};
  row.append(regenerate);
 }
 el('lines').append(row);
}
function editedLines(){
 return Array.from(el('lines').children).map(row=>{
  const pause=row.querySelector('input[type=number]');
  if(!pause.reportValidity())throw Error('Enter a pause between 0 and 10 seconds.');
  return {speaker:row.children[0].value.trim(),text:row.children[1].value.trim(),pause_before:Number(pause.value)};
 });
}



async function connectElevenLabs(){const out=el('elevenFeedback');const key=el('elevenKey').value;out.textContent='Checking account…';try{const d=await api('/api/elevenlabs/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:key})});el('elevenKey').value='';fillElevenVoices(d.voices,d.selected_voice);out.textContent='ElevenLabs connected. Voices now available in Create.';await refreshMainVoices();refreshIntegrationStatus(false);}catch(e){out.textContent='Connection failed: '+e.message;}}
function fillElevenVoices(voices,selected){const sel=el('elevenVoices');sel.replaceChildren();for(const v of voices){let o=document.createElement('option');o.value=v.id;o.textContent=v.name;sel.appendChild(o);}if(selected)sel.value=selected;}
async function saveElevenVoice(){const id=el('elevenVoices').value;if(!id){el('elevenFeedback').textContent='Connect ElevenLabs first';return;}try{await api('/api/elevenlabs/choose',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({voice_id:id})});el('elevenFeedback').textContent='Saved. Choose your ElevenLabs voice in Create.';await refreshMainVoices();}catch(e){el('elevenFeedback').textContent=e.message;}}
async function checkShadowUpdates(){const result=el('shadowUpdateStatus');result.textContent='Checking...';try{const data=await api('/api/updater/check');result.textContent=(data.update_available?'New version '+data.latest+': ':'')+data.message;}catch(e){result.textContent='Update check failed: '+e.message;}}
async function previewVoice(second=false){let b=el('previewVoiceBtn');b.disabled=true;setStatus('Generating voice preview...');try{let r=await fetch('/api/voice-preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({voice:el(second?'secondVoice':'voice').value,rate:Number(el('voiceRate').value)})});if(!r.ok){let d=await r.json();throw Error(d.detail||'Preview failed')}let blob=await r.blob();let url=URL.createObjectURL(blob);let audio=el('voicePreview');if(audio.dataset.url)URL.revokeObjectURL(audio.dataset.url);audio.src=url;audio.dataset.url=url;audio.style.display='block';await audio.play();setStatus('Voice preview ready.')}catch(e){setStatus('Voice error: '+e.message)}finally{b.disabled=false}}
async function renderVideo(){el('renderBtn').disabled=true;el('videoSection').style.display='none';el('progress').value=0;el('progress').style.display='block';setStatus('Preparing...');try{let bg=null;let gameplayId=el('gameplayChoice').value||null;let file=gameplayId?null:el('background').files[0];if(file){setStatus('Uploading your background...');const form=new FormData();form.append('file',file);bg=(await api('/api/upload-background',{method:'POST',body:form})).name}const lines=editedLines();const d=await api('/api/render',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({story:{title:el('title').value,lines},style:el('style').value,background_name:bg,gameplay_id:gameplayId,voice:el('voice').value,second_voice:el('secondVoice').value,voice_rate:Number(el('voiceRate').value),resolution:el('resolution').value})});poll(d.id)}catch(e){setStatus('Error: '+e.message);el('renderBtn').disabled=false}}
async function poll(id){try{let d=await api('/api/job/'+id);setStatus(d.message+(d.state==='running'?' • '+d.percent+'%':''));el('progress').value=d.percent;if(d.state==='complete'){el('preview').src=d.video_url;el('download').href=d.video_url;el('downloadCaptions').href='/api/subtitles/'+encodeURIComponent(id);el('description').value=d.description;el('videoSection').style.display='block';el('renderBtn').disabled=false;refreshLibrary();setStatus('Video technically verified. Review the full MP4 and captions in Library before approval.');return}if(d.state==='failed'){el('renderBtn').disabled=false;return}setTimeout(()=>poll(id),1500)}catch(e){setStatus('Status error: '+e.message);el('renderBtn').disabled=false}}
api('/api/health').then(d=>el('health').textContent=d.ffmpeg&&d.ffprobe?'✅ FFmpeg detected. Private local server ready. '+(d.ollama_model?'Ollama AI enabled.':'Offline story mode.'):'⚠️ FFmpeg not found. Install FFmpeg and restart the app.').catch(()=>el('health').textContent='Could not check local server.');

async function saveProject(){
 if(!el('editor').offsetParent){setStatus('Create a script first');return}
 try{const lines=editedLines();const d=await api('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:el('title').value,lines,style:el('style').value,voice:el('voice').value,voice_rate:Number(el('voiceRate').value),resolution:el('resolution').value})});setStatus('Project saved: '+d.title);await refreshProjects();el('projects').value=d.id}catch(e){setStatus('Could not save: '+e.message)}
}
async function refreshProjects(){try{const d=await api('/api/projects');el('projects').replaceChildren(new Option('Choose a project...',''));d.projects.forEach(p=>el('projects').add(new Option(p.title,p.id)))}catch(e){setStatus('Projects: '+e.message)}}
async function loadProject(){const id=el('projects').value;if(!id)return;try{const p=await api('/api/projects/'+id);el('title').value=p.title;el('style').value=p.style;el('voice').value=p.voice;el('voiceRate').value=String(p.voice_rate);el('resolution').value=p.resolution||'720p';el('lines').replaceChildren();p.lines.forEach(x=>addLine(x.speaker,x.text,x.pause_before));el('editor').style.display='block';el('renderBtn').disabled=false;setStatus('Loaded saved project')}catch(e){setStatus(e.message)}}
async function startReadyBatch(){
 const btn=el('readyBatchBtn'); btn.disabled=true;
 try{
  const d=await api('/api/batch-ready',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({story_indices:[0,1,2],style:el('style').value,voice:el('voice').value,voice_rate:Number(el('voiceRate').value),resolution:el('resolution').value,gameplay_id:el('gameplayChoice').value||null})});
  el('batchStatus').textContent='Started three original stories. You can review them in Library after rendering.';
  await batchPoll(d.id);
 }catch(e){el('batchStatus').textContent='Batch error: '+e.message}
 finally{btn.disabled=false}
}
async function startBatch(){const ideas=el('batchIdeas').value.split('\n').map(s=>s.trim()).filter(Boolean);if(!ideas.length||ideas.length>8){el('batchStatus').textContent='Enter 1–8 story ideas';return}el('batchBtn').disabled=true;try{const d=await api('/api/batch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ideas,style:el('style').value,target:Number(el('target').value),voice:el('voice').value,voice_rate:Number(el('voiceRate').value),resolution:el('resolution').value,gameplay_id:el('gameplayChoice').value||null})});el('batchStatus').textContent='Rendering batch '+d.id;await batchPoll(d.id)}catch(e){el('batchStatus').textContent='Batch error: '+e.message;el('batchBtn').disabled=false}}
async function batchPoll(id){try{const d=await api('/api/job/'+id);el('batchStatus').textContent=d.message+' ('+d.percent+'%)';await refreshJobs();if(d.state==='running'){setTimeout(()=>batchPoll(id),2500);return}el('batchBtn').disabled=false;if(d.state==='complete'){el('batchStatus').textContent+=' — Open Library to review and approve.';await refreshLibrary()}}catch(e){el('batchStatus').textContent=e.message;el('batchBtn').disabled=false}}
async function refreshLibrary(){
 try{
  const data=await api('/api/library');const grid=el('libraryGrid');grid.replaceChildren();
  el('libraryEmpty').textContent=data.videos.length?'':'No completed videos yet. Generate your first Short above.';
  for(const v of data.videos){
   const card=document.createElement('button');card.className='video-tile';card.type='button';
   const icon=document.createElement('span');icon.className='tile-icon';icon.textContent=v.style==='reddit'?'📝':'💬';
   const title=document.createElement('strong');title.textContent=v.title;
   const info=document.createElement('small');info.textContent=(v.duration||0)+' sec · '+(v.resolution||'720p')+' · '+(v.review_status==='approved'?'✓ Approved':v.review_status==='needs_changes'?'Needs changes':'Review needed');
   card.append(icon,title,info);card.onclick=()=>openLibraryItem(v);grid.append(card);
  }
 }catch(e){el('libraryEmpty').textContent='Could not load saved videos: '+e.message}
}
let selectedReviewId=null;
async function runQualityCheck(){
 if(!selectedReviewId)return;
 const box=el('qualityPanel');box.textContent='Checking the MP4…';
 try{
  const q=await api('/api/library/'+selectedReviewId+'/quality');
  const lines=[q.passed?'Technical export: OK':'Technical export: BLOCKED',
   `${q.width}×${q.height} · ${q.fps} FPS · ${q.duration_seconds}s · ${q.file_size_mb} MB`,
   `Audio track: ${q.audio_present?'Present':'Missing'}`];
  if(q.max_volume_db!==null)lines.push(`Peak audio level (opening): ${q.max_volume_db} dB`);
  for(const b of q.blockers)lines.push('BLOCKER: '+b);
  for(const w of q.warnings)lines.push('CHECK: '+w);
  lines.push('Listen to the entire voiceover and confirm gameplay rights before approval.');
  box.textContent=lines.join('\n');box.style.whiteSpace='pre-line';
 }catch(e){box.textContent='Could not inspect video: '+e.message}
}
async function saveReview(){
 if(!selectedReviewId)return;
 const btn=el('saveReviewBtn');btn.disabled=true;
 try{
  await api('/api/library/'+selectedReviewId+'/review',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:el('reviewState').value,notes:el('reviewNotes').value})});
  el('reviewFeedback').textContent='Saved! Approved videos appear in the downloadable upload kit.';
  await refreshLibrary();
 }catch(e){el('reviewFeedback').textContent='Review save failed: '+e.message}
 finally{btn.disabled=false}
}
function openLibraryItem(v){
 selectedReviewId=v.id;
 el('reviewState').value=v.review_status||'needs_review';
 el('reviewNotes').value=v.review_notes||'';
 el('reviewFeedback').textContent='Nothing publishes automatically. Approving only prepares files for upload.';
 el('qualityPanel').textContent='Run quality check before approval. Video review and gameplay permissions remain your responsibility.';
 el('assistTitle').value=v.title||'';el('assistDesc').value=v.description||'';el('draftVideoId').value=v.id;
 el('libraryDetails').hidden=false;el('libraryTitle').textContent=v.title;
 el('libraryPlayer').src=v.video_url;el('libraryDescription').value=v.description||'';
 el('libraryDownload').href=v.video_url;el('subtitleDownload').href='/api/subtitles/'+encodeURIComponent(v.id);el('subtitleDownload').hidden=false;el('handoffDownload').href='/api/chatgpt-handoff/'+encodeURIComponent(v.id);
 el('libraryDetails').scrollIntoView({behavior:'smooth',block:'nearest'});
}

async function createCloudHandoff(){
 if(!selectedReviewId)return;
 const btn=el('cloudHandoffBtn'),out=el('cloudHandoffResult');out.hidden=false;
 btn.disabled=true;out.textContent='Uploading approved video to your private storage…';
 try{
  const d=await api('/api/cloud-handoff/'+encodeURIComponent(selectedReviewId),{method:'POST'});
  out.replaceChildren();
  const intro=document.createElement('p');intro.textContent='Private link ready (expires in 24 hours). Anyone with the link can view the video. Paste this request into ChatGPT to create a vidIQ draft; nothing is published yet.';
  const area=document.createElement('textarea');area.value=d.request_for_chatgpt;area.readOnly=true;area.rows=7;
  const copy=document.createElement('button');copy.textContent='Copy ChatGPT + vidIQ request';copy.onclick=()=>navigator.clipboard.writeText(area.value);
  out.append(intro,area,copy);
 }catch(e){out.textContent='Handoff not created: '+e.message+' Configure storage in .env (see README).'}
 finally{btn.disabled=false}
}

async function stagePublishingDraft(){
 const id=el('draftVideoId').value.trim();
 if(!id){el('draftFeedback').textContent='Open a finished video in Library first.';return}
 try{
  const d=await api('/api/publishing-drafts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({video_id:id,title:el('assistTitle').value,description:el('assistDesc').value,privacy:el('draftPrivacy').value})});
  el('draftFeedback').textContent='Saved local draft '+d.id+'. No video has been uploaded.';
  await refreshPublishingDrafts();
 }catch(e){el('draftFeedback').textContent='Could not stage draft: '+e.message}
}
async function refreshPublishingDrafts(){
 const container=el('draftQueue');container.replaceChildren();
 try{const data=await api('/api/publishing-drafts');
  if(!data.drafts.length){container.textContent='No publishing drafts yet.';return}
  for(const d of data.drafts){const row=document.createElement('div');row.className='help';
   const title=document.createElement('strong');title.textContent=d.title;
   const info=document.createElement('p');info.className='muted';info.textContent='Local draft '+d.id+' · requested '+d.desired_privacy+' · not uploaded';
   row.append(title,info);container.appendChild(row)}
 }catch(e){container.textContent=e.message}
}
async function refreshJobs(){try{let d=await api('/api/jobs');let list=el('jobList');list.replaceChildren();for(const j of d.jobs){if(j.title==='Batch render')continue;const r=document.createElement('div');r.style='border-bottom:1px solid #34415a;padding:10px 0';const t=document.createElement('strong');t.textContent=j.title+' — '+j.state;r.append(t);if(j.video_url){const a=document.createElement('a');a.href=j.video_url;a.textContent='  Download video';a.download='';a.style='color:#a5ccff;margin-left:12px';r.append(a)}list.append(r)}}catch(e){el('jobList').textContent=e.message}}
async function createClip(){el('clipBtn').disabled=true;try{el('clipStatus').textContent='Importing...';const u=await clipSource('clipFile','clipUrl','clipRights');el('clipStatus').textContent='Trimming...';const d=await api('/api/clip',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({background_name:u.name,start:Number(el('clipStart').value),end:Number(el('clipEnd').value),orientation:'vertical'})});const a=document.createElement('a');a.href=d.download_url;a.download='shadow_clip.mp4';a.textContent='Download vertical clip';a.style='color:#a5ccff';el('clipLink').replaceChildren(a);el('clipStatus').textContent='Clip ready'}catch(e){el('clipStatus').textContent='Clip error: '+e.message}finally{el('clipBtn').disabled=false}}
async function refreshMainVoices(){try{const d=await api('/api/voices');for(const id of ['voice','secondVoice']){const select=el(id);const current=select.value;select.replaceChildren();for(const voice of d.voices){const opt=document.createElement('option');opt.value=voice.id;opt.textContent=voice.name+(voice.provider==='premium'?' • ElevenLabs credits':'');select.appendChild(opt)}const initial=id==='voice'?'en-US-AriaNeural':'en-US-GuyNeural';select.value=[...select.options].some(x=>x.value===current)?current:initial;}el('voiceAvailability').textContent=(d.neural_available?'✓ Free neural voices installed. ':'⚠️ Neural voices unavailable. ')+(d.premium_configured?'ElevenLabs account voices loaded. ':'')+(d.premium_error||'');}catch(e){el('voiceAvailability').textContent='Could not load voices: '+e.message}}
refreshMainVoices();

async function checkProductionReadiness(){
  const box=el('launchChecks');if(!box)return;
  box.classList.add('show');const items=el('launchCheckItems');items.replaceChildren();el('launchCheckNote').textContent='Checking local tools…';
  try{
    const d=await api('/api/launch-readiness');
    el('metricRendered').textContent=d.videos.completed;
    el('metricApproved').textContent=d.videos.approved;
    el('metricPending').textContent=d.videos.awaiting_review;
    for(const check of d.checks){const tag=document.createElement('span');tag.className='check-chip '+(check.passed?'ok':'no');tag.textContent=(check.passed?'✓ ':'! ')+check.name+(check.passed?' ready':(check.required?' missing':' optional'));tag.title=check.detail;items.appendChild(tag)}
    el('launchCheckNote').textContent=d.ready_to_attempt_render?'Core render tools detected. Preview the voice you choose before starting a batch.':'FFmpeg or FFprobe is missing. Install it before rendering.';
  }catch(e){el('launchCheckNote').textContent='Could not contact the local app: '+e.message;}
}
refreshProjects();refreshJobs();refreshLibrary();checkProductionReadiness();
async function refreshIntegrationStatus(live){
 const root=el('integrationStatus');if(!root)return;
 root.textContent=live?'Testing provider connections…':'Loading setup…';
 try{
  const d=await api(live?'/api/integrations/check':'/api/integrations',live?{method:'POST'}:undefined);
  root.replaceChildren();
  for(const item of d.integrations){
   const card=document.createElement('article');card.className='integration-health-card';
   const head=document.createElement('h3');head.textContent=item.name;
   const status=document.createElement('div');status.className='integration-health-state';
   status.textContent=item.reachable===true?'● Connected (read-only check)':item.reachable===false?'● Not reachable':item.configured?'● Configured — not live-tested':'○ Not configured';
   const detail=document.createElement('p');detail.textContent=item.detail;
   const tip=document.createElement('small');tip.textContent='Setup: '+item.setup;
   card.append(head,status,detail,tip);root.append(card);
  }
 }catch(e){root.textContent='Integration check failed: '+e.message}
}
refreshGameplay();showStarters();refreshPublishingDrafts();refreshIntegrationStatus(false);switchView('create');</script></body></html>'''