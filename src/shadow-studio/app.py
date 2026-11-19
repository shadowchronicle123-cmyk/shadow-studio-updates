"""Shadow Studio local production dashboard."""
import json, os, re, threading, uuid, io, zipfile, hashlib, subprocess, shutil as file_shutil
from urllib.parse import urlsplit
from datetime import datetime, timezone
from dotenv import load_dotenv
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Literal
load_dotenv(Path(__file__).resolve().parent / ".env")
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from story_engine import make_story, metadata, random_story, rewrite_line
from renderer import line_pause, generate, preview_layout
from update_manager import VERSION as STUDIO_VERSION
from audio_mix import line_sound
from captions import read_captions, as_webvtt
from voice_engine import VOICES, available_voices, valid_voice, create_voice
from studio_tools import save_project, list_projects, get_project, clip_media, CLIPS
from gameplay_library import GameplayLibrary, gameplay_duration
from split_screen import split_screen
from video_ranking import rank_videos
from advanced_tools import scene_candidates, transcribe_local, edit_video, motion_from_image, MEDIA_ROOT
from quality_check import analyze as quality_analyze
from approval_security import approved_unchanged, file_digest
from integration_bridge import router as agent_router, DraftRequest, create_draft as stage_local_draft, list_drafts as staged_drafts
from cloud_handoff import config as cloud_config, state as cloud_state, start_upload as cloud_upload, test_connection as cloud_test, auto_upload as cloud_auto_upload, HandoffError

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

def validate_rendered_video(result, *, captions_required=True):
    """Fail closed for a broken video before it appears as completed or publishable."""
    report=quality_analyze(Path(result['path']))
    if not report['passed']:
        raise RuntimeError('Export failed technical checks: ' + '; '.join(report['blockers']))
    captions=Path(result['path']).parent/'captions.srt'
    if captions_required and (not captions.is_file() or not captions.read_text(encoding="utf-8").strip()):
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
            item['captions_url']='/api/subtitles/'+item['id'] if (path.parent/'captions.srt').is_file() else None
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
    return {**item,'video_url':'/api/video/'+jobid,
            'captions_url':'/api/subtitles/'+jobid if (path.parent/'captions.srt').is_file() else None}


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
    return {**item,'video_url':'/api/video/'+jobid,
            'captions_url':'/api/subtitles/'+jobid if (path.parent/'captions.srt').is_file() else None}

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
        return {'configured':c['enabled'],'auto_upload_on_approval':c['enabled'] and c['auto_upload'], 'missing':c['missing'],
                'instructions':'Private cloud upload only. ChatGPT / vidIQ draft initiation and publishing are separate.'}
    except HandoffError as exc:raise HTTPException(400,str(exc))

@app.post('/api/cloud-handoff/test')
def cloud_handoff_test():
    try:
        return cloud_test()
    except HandoffError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get('/api/cloud-handoff/{jobid}/status')
def cloud_handoff_status(jobid:str):
    try:return JSONResponse(cloud_state(jobid), headers={'Cache-Control':'no-store'})
    except HandoffError as exc:raise HTTPException(400,str(exc))

@app.post('/api/cloud-handoff/{jobid}', status_code=202)
def cloud_handoff_send(jobid:str):
    try:return cloud_upload(jobid)
    except HandoffError as exc:
        raise HTTPException(409,str(exc))

def download_archive(archive, filename):
    archive.seek(0)
    def chunks():
        with archive:
            while chunk := archive.read(1024 * 1024):
                yield chunk
    return StreamingResponse(chunks(), media_type='application/zip', headers={
        'Content-Disposition': f'attachment; filename="{filename}"',
        'Cache-Control': 'no-store',
    })


def add_approved_video(archive, name, video, expected):
    digest = hashlib.sha256()
    with video.open('rb') as source, archive.open(name, 'w') as destination:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            destination.write(chunk)
    if digest.hexdigest() != expected:
        raise HTTPException(409, 'The MP4 changed during packaging. Review and approve the current video.')


@app.get('/api/chatgpt-handoff/{jobid}')
def chatgpt_handoff(jobid: str):
    """Export one human-approved Short for manual attachment in ChatGPT.

    This endpoint never transmits a file off the computer and does not grant ChatGPT access.
    """
    _, item = video_record(jobid)
    if not approved_unchanged(item, OUT/jobid/'short.mp4'):
        raise HTTPException(409, 'Review and approve the current MP4 in Review Library before downloading a publishing kit.')
    video = OUT / jobid / 'short.mp4'
    result = quality_analyze(video)
    if not result['passed']:
        raise HTTPException(409, 'Video failed current technical quality check')
    if video.stat().st_size > 200 * 1024 * 1024:
        raise HTTPException(413, 'MP4 exceeds 200 MB; download video separately')
    digest=item['approved_video_sha256']
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
        '3. If vidIQ is connected and accepts the file, request a draft. You can also upload the MP4 manually in YouTube Studio.\n'
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
    buffer=SpooledTemporaryFile(max_size=8*1024*1024)
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_STORED) as archive:
        add_approved_video(archive, 'short.mp4', video, digest)
        archive.writestr('YOUTUBE_METADATA.txt', f"{item.get('title','')}\n\n{item.get('description','')}\n")
        if captions.is_file():archive.write(captions,'captions.srt')
        archive.writestr('handoff.json', json.dumps(manifest,indent=2,ensure_ascii=False))
        archive.writestr('REQUEST_FOR_CHATGPT.txt',request)
        archive.writestr('README_FIRST.txt',instructions)
    return download_archive(buffer, f'shadow-chatgpt-handoff-{jobid}.zip')


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
    mem=SpooledTemporaryFile(max_size=8*1024*1024)
    manifest=[]
    with zipfile.ZipFile(mem,'w',compression=zipfile.ZIP_STORED) as z:
        for item,file in sorted(approved,key=lambda x:x[0].get('created_at','')):
            jid=item['id']
            add_approved_video(z,f'{jid}/short.mp4',file,item['approved_video_sha256'])
            captionfile=file.parent/'captions.srt'
            if captionfile.is_file():z.write(captionfile,f'{jid}/captions.srt')
            z.writestr(f'{jid}/youtube.txt',f"TITLE: {item.get('title','')}\n\nDESCRIPTION:\n{item.get('description','')}\n")
            manifest.append({'id':jid,'title':item.get('title',''),'review_status':'approved'})
        z.writestr('UPLOAD_CHECKLIST.txt','1. Review every MP4.\n2. Confirm voice and gameplay rights.\n3. Upload approved video in YouTube Studio or vidIQ.\n4. Confirm title, description and visibility.\n5. Publish only after your own final approval.\n')
        z.writestr('manifest.json',json.dumps(manifest,indent=2))
    return download_archive(mem, 'shadow-studio-approved-shorts.zip')


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
    previous_title:str=Field(default="",max_length=95)
    genre:str|None=None
    mode:Literal["custom", "random"]|None=None
    provider:Literal["offline", "ollama"]="offline"
    avoid:list[str]=Field(default_factory=list,max_length=12)
    ending:Literal["resolved", "question"]="resolved"
    style:str='messages'
    target:int=45

class RenderOptions(BaseModel):
    style:str='messages'
    voice:str="en-US-AriaNeural"
    voice_rate:int=Field(default=0,ge=-30,le=30)
    second_voice:str|None=None
    primary_speaker:str|None=Field(default=None,max_length=24)
    resolution:str="720p"
    gameplay_id:str|None=None
    gameplay_percent:int=Field(default=58,ge=55,le=60)
    gameplay_anchor:Literal["top", "center", "bottom"]="center"
    pacing:Literal["snappy", "normal", "deliberate"]="normal"
    captions:bool=True
    allow_premium:bool=False
    music_name:str|None=None
    voice_volume:int=Field(default=100,ge=25,le=150)
    music_volume:int=Field(default=22,ge=0,le=100)
    target_seconds:Literal[30, 45, 60]|None=None

class RenderRequest(RenderOptions):
    story: dict
    background_name: str | None = None


def safe_style(s):
    if s not in ('messages','reddit'):raise HTTPException(400,'Choose messages or reddit style')
    return s

@app.get('/',response_class=HTMLResponse)
def home():
    return HTML.replace("__STUDIO_VERSION__", STUDIO_VERSION)


@app.get('/studio.css', include_in_schema=False)
def studio_styles():
    return FileResponse(ROOT/'studio.css', media_type='text/css')

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
        if req.mode == "random" or (req.mode is None and req.genre is not None):
            return {'story':random_story(req.genre or "random", req.target, req.ending, req.previous_title)}
        if len(req.idea.strip()) < 3:
            raise ValueError('Enter a story idea or choose a genre')
        return {'story':make_story(req.idea,req.style,req.target,req.ending,genre=req.genre or 'drama',provider=req.provider,avoid=req.avoid)}
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502,str(exc)) from exc


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
        await run_in_threadpool(gameplay_duration,path)
    except ValueError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(400,str(exc)) from exc
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {'name':name,'size':size}

MUSIC_FILENAME = re.compile(r'music_[a-f0-9]{12}_[a-zA-Z0-9_-]{1,60}\.(mp3|wav|m4a|aac|ogg|flac)')


@app.get('/api/music')
def music_list():
    return {'tracks': [
        {'name': path.name, 'label': path.stem.split('_', 2)[2].replace('_', ' ')}
        for path in sorted(UPLOADS.glob('music_*'))
        if path.is_file() and MUSIC_FILENAME.fullmatch(path.name)
    ]}


@app.post('/api/music')
async def upload_music(file: UploadFile = File(...), licensed: bool = Form(False)):
    if not licensed:
        raise HTTPException(400, 'Confirm you may use the music in your videos')
    original = Path(file.filename or '')
    extension = original.suffix.lower()
    label = re.sub(r'[^a-zA-Z0-9_-]+', '_', original.stem).strip('_')[:60] or 'track'
    name = f'music_{uuid.uuid4().hex[:12]}_{label}{extension}'
    if not MUSIC_FILENAME.fullmatch(name):
        raise HTTPException(400, 'Upload MP3, WAV, M4A, AAC, OGG, or FLAC audio')
    destination = UPLOADS / name
    temporary = destination.with_suffix('.part')
    try:
        size = 0
        with temporary.open('xb') as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 50 * 1024 * 1024:
                    raise HTTPException(413, 'Music upload limit is 50 MB')
                output.write(chunk)
        probe = await run_in_threadpool(
            subprocess.run,
            ['ffprobe','-v','error','-select_streams','a:0','-show_entries','format=duration:stream=codec_type',
             '-of','json',str(temporary)], capture_output=True, text=True, timeout=20,
        )
        data = json.loads(probe.stdout) if probe.returncode == 0 else {}
        if not data.get('streams') or not 0.25 <= float(data.get('format', {}).get('duration', 0)) <= 7200:
            raise HTTPException(400, 'Choose a playable audio track between 0.25 seconds and two hours')
        temporary.replace(destination)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(400, 'Could not validate this audio file') from exc
    finally:
        temporary.unlink(missing_ok=True)
    return {'name': name}


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
        return await run_in_threadpool(GAMEPLAY.register,target,tag)
    except ValueError as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(400,str(exc)) from exc
    except Exception:
        target.unlink(missing_ok=True)
        raise

class VoicePreviewRequest(BaseModel):
    voice:str='en-US-AriaNeural'
    rate:int=Field(default=0,ge=-30,le=30)
    allow_premium:bool=False

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
    require_voice_consent(req.allow_premium,req.voice)
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

def clean_render_story(data):
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
            effect, sound_offset = line_sound(x)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        clean.append({'speaker':speaker or 'Narrator','text':message,'pause_before':pause,'sound_effect':effect,'sound_offset':sound_offset})
    title=str(data.get('title','Original Story'))[:95]
    return {'title': title, 'lines': clean}


class RewriteLineRequest(BaseModel):
    story: dict
    line_index: int = Field(ge=0, le=23)


@app.post('/api/story/rewrite-line')
def story_rewrite_line(req: RewriteLineRequest):
    story = clean_render_story(req.story)
    if req.line_index >= len(story['lines']):
        raise HTTPException(400, 'Choose a line to rewrite')
    try:
        return {'text': rewrite_line(story, req.line_index)}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


def background_file(name):
    if not re.fullmatch(r'background_[0-9a-f]{12}\.(mp4|mov|mkv|webm)', name):
        raise HTTPException(400,'Invalid background')
    path = UPLOADS/name
    if not path.is_file():
        raise HTTPException(404,'Background not found. Select the file again.')
    return path


class LayoutPreviewRequest(BaseModel):
    story: dict
    style: Literal["messages", "reddit"] = "messages"
    line_index: int = Field(default=0, ge=0, le=23)
    gameplay_id: str | None = None
    show_gameplay: bool = False
    background_name: str | None = None
    resolution: Literal["720p","1080p"] = "720p"
    gameplay_percent: int = Field(default=58,ge=55,le=60)
    gameplay_anchor: Literal["top","center","bottom"] = "center"


@app.post('/api/layout-preview')
def layout_preview(req: LayoutPreviewRequest):
    story = clean_render_story(req.story)
    if req.line_index >= len(story['lines']):
        raise HTTPException(400, 'Choose a dialogue line to preview')
    if req.background_name and req.gameplay_id:
        raise HTTPException(400,'Choose a saved clip or a one-time upload.')
    clip = pick_gameplay(req.gameplay_id)
    background = background_file(req.background_name) if req.background_name else clip['path'] if clip else None
    try:
        image = preview_layout(
            story, req.line_index, req.style,
            show_gameplay=bool(background) or req.show_gameplay,
            background=background,
            start=clip['cursor'] if clip else 0,resolution=req.resolution,
            gameplay_percent=req.gameplay_percent,gameplay_anchor=req.gameplay_anchor,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(422, 'Could not render a layout preview for this gameplay file') from exc
    return Response(image, media_type='image/png', headers={'Cache-Control': 'no-store'})


def require_voice_consent(allowed, *voices):
    if not allowed and any(voice and voice.startswith('elevenlabs:') for voice in voices):
        raise HTTPException(409, 'Confirm ElevenLabs credit usage before using this voice.')


def render_settings(req):
    safe_style(req.style)
    require_voice_consent(req.allow_premium,req.voice,req.second_voice)
    if req.resolution not in ('720p','1080p'): raise HTTPException(400,'Choose 720p or 1080p')
    if not valid_voice(req.voice):raise HTTPException(400,'Invalid voice')
    if req.second_voice and not valid_voice(req.second_voice):raise HTTPException(400,'Invalid second voice')
    music = None
    if req.music_name:
        if not MUSIC_FILENAME.fullmatch(req.music_name):
            raise HTTPException(400, 'Invalid music file')
        music = UPLOADS / req.music_name
        if not music.is_file():
            raise HTTPException(404, 'Music track not found')
    return {
        'style': req.style, 'voice': req.voice, 'second_voice': req.second_voice,
        'voice_rate': req.voice_rate, 'resolution': req.resolution, 'pacing': req.pacing,
        'captions': req.captions, 'music': music, 'voice_volume': req.voice_volume,
        'music_volume': req.music_volume, 'target_seconds': req.target_seconds, 'gameplay_percent': req.gameplay_percent,
        'gameplay_anchor': req.gameplay_anchor, 'primary_speaker':req.primary_speaker,
    }


def pick_gameplay(gameplay_id):
    if not gameplay_id or (gameplay_id == 'auto' and not GAMEPLAY.list_clips()):
        return None
    try:
        return GAMEPLAY.pick(gameplay_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post('/api/render')
def render(req:RenderRequest):
    settings = render_settings(req)
    story = clean_render_story(req.story)
    title, clean = story['title'], story['lines']
    if req.primary_speaker and req.primary_speaker.strip().casefold() not in {row['speaker'].casefold() for row in clean}:
        raise HTTPException(400, 'Choose a main voice speaker who appears in this conversation.')
    background=None
    if req.background_name and req.gameplay_id:raise HTTPException(400,'Choose a gameplay library clip or a one-time upload, not both')
    selected_gameplay = pick_gameplay(req.gameplay_id)
    if selected_gameplay:
        background = selected_gameplay['path']
    if req.background_name:
        background = background_file(req.background_name)
    with lock:
        if active['id'] and jobs.get(active['id'],{}).get('state')=='running':raise HTTPException(409,'A video is already rendering')
        jobid=uuid.uuid4().hex[:12]
        jobs[jobid]={'id':jobid,'state':'running','percent':1,'message':'Starting render','title':title,'description':metadata({'title':title,'lines':clean})['description']}
        active['id']=jobid
    def run_job():
        def callback(p,msg):
            with lock:jobs[jobid].update(percent=p,message=msg)
        try:
            result = generate(story, OUT/jobid, background=background, progress=callback,
                              gameplay_start=selected_gameplay['cursor'] if selected_gameplay else 0,
                              **settings)
            callback(96, 'Verifying finished video and audio')
            validate_rendered_video(result, captions_required=req.captions)
            archive_video(jobid,title,jobs[jobid]['description'],result['duration'],req.style,req.resolution,req.voice)
            if selected_gameplay:GAMEPLAY.mark_used(selected_gameplay['id'],result['duration'])
            with lock: jobs[jobid].update(state='complete',message='Ready to review',percent=100,duration=result['duration'],target_seconds=req.target_seconds,resolution=req.resolution,fps=30,video_url='/api/video/'+jobid,captions_url='/api/subtitles/'+jobid if req.captions else None)
        except Exception as e:
            with lock: jobs[jobid].update(state='failed',message=str(e)[-650:])
    threading.Thread(target=run_job,daemon=True).start()
    return {'id':jobid}

@app.get('/api/job/{jobid}')
def job(jobid:str):
    with lock: data=dict(jobs[jobid]) if jobid in jobs else None
    if not data:raise HTTPException(404,'Unknown job')
    return data

def subtitle_path(jobid):
    video_record(jobid)
    path = OUT / jobid / 'captions.srt'
    if not path.is_file():
        raise HTTPException(404, 'Subtitles unavailable for this video')
    return path


@app.get('/api/subtitles/{jobid}')
def subtitles(jobid: str):
    return FileResponse(subtitle_path(jobid), media_type='application/x-subrip',
                        filename=f'shadowchronicles_{jobid}.srt', headers={'Cache-Control':'no-store'})


@app.get('/api/captions-review/{jobid}')
def captions_review(jobid: str, webvtt: bool = False):
    cues = read_captions(subtitle_path(jobid))
    if webvtt:
        return Response(as_webvtt(cues), media_type='text/vtt', headers={'Cache-Control':'no-store'})
    return {'cues': cues}


@app.get('/api/video/{jobid}')
def video(jobid:str):
    if not re.fullmatch(r'[a-f0-9]{12}',jobid):raise HTTPException(400,'Invalid id')
    path=OUT/jobid/'short.mp4'
    if not path.is_file():raise HTTPException(404,'Video not ready')
    return FileResponse(path,media_type='video/mp4',filename='shadowchronicles_'+jobid+'.mp4')


class ProjectRequest(BaseModel):
    title:str='Untitled'
    idea:str=Field(default='',max_length=350)
    provider:Literal["offline","ollama"]="offline"
    lines:list[dict]
    style:str='messages'
    voice:str='en-US-AriaNeural'
    voice_rate:int=0
    second_voice:str|None=None
    primary_speaker:str|None=Field(default=None,max_length=24)
    resolution:str='720p'
    gameplay_id:str|None=None
    gameplay_percent:int=Field(default=58,ge=55,le=60)
    gameplay_anchor:Literal["top", "center", "bottom"]="center"
    pacing:Literal["snappy", "normal", "deliberate"]="normal"
    captions:bool=True
    music_name:str|None=None
    voice_volume:int=Field(default=100,ge=25,le=150)
    music_volume:int=Field(default=22,ge=0,le=100)
    target:int=45
    fit_duration:bool=True
    genre:str="random"
    ending:Literal["resolved", "question"]="resolved"

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
    with lock:return {'jobs':[dict(jobs[jobid]) for jobid in list(jobs)[-50:][::-1]]}

class BatchRequest(RenderOptions):
    genre: Literal["random","horror","drama","romance","betrayal","revenge","mystery","comedy"] = "drama"
    provider: Literal["offline","ollama"] = "offline"
    ideas: list[str] = Field(min_length=1, max_length=8)
    target: int = 45
    ending: Literal["resolved", "question"] = "resolved"


class ReadyBatchRequest(RenderOptions):
    story_indices: list[int] = Field(default_factory=lambda: [0, 1, 2], min_length=1, max_length=3)


class RandomBatchRequest(RenderOptions):
    count: int = Field(default=3, ge=1, le=8)
    genre: Literal["random", "horror", "drama", "romance", "betrayal", "revenge", "mystery", "comedy"] = "random"
    target: int = 45
    ending: Literal["resolved", "question"] = "resolved"


def start_batch(req, values, build_story, title):
    settings = render_settings(req)
    settings['primary_speaker'] = None
    if req.gameplay_id and req.gameplay_id != 'auto':
        pick_gameplay(req.gameplay_id)
    with lock:
        if active['id'] and jobs.get(active['id'], {}).get('state') == 'running':
            raise HTTPException(409, 'A render is already running')
        batch_id = uuid.uuid4().hex[:12]
        active['id'] = batch_id
        jobs[batch_id] = {'id': batch_id, 'state': 'running', 'percent': 1,
                          'message': 'Preparing batch', 'title': title, 'video_ids': []}

    def work():
        previous_title = ''
        job_id = None
        try:
            for index, value in enumerate(values):
                story = build_story(value, previous_title)
                previous_title = story['title']
                description = metadata(story)['description']
                job_id = uuid.uuid4().hex[:12]
                with lock:
                    jobs[job_id] = {'id': job_id, 'state': 'running', 'percent': 0,
                                    'message': 'Starting narration', 'title': story['title'],
                                    'description': description}
                    jobs[batch_id].update(message=f'Rendering {index + 1} of {len(values)}',
                                          percent=round(index / len(values) * 100))

                def progress(percent, message):
                    with lock:
                        jobs[job_id].update(percent=percent, message=message)

                clip = pick_gameplay(req.gameplay_id)
                result = generate(story, OUT/job_id, progress=progress,
                                  background=clip['path'] if clip else None,
                                  gameplay_start=clip['cursor'] if clip else 0, **settings)
                progress(96, 'Verifying finished video and audio')
                validate_rendered_video(result, captions_required=req.captions)
                archive_video(job_id, story['title'], description, result['duration'],
                              req.style, req.resolution, req.voice)
                if clip:
                    GAMEPLAY.mark_used(clip['id'], result['duration'])
                with lock:
                    jobs[job_id].update(state='complete', percent=100, message='Ready for review',
                                        duration=result['duration'], video_url='/api/video/'+job_id)
                    jobs[batch_id]['video_ids'] = [*jobs[batch_id]['video_ids'], job_id]
            with lock:
                jobs[batch_id].update(state='complete', percent=100,
                                      message=f'{len(values)} Shorts ready for review')
        except Exception as exc:
            message = str(exc.detail if isinstance(exc, HTTPException) else exc)[-600:]
            with lock:
                if job_id and jobs[job_id]['state'] == 'running':
                    jobs[job_id].update(state='failed', message=message)
                jobs[batch_id].update(state='failed', message=message)
        finally:
            with lock:
                if active['id'] == batch_id:
                    active['id'] = None
    threading.Thread(target=work, daemon=True).start()
    return {'id': batch_id, 'count': len(values)}


@app.post('/api/batch')
def batch_render(req: BatchRequest):
    ideas = [idea.strip() for idea in req.ideas]
    if any(not 3 <= len(idea) <= 350 for idea in ideas):
        raise HTTPException(400, 'Each story idea must have 3 to 350 characters')
    return start_batch(req, ideas, lambda idea, _: make_story(idea, req.style, req.target, req.ending,genre=req.genre,provider=req.provider), 'Idea batch')


@app.post('/api/batch-ready')
def batch_ready(req: ReadyBatchRequest):
    indices = req.story_indices
    if len(set(indices)) != len(indices) or any(not 0 <= index < len(STARTER_STORIES) for index in indices):
        raise HTTPException(400, 'Choose distinct starter stories')
    return start_batch(req, indices, lambda index, _: STARTER_STORIES[index], 'Ready stories batch')


@app.post('/api/batch-random')
def batch_random(req: RandomBatchRequest):
    return start_batch(req, range(req.count),
                       lambda _, previous: random_story(req.genre, req.target, req.ending, previous),
                       'Random stories batch')


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

HTML=r'''<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Shadow Studio — Private</title><link rel="stylesheet" href="/studio.css"></head><body>
<header><div class="brandrow"><img src="/brand-logo" alt="" class="brandmark"><h1>Shadow Studio</h1></div><nav aria-label="Workspace">
<button type="button" class="tab active" data-view="create" onclick="switchView('create')">Create</button>
<button type="button" class="tab" data-view="library" onclick="switchView('library')">Review Library</button>
<button type="button" class="tab" data-view="automation" onclick="switchView('automation')">Render Queue</button>
<button type="button" class="tab" data-view="assistant" onclick="switchView('assistant')">Publishing</button>
<button type="button" class="tab" data-view="tools" onclick="switchView('tools')">Media Tools</button>
<button type="button" class="tab" data-view="settings" onclick="switchView('settings')">Settings</button>
</nav><span class="version">v__STUDIO_VERSION__</span></header>
<main data-view-panel="create">
<div class="workspace-head"><div><h2>Create a Short</h2><p>Write the conversation, frame your video, then render it for review.</p></div>
<div class="launch-metrics" aria-label="Video library totals"><div class="metric"><strong id="metricRendered">—</strong><span>Rendered</span></div><div class="metric"><strong id="metricPending">—</strong><span>To review</span></div><div class="metric"><strong id="metricApproved">—</strong><span>Approved</span></div></div></div>
<div class="workspace"><div><div class="stage-bar" aria-label="Production stages"><button type="button" data-stage="story" aria-current="step" onclick="showStage('story')">Story</button><button type="button" data-stage="setup" onclick="showStage('setup')">Voice &amp; gameplay</button></div>
<section class="panel" id="create-panel"><div class="section-head"><h2>Story</h2><div><label for="projects">Open saved project</label><select id="projects" onchange="loadProject()"><option value="">Choose a project…</option></select></div></div><details class="help"><summary>Start with a finished example</summary><div id="starterList" class="row"></div></details><label for="idea">Story idea</label><textarea id="idea" maxlength="350" placeholder="My girlfriend invited her ex to our wedding."></textarea><div class="mini-grid"><div><label for="style">Video style</label><select id="style"><option value="messages">Animated text message story</option><option value="reddit">Reddit-style story</option></select></div><div><label for="target">Target story length</label><select id="target"><option value="30">30 seconds</option><option value="45" selected>45 seconds</option><option value="60">60 seconds</option></select></div><div><label for="genre">Story genre</label><select id="genre"><option value="random">Random</option><option value="horror">Horror</option><option value="drama">Drama</option><option value="romance">Romance</option><option value="betrayal">Betrayal</option><option value="revenge">Revenge</option><option value="mystery">Mystery</option><option value="comedy">Comedy</option></select></div><div><label for="storyProvider">Custom story engine</label><select id="storyProvider"><option value="offline">Offline expansion · free</option><option value="ollama">Ollama · local AI (optional)</option></select></div></div><label for="ending">Ending</label><select id="ending"><option value="resolved">Story resolution</option><option value="question">Resolution + audience question</option></select><div class="row"><button onclick="createStory()" id="storyBtn">Generate story</button><button type="button" id="regenerateBtn" class="subtle" onclick="createStory()" disabled>Regenerate</button><button class="subtle" onclick="createStory(true)" id="randomStoryBtn">Generate random story</button><button onclick="quickGenerate()" id="quickBtn">Quick render</button></div><p class="muted">Offline expansion uses authored plot branches. Local Ollama is optional and needs a model on this PC.</p><div id="editor"><h3>Dialogue</h3><label for="title">Video title</label><input id="title" maxlength="95"><div id="lines"></div><button class="subtle" onclick="addLine()">+ Add dialogue line</button><div class="row"><button class="subtle" onclick="saveProject()">Save project</button><button type="button" onclick="showStage('setup')">Continue to video setup</button></div></div></section><section class="panel" id="production-panel" hidden><h2>Voice &amp; gameplay</h2><label for="voice">Narrator voice</label><select id="voice"><option value="en-US-AriaNeural">Aria — conversational woman (US)</option><option value="en-US-JennyNeural">Jenny — friendly woman (US)</option><option value="en-US-MichelleNeural">Michelle — warm woman (US)</option><option value="en-US-GuyNeural">Guy — storyteller man (US)</option><option value="en-US-DavisNeural">Davis — deeper man (US)</option><option value="en-US-ChristopherNeural">Christopher — announcer man (US)</option><option value="en-GB-SoniaNeural">Sonia — woman (British)</option><option value="en-GB-RyanNeural">Ryan — man (British)</option><option value="windows-default">Windows voice — offline fallback</option></select><p class="muted">Preview each voice before rendering. ElevenLabs voices can use credits.</p><label for="secondVoice">Second character voice</label><select id="secondVoice"><option value="en-US-GuyNeural">Guy — storyteller man (US)</option><option value="en-US-DavisNeural">Davis — deeper man (US)</option><option value="en-US-JennyNeural">Jenny — friendly woman (US)</option><option value="en-US-AriaNeural">Aria — conversational woman (US)</option></select><p class="muted">Other speakers use the second voice.</p><label for="primarySpeaker">Assign main voice</label><select id="primarySpeaker"><option value="">First speaker automatically</option></select><label for="voiceRate">Speaking speed</label><select id="voiceRate"><option value="-15">Slower</option><option value="0" selected>Normal</option><option value="10">A little faster</option><option value="20">Fast</option></select><div class="row"><button class="subtle" id="previewVoiceBtn" onclick="previewVoice()">▶ Preview main voice</button><button class="subtle" onclick="previewVoice(true)">▶ Preview second voice</button></div><audio id="voicePreview" controls preload="none" style="display:none;width:100%;margin-top:12px"></audio><div class="voice-note" id="voiceAvailability">Voice studio: loading...</div><p class="muted">Natural voices are the default and require internet. If a natural voice is unavailable, the render stops with an error instead of silently switching to a robotic voice. Always preview before rendering.</p><label class="checkboxline"><input type="checkbox" id="fitDuration" checked>Fit to target length by adjusting automatic gaps</label><label for="pacing">Dialogue pacing</label><select id="pacing"><option value="snappy">Snappy</option><option value="normal" selected>Normal</option><option value="deliberate">Deliberate</option></select><label class="checkboxline"><input type="checkbox" id="exportCaptions" checked>Export separate timed captions (.srt)</label><details class="help"><summary>Music and sound effects</summary><label for="musicTrack">Background music</label><select id="musicTrack"><option value="">No music</option></select><label for="musicFile">Import licensed audio</label><input id="musicFile" type="file" accept=".mp3,.wav,.m4a,.aac,.ogg,.flac"><label class="checkboxline"><input type="checkbox" id="musicLicensed">I have permission to use this track in my videos</label><button type="button" class="subtle" onclick="importMusic()">Import track</button><p class="muted" id="musicStatus" role="status"></p><label for="voiceVolume">Voice level (%)</label><input id="voiceVolume" type="number" min="25" max="150" value="100"><label for="musicVolume">Music level (%)</label><input id="musicVolume" type="number" min="0" max="100" value="22"><p class="muted">Music lowers during speech. Add a message sound, impact, or transition to any dialogue line.</p></details><label for="resolution">Export resolution (30 FPS)</label><select id="resolution"><option value="1080p" selected>1080 × 1920</option><option value="720p">720 × 1280 · faster</option></select><label for="gameplayChoice">Gameplay library (only clips you own or may use)</label><select id="gameplayChoice"><option value="">No saved gameplay</option><option value="auto">Automatic — rotate saved clips</option></select><div class="row"><button class="subtle" onclick="refreshGameplay()">Refresh gameplay library</button></div><div class="mini-grid"><div><label for="gameplayPercent">Gameplay height</label><select id="gameplayPercent"><option value="55">55%</option><option value="58" selected>58%</option><option value="60">60%</option></select></div><div><label for="gameplayAnchor">Gameplay crop</label><select id="gameplayAnchor"><option value="center">Center</option><option value="top">Top</option><option value="bottom">Bottom</option></select></div></div><label for="gameplayFile">Add gameplay to library</label><input id="gameplayFile" type="file" accept="video/mp4,video/quicktime,video/webm,video/x-matroska"><label for="gameplayTag">Game or category</label><input id="gameplayTag" placeholder="Minecraft parkour, GTA driving, etc."><div class="row"><button class="subtle" onclick="saveGameplay()" id="gameplaySave">Save gameplay</button></div><p class="muted" id="gameplayStatus">Saved footage is kept on this PC and reused across Shorts. Dialogue appears above gameplay, which fills the lower 58% of the video.</p><label for="background">Or upload gameplay for just this video</label><input id="background" type="file" accept="video/mp4,video/quicktime,video/webm,video/x-matroska"><p class="muted">Leave both blank for the original dark story layout. Saved footage takes priority over the one-time upload.</p></section></div><aside class="panel monitor" aria-label="Video preview"><div class="section-head"><h2>Preview</h2><span class="eyebrow">Vertical video</span></div><div class="row"><button onclick="showLayoutPreview(0)" id="layoutBtn" type="button" class="subtle">Preview layout</button><button onclick="renderVideo()" id="renderBtn" disabled>Render MP4</button></div><div id="status" role="status">Enter an idea to begin.</div><progress id="progress" max="100" value="0" style="display:none" aria-label="Render progress"></progress><div class="canvas"><div id="emptyMonitor">Generate a story to preview its layout here.</div><section class="help layout-preview" id="layoutPreview" hidden><div class="row"><button onclick="showLayoutPreview(-1)" id="layoutPrevious" type="button" class="subtle">Previous line</button><button onclick="showLayoutPreview(1)" id="layoutNext" type="button" class="subtle">Next line</button></div><p id="layoutStatus" class="muted" role="status"></p><img id="layoutImage" alt="Vertical dialogue layout preview" hidden></section><div id="videoSection"><video id="preview" controls playsinline></video><details id="previewCaptionReview" class="help caption-review" hidden><summary>Review caption timing</summary><div></div></details><div class="row"><a class="button" id="download" download>Download MP4</a><button type="button" onclick="reviewExport()">Review export</button><a class="button subtle" id="downloadCaptions" download>Download timed captions</a></div><label for="description">YouTube description (copy after reviewing)</label><textarea id="description" rows="7" readonly></textarea><div class="row"><button class="subtle" onclick="navigator.clipboard.writeText(document.getElementById('description').value)">Copy description</button></div></div></div></aside></div></main><section class="panel" id="integration-center" data-view-panel="settings" hidden style="max-width:1000px;margin:24px auto"><h2>Settings &amp; connections</h2><div id="health" class="muted"></div><details class="launch-checks" id="launchChecks"><summary>Render readiness</summary><div id="launchCheckItems" aria-live="polite"></div><p class="muted" id="launchCheckNote"></p><button class="subtle" onclick="checkProductionReadiness()">Run checks again</button></details><p class="muted">See which tools are configured, then run a read-only connection test. No keys are shown, no credits are used, no files are sent and nothing gets published. A green connection does not guarantee successful generation.</p><div class="row"><button type="button" class="subtle" onclick="refreshIntegrationStatus(false)">Refresh setup</button><button type="button" onclick="refreshIntegrationStatus(true)">Test provider connections</button></div><div id="integrationStatus" class="integration-health-grid" role="status" aria-live="polite">Checking available integrations…</div><section class="help" style="margin-top:18px"><h3>Connect ElevenLabs</h3><p class="muted">Use your own ElevenLabs API key. We verify it with ElevenLabs first, then store it only in the local .env file. Previewing or rendering premium narration may consume ElevenLabs credits.</p><label for="elevenKey">ElevenLabs API key</label><input id="elevenKey" type="password" autocomplete="off" placeholder="Paste your ElevenLabs API key"><button type="button" onclick="connectElevenLabs()">Connect ElevenLabs</button><div class="row"><select id="elevenVoices" aria-label="ElevenLabs voice"><option value="">Connect account to list voices</option></select><button type="button" class="subtle" onclick="saveElevenVoice()">Use selected voice</button></div><p id="elevenFeedback" class="muted">Not tested with your account yet.</p></section><section class="help" style="margin-top:18px"><h3>Private storage</h3><p class="muted">A publishing kit works on this PC. Optional S3-compatible storage creates a temporary link you can give to a supported uploader.</p><div id="handoffDiagnostics" role="status"></div><div class="row"><button type="button" class="subtle" onclick="checkHandoff(false)">Check configuration</button><button type="button" class="subtle" onclick="checkHandoff(true)">Test connection</button></div></section><section class="help" style="margin-top:18px"><h3>App Updates</h3><p class="muted">Check for verified updates without downloading a ZIP manually. Configure SHADOW_UPDATE_FEED once in .env; close the app and run update_windows.bat to install. Your work stays on this PC.</p><button type="button" class="subtle" onclick="checkShadowUpdates()">Check for updates</button><p id="shadowUpdateStatus" class="muted">Update source not yet checked.</p></section></section><section class="panel library-panel" id="library-panel" data-view-panel="library"><div class="section-head"><div><span class="eyebrow">YOUR WORKSPACE</span><h2>Review Queue & Video Library</h2><p class="muted">Review videos, approve the ones you want to publish, and download an upload kit containing MP4s and descriptions.</p></div><button class="subtle" onclick="refreshLibrary()">↻ Refresh library</button></div><div id="libraryEmpty" class="muted">Loading your completed videos…</div><div id="libraryGrid" class="library-grid"></div><div class="row"><a class="button" href="/api/publishing-kit" download>↓ Download approved upload kit</a></div><div id="libraryDetails" hidden><h3 id="libraryTitle"></h3><video id="libraryPlayer" controls playsinline preload="metadata"></video><details id="libraryCaptionReview" class="help caption-review" hidden><summary>Review caption timing</summary><div></div></details><label for="reviewState">Review decision</label><select id="reviewState"><option value="needs_review">Needs review</option><option value="needs_changes">Needs changes</option><option value="approved">Approved for upload kit</option></select><label for="reviewNotes">Review notes</label><textarea id="reviewNotes" rows="2" placeholder="Optional notes before publishing..."></textarea><div class="row"><button class="subtle" onclick="runQualityCheck()">Run video quality check</button><button id="saveReviewBtn" onclick="saveReview()">Save review decision</button></div><div class="help" id="qualityPanel" role="status">Before approving, run a technical check. A passing check does not replace watching the complete video.</div><p class="muted" id="reviewFeedback">Approval only adds the video to your upload kit. Nothing is sent to YouTube.</p><label for="libraryDescription">YouTube description</label><textarea id="libraryDescription" readonly rows="5"></textarea><div class="row"><a id="libraryDownload" class="button" download>Download video</a><a id="subtitleDownload" class="button subtle" download hidden>↓ Download captions (.srt)</a><a id="handoffDownload" class="button" download>Download publishing kit</a><button class="subtle" id="cloudHandoffBtn" onclick="createCloudHandoff()">Create private link</button><button class="subtle" onclick="navigator.clipboard.writeText(el('libraryDescription').value)">Copy description</button></div><div id="cloudHandoffResult" class="help muted" role="status" hidden></div><p id="handoffNext" class="muted"></p></div></section><section class="panel" data-view-panel="tools" id="split-panel" style="max-width:860px;margin:24px auto" hidden>

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
</section><section class="panel" id="automation-panel" data-view-panel="automation" style="max-width:1100px;margin:0 auto 30px"><h2>5. Automation & Media Tools</h2><p class="muted">Queue up to eight Shorts with the production settings from Create. Finished videos go to Review Library.</p><label for="batchCount">Number of random stories</label><input type="number" id="batchCount" value="3" min="1" max="8"><button id="randomBatchBtn" onclick="startRandomBatch()" type="button">Queue random stories</button><label>Batch story ideas — one idea per line</label><textarea id="batchIdeas" placeholder="My friend found a secret note...&#10;The wedding invitation had the wrong name..." rows="4"></textarea><div class="help"><strong>Tonight’s quick production: three complete original stories</strong><p class="muted">Use three complete starter scripts with your current voices, gameplay, captions, pacing and music.</p><button id="readyBatchBtn" onclick="startReadyBatch()">▶ Render all 3 ready stories</button></div><div class="row"><button onclick="startBatch()" id="batchBtn">Generate batch</button><button class="subtle" onclick="refreshJobs()">Refresh jobs</button></div><div id="batchStatus" class="muted"></div><div id="jobList"></div><hr style="border:0;border-top:1px solid #303b50;margin:26px 0"><h2 id="clipper-panel">6. Basic video clipper</h2><p class="muted">Trim a video you own or have permission to use. This is a manual clipper, not AI highlight detection.</p><label>Source video</label><input type="file" id="clipFile" accept="video/*"><label for="clipUrl">Or public YouTube / Twitch URL</label><input type="url" id="clipUrl" placeholder="https://clips.twitch.tv/..."><label class="muted"><input type="checkbox" id="clipRights" style="width:auto"> I have the right to use this footage</label><div class="row"><div><label>Start second</label><input type="number" id="clipStart" min="0" step="0.1" value="0"></div><div><label>End second</label><input type="number" id="clipEnd" min="0.1" step="0.1" value="30"></div></div><div class="row"><button onclick="createClip()" id="clipBtn">Make vertical clip</button></div><div id="clipStatus" class="muted"></div><div id="clipLink"></div><div class="help muted">Nothing is uploaded to YouTube automatically. Videos need your approval and a connected publishing integration.</div></section><section class="panel assist-panel" data-view-panel="assistant" id="assistant-panel" style="max-width:1100px;margin:0 auto 30px"><span class="eyebrow">OWNER APPROVAL</span><h2>ChatGPT + YouTube handoff</h2><p class="muted">Start with an approved video from Review Library. Download the publishing kit and upload in YouTube Studio, or use a connected tool that can prepare a draft. Confirm visibility and publish in that service.</p><label for="assistTitle">Video title</label><input id="assistTitle" placeholder="Choose a finished video from Library, or enter a title"><label for="assistDesc">Description</label><textarea id="assistDesc" placeholder="Paste or edit the video description"></textarea><label for="assistNotes">What should ChatGPT help with?</label><select id="assistNotes"><option value="Review this video and improve its title, description, and hook">Review and optimize</option><option value="Prepare a YouTube Shorts publishing draft for ShadowChronicles and show it to me for approval">Prepare publishing draft</option><option value="Suggest improvements to the next three episodes">Plan follow-up stories</option></select><div class="row"><a class="button" href="https://studio.youtube.com/" target="_blank" rel="noopener noreferrer">Open YouTube Studio</a><button onclick="copyAssistantRequest()">Copy request for ChatGPT</button><button class="subtle" onclick="switchView('library')">Choose video from Library</button></div><div class="help muted" id="assistStatus">To send a completed MP4 to ChatGPT, attach the downloaded file in your chat after copying the request. A separate read-only private MCP connector is included in v0.20. It must be configured and connected from ChatGPT on web; it does not send messages into this conversation automatically.</div><div class="help"><strong>Download and upload workflow</strong><p>Open a completed video in Library, run its quality check, and mark it approved. Download the publishing kit for the MP4, captions when available, and the title and description. Use YouTube Studio or a connected uploader.</p><p class="muted">Optional private link handoff: configure S3-compatible storage in .env, then approve a video and click Generate private video link. Or enable auto-upload on approval. No tokens are included in any exported package.</p></div><h3 style="margin-top:26px">Bring a ChatGPT story into Shadow Studio</h3><p class="muted">Ask ChatGPT to write a story as JSON using this format: <code>{"title":"...","lines":[{"speaker":"Me","text":"..."}]}</code>. Paste its response here, then import the editable dialogue into Create.</p><textarea id="assistantImport" placeholder='{"title":"My shocking story","lines":[{"speaker":"Me","text":"You won’t believe this..."}]}'></textarea><div class="row"><button class="subtle" onclick="importAssistantStory()">Import story JSON</button></div><h3 style="margin-top:26px">Publishing draft queue</h3><p class="muted">Select a finished video in Library, approve it after the quality check, then return here to stage its title and description. Nothing will upload to YouTube.</p><label for="draftVideoId">Approved video ID</label><input id="draftVideoId" placeholder="Choose a video in Library first" readonly><label for="draftPrivacy">Desired YouTube visibility</label><select id="draftPrivacy"><option value="private">Private</option><option value="unlisted">Unlisted</option><option value="public">Public — still a draft only</option></select><div class="row"><button onclick="stagePublishingDraft()">Save publishing draft</button><button class="subtle" onclick="refreshPublishingDrafts()">Refresh drafts</button></div><div class="help" id="draftFeedback">Drafts are stored locally; no platform account is connected.</div><div id="draftQueue" class="muted"></div><h3 style="margin-top:26px">Publishing safety</h3><p class="muted">Draft → Review → Explicit approval → Publish using your connected YouTube/vidIQ service. No auto-publication or stored YouTube credentials in this release.</p></section><script>



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

function showStage(stage){
 if(stage==='setup')refreshSpeakerSlots();
 el('create-panel').hidden=stage!=='story';
 el('production-panel').hidden=stage!=='setup';
 for(const tab of document.querySelectorAll('[data-stage]')){
  if(tab.dataset.stage===stage)tab.setAttribute('aria-current','step');
  else tab.removeAttribute('aria-current');
 }
}
let latestExportId=null;
async function reviewExport(){
 if(!latestExportId)return;
 try{
  const item=await api('/api/library/'+latestExportId);
  await refreshLibrary();
  switchView('library');openLibraryItem(item);
 }catch(error){setStatus(error.message)}
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
  if(!data||typeof data.title!=='string'||!Array.isArray(data.lines)||data.lines.length<2||data.lines.length>24||data.title.length>95)throw Error('Use a title and 2–24 dialogue lines');
  for(const l of data.lines){if(typeof l.speaker!=='string'||!l.speaker.trim()||l.speaker.length>24||typeof l.text!=='string'||!l.text.trim()||l.text.length>240)throw Error('Each line needs a speaker (max 24) and text (max 240)')}
  el('title').value=data.title;el('lines').replaceChildren();data.lines.forEach(l=>addLine(l.speaker,l.text,l.pause_before));
  el('editor').style.display='block';el('renderBtn').disabled=false;
  switchView('create');setStatus('ChatGPT story imported. Review it before rendering.');
 }catch(e){el('assistStatus').textContent='Story import error: '+e.message}
}
async function refreshGameplay(){try{let r=await api('/api/gameplay');let sel=el('gameplayChoice');let current=sel.value;sel.replaceChildren(new Option('No saved gameplay',''),new Option('Automatic — rotate saved clips','auto'));for(let c of r.clips){sel.add(new Option(`${c.tag} · ${c.duration.toFixed(0)}s · used ${c.uses}x`,c.id))}if([...sel.options].some(o=>o.value===current))sel.value=current;el('gameplayStatus').textContent=`${r.clips.length} saved gameplay video(s). Auto selects the least-used footage.`;}catch(e){el('gameplayStatus').textContent='Gameplay library error: '+e.message}}
async function saveGameplay(){let f=el('gameplayFile').files[0];if(!f){el('gameplayStatus').textContent='Select a video first.';return}el('gameplaySave').disabled=true;try{let fd=new FormData();fd.append('file',f);fd.append('tag',el('gameplayTag').value||'general');el('gameplayStatus').textContent='Uploading footage...';let r=await api('/api/gameplay',{method:'POST',body:fd});await refreshGameplay();el('gameplayChoice').value=r.id;el('gameplayStatus').textContent=`Saved ${r.tag} (${Math.round(r.duration)}s). Selected for next render.`}catch(e){el('gameplayStatus').textContent='Upload error: '+e.message}finally{el('gameplaySave').disabled=false}}

let story=null;const el=id=>document.getElementById(id);const setStatus=s=>el('status').textContent=s;
async function api(path,options){
 const response=await fetch(path,options);
 const body=await response.json().catch(()=>null);
 if(!response.ok){
  const detail=body?.detail;
  const message=Array.isArray(detail)?detail.map(entry=>entry.msg).join(' · '):detail;
  throw Error(message||('Request failed ('+response.status+'). Try again.'));
 }
 return body;
}
async function loadStarter(i){
 try{
  const d=await api('/api/starter-stories');const x=d.stories[i];if(!x)throw Error('Not found');
  el('title').value=x.title;el('lines').replaceChildren();x.lines.forEach(l=>addLine(l.speaker,l.text,l.pause_before));
  el('editor').style.display='block';el('renderBtn').disabled=false;
  el('idea').value=x.title;setStatus('Complete original story loaded. Review it, preview the voice, and render.');
 }catch(e){setStatus('Starter story error: '+e.message)}
}
async function showStarters(){try{let d=await api('/api/starter-stories');const box=el('starterList');box.replaceChildren();d.stories.forEach((v,i)=>{const b=document.createElement('button');b.className='subtle';b.textContent='Load story '+(i+1);b.title=v.title;b.onclick=()=>loadStarter(i);box.append(b)});}catch(e){}}
const premiseVariants=new Map();
async function createStory(random=false){
 const buttons=[el('storyBtn'),el('randomStoryBtn'),el('quickBtn'),el('regenerateBtn')];
 buttons.forEach(button=>button.disabled=true);
 setStatus('Creating script...');
 try{
  const request={idea:el('idea').value,previous_title:el('title').value,style:el('style').value,target:Number(el('target').value),ending:el('ending').value};
  request.mode=random?'random':'custom';
  request.genre=el('genre').value;
  request.provider=el('storyProvider').value;
  const premise=request.idea.trim().toLowerCase();
  request.avoid=premiseVariants.get(premise)||[];
  const result=await api('/api/story',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});
  story=result.story;
  if(story.variant)premiseVariants.set(premise,[...request.avoid,story.variant].slice(-8));
  el('title').value=story.title;
  el('lines').replaceChildren();
  story.lines.forEach(line=>addLine(line.speaker,line.text,line.pause_before,line.alternate,line.sound_effect,line.sound_offset));
  el('editor').style.display='block';
  el('renderBtn').disabled=false;
  refreshSpeakerSlots();
  setStatus('Script ready. Read through the conversation, then choose your voices and gameplay.');
  return true;
 }catch(error){setStatus('Error: '+error.message);return false}
 finally{buttons.forEach(button=>button.disabled=false)}
}

function addLine(s='Me',t='',pause=0,alternate,effect='none',offset=0){
 const row=document.createElement('div');row.className='line';
 const speaker=document.createElement('input');speaker.addEventListener('change',()=>refreshSpeakerSlots());speaker.value=s;speaker.maxLength=24;speaker.placeholder='Speaker';speaker.setAttribute('aria-label','Speaker');
 const dialogue=document.createElement('textarea');dialogue.rows=2;dialogue.value=t;dialogue.maxLength=240;dialogue.placeholder='Dialogue';dialogue.setAttribute('aria-label','Dialogue');
 const timing=document.createElement('label');timing.textContent='Pause before (s)';
 const seconds=document.createElement('input');seconds.type='number';seconds.min='0';seconds.max='10';seconds.step='0.1';seconds.value=pause;timing.append(seconds);
 const remove=document.createElement('button');remove.textContent='×';remove.setAttribute('aria-label','Remove line');remove.onclick=()=>{row.remove();refreshSpeakerSlots()};
 const order=document.createElement('div');order.className='line-order';
 for(const [direction,label] of [['up','↑'],['down','↓']]){
  const move=document.createElement('button');move.type='button';move.textContent=label;move.setAttribute('aria-label','Move line '+direction);
  move.onclick=()=>{
   if(direction==='up' && row.previousElementSibling)row.previousElementSibling.before(row);
   if(direction==='down' && row.nextElementSibling)row.nextElementSibling.after(row);
   move.focus();
  };
  order.append(move);
 }
 row.append(speaker,dialogue,timing,order,remove);
 if(alternate){
  const regenerate=document.createElement('button');regenerate.className='subtle';
  regenerate.textContent='Swap wording';
  row.dataset.alternate=alternate;
  regenerate.onclick=()=>{const current=dialogue.value;dialogue.value=row.dataset.alternate;row.dataset.alternate=current};
  row.append(regenerate);
 }
 const rewrite=document.createElement('button');
 rewrite.className='subtle ollama-rewrite';rewrite.type='button';rewrite.textContent='Rewrite with Ollama';
 rewrite.onclick=async()=>{
  rewrite.disabled=true;
  try{
   const source=editedLines();
   const lineIndex=Array.from(el('lines').children).indexOf(row);
   const original=dialogue.value;
   setStatus('Rewriting this line locally…');
   const result=await api('/api/story/rewrite-line',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    story:{title:el('title').value,lines:source},line_index:lineIndex
   })});
   if(row.isConnected && dialogue.value===original){
    dialogue.value=result.text;
    setStatus('Dialogue line rewritten. Review it before rendering.');
   }else{
    setStatus('The line changed while Ollama was working. Your current text was kept.');
   }
  }catch(error){setStatus(error.message)}
  finally{rewrite.disabled=false}
 };
 row.append(rewrite);
 const sound=document.createElement('details');
 const summary=document.createElement('summary');summary.textContent='Sound effect';
 const choices=document.createElement('select');choices.className='lineEffect';choices.setAttribute('aria-label','Sound effect');
 for(const [value,label] of [['none','No sound'],['message','Message'],['impact','Impact'],['transition','Transition']]){
  choices.add(new Option(label,value));
 }
 choices.value=effect;
 const delay=document.createElement('label');delay.textContent='Offset from speech (s)';
 const shift=document.createElement('input');shift.type='number';shift.className='lineOffset';shift.min='-2';shift.max='2';shift.step='0.1';shift.value=offset;
 delay.append(shift);sound.append(summary,choices,delay);row.append(sound);
 el('lines').append(row);
}
function editedLines(){
 return Array.from(el('lines').children).map(row=>{
  const pause=row.querySelector('input[type=number]');
  if(!pause.reportValidity())throw Error('Enter a pause between 0 and 10 seconds.');
  const offset=row.querySelector('.lineOffset');
  if(!offset.checkValidity())throw Error('Sound effect offsets must be between -2 and 2 seconds.');
  return {speaker:row.children[0].value.trim(),text:row.children[1].value.trim(),pause_before:Number(pause.value),alternate:row.dataset.alternate,
   sound_effect:row.querySelector('.lineEffect').value,sound_offset:Number(offset.value)};
 });
}



async function connectElevenLabs(){const out=el('elevenFeedback');const key=el('elevenKey').value;out.textContent='Checking account…';try{const d=await api('/api/elevenlabs/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:key})});el('elevenKey').value='';fillElevenVoices(d.voices,d.selected_voice);out.textContent='ElevenLabs connected. Voices now available in Create.';await refreshMainVoices();refreshIntegrationStatus(false);}catch(e){out.textContent='Connection failed: '+e.message;}}
function fillElevenVoices(voices,selected){const sel=el('elevenVoices');sel.replaceChildren();for(const v of voices){let o=document.createElement('option');o.value=v.id;o.textContent=v.name;sel.appendChild(o);}if(selected)sel.value=selected;}
async function saveElevenVoice(){const id=el('elevenVoices').value;if(!id){el('elevenFeedback').textContent='Connect ElevenLabs first';return;}try{await api('/api/elevenlabs/choose',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({voice_id:id})});el('elevenFeedback').textContent='Saved. Choose your ElevenLabs voice in Create.';await refreshMainVoices();}catch(e){el('elevenFeedback').textContent=e.message;}}
async function checkShadowUpdates(){const result=el('shadowUpdateStatus');result.textContent='Checking...';try{const data=await api('/api/updater/check');result.textContent=(data.update_available?'New version '+data.latest+': ':'')+data.message;}catch(e){result.textContent='Update check failed: '+e.message;}}
async function previewVoice(second=false){
 const select=el(second?'secondVoice':'voice');
 if(select.selectedOptions[0]?.disabled){setStatus('Choose an available voice before previewing.');return}
 if(select.value.startsWith('elevenlabs:')&&!confirm('Preview this ElevenLabs voice? This may use credits.'))return;
 let b=el('previewVoiceBtn');b.disabled=true;setStatus('Generating voice preview...');try{let r=await fetch('/api/voice-preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({voice:el(second?'secondVoice':'voice').value,rate:Number(el('voiceRate').value),allow_premium:true})});if(!r.ok){let d=await r.json();throw Error(d.detail||'Preview failed')}let blob=await r.blob();let url=URL.createObjectURL(blob);let audio=el('voicePreview');if(audio.dataset.url)URL.revokeObjectURL(audio.dataset.url);audio.src=url;audio.dataset.url=url;audio.style.display='block';await audio.play();setStatus('Voice preview ready.')}catch(e){setStatus('Voice error: '+e.message)}finally{b.disabled=false}}
async function quickGenerate(){
 if(el('renderBtn').disabled && el('progress').style.display==='block'){
  setStatus('Wait for the current render to finish.');
  return;
 }
 if(!confirm('Generate a new story and replace the current script in the editor? Save the script first if you want to keep it.'))return;
 if(await createStory(!el('idea').value.trim()))await renderVideo(true);
}
async function refreshMusic(selected=el('musicTrack').value){
 try{
  const result=await api('/api/music');
  const select=el('musicTrack');
  select.replaceChildren(new Option('No music',''));
  for(const track of result.tracks)select.add(new Option(track.label,track.name));
  select.value=[...select.options].some(option=>option.value===selected)?selected:'';
  el('musicStatus').textContent=selected && !select.value?'Saved music file is unavailable.':'';
 }catch(error){el('musicStatus').textContent=error.message}
}
async function importMusic(){
 const file=el('musicFile').files[0];
 const licensed=el('musicLicensed').checked;
 if(!file || !licensed){el('musicStatus').textContent='Choose an audio file and confirm your permission.';return}
 const form=new FormData();form.append('file',file);form.append('licensed',String(licensed));
 el('musicStatus').textContent='Importing audio…';
 try{
  const result=await api('/api/music',{method:'POST',body:form});
  await refreshMusic(result.name);
  el('musicFile').value='';el('musicLicensed').checked=false;
 }catch(error){el('musicStatus').textContent=error.message}
}
refreshMusic();
function confirmPremium(settings, action){
 if(['voice','secondVoice'].some(id=>el(id).selectedOptions[0]?.disabled))throw Error('Choose available voices before rendering.');
 return ![settings.voice,settings.second_voice].some(voice=>voice.startsWith('elevenlabs:')) ||
  confirm(action+' with ElevenLabs? This may use credits.');
}
function refreshSpeakerSlots(preferred=el('primarySpeaker').value){
 const field=el('primarySpeaker');
 const names=[...new Set(Array.from(el('lines').children, row=>row.children[0].value.trim()).filter(Boolean))];
 field.replaceChildren(new Option('First speaker automatically',''));
 for(const name of names)field.add(new Option(name,name));
 field.value=names.includes(preferred)?preferred:'';
}
function productionSettings(){
 if(!el('voiceVolume').checkValidity() || !el('musicVolume').checkValidity())throw Error('Enter a voice level of 25–150% and music level of 0–100%.');
 return {style:el('style').value,voice:el('voice').value,second_voice:el('secondVoice').value,
  primary_speaker:el('primarySpeaker').value||null,voice_rate:Number(el('voiceRate').value),resolution:el('resolution').value,
  gameplay_id:el('gameplayChoice').value||null,gameplay_percent:Number(el('gameplayPercent').value),gameplay_anchor:el('gameplayAnchor').value,pacing:el('pacing').value,captions:el('exportCaptions').checked,
  music_name:el('musicTrack').value||null,voice_volume:Number(el('voiceVolume').value),music_volume:Number(el('musicVolume').value)};
}
const localBackgrounds=new WeakMap();
async function readyBackground(file){
 if(!localBackgrounds.has(file)){
  const form=new FormData();
  form.append('file',file);
  localBackgrounds.set(file,api('/api/upload-background',{method:'POST',body:form}));
 }
 try{return (await localBackgrounds.get(file)).name}
 catch(error){localBackgrounds.delete(file);throw error}
}
async function renderVideo(automatic=false){
 let settings;
 try{
  settings=productionSettings();
  if(!confirmPremium(settings,'Render this video'))return;
 }catch(error){setStatus(error.message);return}
 el('renderBtn').disabled=true;
 el('videoSection').style.display='none';
 el('emptyMonitor').hidden=false;el('emptyMonitor').textContent='Rendering your Short…';
 el('layoutPreview').hidden=true;
 el('progress').value=0;el('progress').style.display='block';
 setStatus('Preparing...');
 try{
  let background=null;
  const file=settings.gameplay_id?null:el('background').files[0];
  if(file){
   setStatus('Uploading your background...');
   background=await readyBackground(file);
  }else if(automatic && !settings.gameplay_id){
   settings.gameplay_id='auto';
  }
  const request={...settings,allow_premium:true,target_seconds:el('fitDuration').checked?Number(el('target').value):null,story:{title:el('title').value,lines:editedLines()},background_name:background};
  const result=await api('/api/render',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});
  poll(result.id);
 }catch(error){setStatus('Error: '+error.message);el('renderBtn').disabled=false}
}

let layoutLine=0;
let layoutUrl=null;
let layoutBusy=false;
async function showLayoutPreview(step){
 if(layoutBusy)return;
 const status=el('layoutStatus');
 const image=el('layoutImage');
 const panel=el('layoutPreview');
 panel.hidden=false;
 el('emptyMonitor').hidden=true;el('videoSection').style.display='none';
 try{
  const lines=editedLines();
  if(lines.length<2)throw Error('Add at least two dialogue lines before previewing.');
  const line=Math.max(0,Math.min(lines.length-1,layoutLine+step));
  const gameplayId=el('gameplayChoice').value||null;
  const file=gameplayId?null:el('background').files[0];
  layoutBusy=true;
  el('layoutPrevious').disabled=true;el('layoutNext').disabled=true;
  status.textContent=file?'Preparing local gameplay preview…':'Drawing layout…';
  const background_name=file?await readyBackground(file):null;
  const response=await fetch('/api/layout-preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
   story:{title:el('title').value,lines},line_index:line,style:el('style').value,
   gameplay_id:gameplayId,background_name,resolution:el('resolution').value,
   gameplay_percent:Number(el('gameplayPercent').value),gameplay_anchor:el('gameplayAnchor').value
  })});
  if(!response.ok){
   const data=await response.json();
   throw Error(typeof data.detail==='string'?data.detail:'Layout preview failed.');
  }
  const preview=URL.createObjectURL(await response.blob());
  if(layoutUrl)URL.revokeObjectURL(layoutUrl);
  layoutUrl=preview;image.src=preview;image.hidden=false;
  layoutLine=line;
  status.textContent='Line '+(line+1)+' of '+lines.length;
  el('layoutPrevious').disabled=line===0;el('layoutNext').disabled=line>=lines.length-1;
 }catch(error){
  image.hidden=true;
  status.textContent=error.message;
 }finally{layoutBusy=false}
}
async function attachCaptionReview(video, panel, id, available){
 video.querySelectorAll('track').forEach(track=>track.remove());
 panel.dataset.job=id;
 panel.hidden=true;
 panel.open=false;
 const list=panel.querySelector('div');
 list.replaceChildren();
 if(!available)return;
 const endpoint='/api/captions-review/'+encodeURIComponent(id);
 const track=document.createElement('track');
 track.kind='captions';track.label='English';track.srclang='en';track.src=endpoint+'?webvtt=true';
 video.append(track);
 try{
  const result=await api(endpoint);
  if(panel.dataset.job!==id)return;
  for(const cue of result.cues){
   const button=document.createElement('button');button.type='button';
   const time=document.createElement('time');
   time.textContent=Math.floor(cue.start/60)+':'+(cue.start%60).toFixed(1).padStart(4,'0');
   button.append(time,document.createTextNode(cue.text));
   button.onclick=()=>{video.currentTime=cue.start;track.track.mode='showing'};
   list.append(button);
  }
  panel.hidden=!result.cues.length;
 }catch(error){
  if(panel.dataset.job===id){
   list.textContent='Caption timeline unavailable: '+error.message;
   panel.hidden=false;
  }
 }
}
async function poll(id){try{let d=await api('/api/job/'+id);setStatus(d.message+(d.state==='running'?' • '+d.percent+'%':''));el('progress').value=d.percent;if(d.state==='complete'){latestExportId=id;el('emptyMonitor').hidden=true;el('layoutPreview').hidden=true;el('preview').src=d.video_url;el('download').href=d.video_url;el('downloadCaptions').hidden=!d.captions_url;if(d.captions_url)el('downloadCaptions').href=d.captions_url;attachCaptionReview(el('preview'),el('previewCaptionReview'),id,d.captions_url);el('description').value=d.description;el('videoSection').style.display='block';el('renderBtn').disabled=false;refreshLibrary();const duration='Rendered '+d.duration+' s'+(d.target_seconds?' (target '+d.target_seconds+' s)':'')+'. ';const timing=d.target_seconds&&Math.abs(d.duration-d.target_seconds)>0.2?'Edit the script or pauses to get closer to your target. ':'';setStatus(duration+timing+'Review the full MP4 in Library before approval.');return}if(d.state==='failed'){el('emptyMonitor').textContent='Render failed. Check the message above and retry.';el('renderBtn').disabled=false;return}setTimeout(()=>poll(id),1500)}catch(e){setStatus('Status error: '+e.message);el('renderBtn').disabled=false}}
api('/api/health').then(d=>{el('lines').classList.toggle('ollama-available',Boolean(d.ollama_model));el('health').textContent=d.ffmpeg&&d.ffprobe?'✅ FFmpeg detected. Private local server ready. '+(d.ollama_model?'Ollama AI enabled.':'Offline story mode.'):'⚠️ FFmpeg not found. Install FFmpeg and restart the app.'}).catch(()=>el('health').textContent='Could not check local server.');

async function saveProject(){
 if(!el('lines').children.length){setStatus('Create a script first');return}
 try{const lines=editedLines();const d=await api('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...productionSettings(),title:el('title').value,idea:el('idea').value,provider:el('storyProvider').value,lines,target:Number(el('target').value),fit_duration:el('fitDuration').checked,genre:el('genre').value,ending:el('ending').value})});setStatus('Project saved: '+d.title);await refreshProjects();el('projects').value=d.id}catch(e){setStatus('Could not save: '+e.message)}
}
async function refreshProjects(){try{const d=await api('/api/projects');el('projects').replaceChildren(new Option('Choose a project...',''));d.projects.forEach(p=>el('projects').add(new Option(p.title,p.id)))}catch(e){setStatus('Projects: '+e.message)}}
async function loadProject(){
 const id=el('projects').value;if(!id)return;
 try{
  const project=await api('/api/projects/'+id);
  el('title').value=project.title;
  el('idea').value=project.idea||'';el('storyProvider').value=project.provider||'offline';
  if(project.voice)selectVoice('voice',project.voice);
  if(project.second_voice)selectVoice('secondVoice',project.second_voice);
  const fields={style:project.style,
   voiceRate:project.voice_rate,resolution:project.resolution,gameplayChoice:project.gameplay_id||'',gameplayPercent:project.gameplay_percent??58,gameplayAnchor:project.gameplay_anchor||'center',
   pacing:project.pacing||'normal',target:project.target||45,genre:project.genre||'random',ending:project.ending||'resolved'};
  for(const [field,value] of Object.entries(fields)){
   const select=el(field);
   if([...select.options].some(option=>option.value===String(value)))select.value=String(value);
  }
  el('exportCaptions').checked=project.captions??true;
  el('fitDuration').checked=project.fit_duration??true;
  await refreshMusic(project.music_name || '');
  el('voiceVolume').value=project.voice_volume??100;
  el('musicVolume').value=project.music_volume??22;
  el('lines').replaceChildren();
  project.lines.forEach(line=>addLine(line.speaker,line.text,line.pause_before,line.alternate,line.sound_effect,line.sound_offset));
  refreshSpeakerSlots(project.primary_speaker||'');
  el('editor').style.display='block';el('renderBtn').disabled=false;
  setStatus('Loaded saved project');
 }catch(error){setStatus(error.message)}
}

function startReadyBatch(){
 return queueBatch('/api/batch-ready',{story_indices:[0,1,2]});
}
function startBatch(){
 const ideas=el('batchIdeas').value.split('\n').map(value=>value.trim()).filter(Boolean);
 if(!ideas.length||ideas.length>8){el('batchStatus').textContent='Enter 1–8 story ideas';return}
 return queueBatch('/api/batch',{ideas});
}
function startRandomBatch(){
 const input=el('batchCount');
 if(!input.reportValidity()||!input.value){el('batchStatus').textContent='Enter a count from 1 to 8';return}
 return queueBatch('/api/batch-random',{count:Number(input.value),genre:el('genre').value});
}
async function queueBatch(endpoint, choices){
 const status=el('batchStatus');
 const buttons=[el('readyBatchBtn'),el('batchBtn'),el('randomBatchBtn')];
 try{
  const settings=productionSettings();
  if(!confirmPremium(settings,'Render this entire batch'))return;
  buttons.forEach(button=>button.disabled=true);
  const target=Number(el('target').value);
  const request={...settings,allow_premium:true,genre:el('genre').value,provider:el('storyProvider').value,...choices,target,ending:el('ending').value,
   target_seconds:el('fitDuration').checked?target:null};
  if(endpoint==='/api/batch-random'&&!request.gameplay_id)request.gameplay_id='auto';
  const queued=await api(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});
  while(true){
   const batch=await api('/api/job/'+queued.id);
   status.textContent=batch.message+' ('+batch.percent+'%)';
   await refreshJobs();
   if(batch.state!=='running'){
    if(batch.video_ids.length){
     status.textContent+=' · '+batch.video_ids.length+' video(s) in Review Library.';
     await refreshLibrary();
    }
    break;
   }
   await new Promise(resolve=>setTimeout(resolve,2500));
  }
 }catch(error){status.textContent='Batch error: '+error.message}
 finally{buttons.forEach(button=>button.disabled=false)}
}
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
 const videoId=selectedReviewId;
 if(!videoId)return;
 const box=el('qualityPanel');box.textContent='Checking the MP4…';
 try{
  const q=await api('/api/library/'+videoId+'/quality');
  if(selectedReviewId!==videoId)return;
  const lines=[q.passed?'Technical export: OK':'Technical export: BLOCKED',
   `${q.width}×${q.height} · ${q.fps} FPS · ${q.duration_seconds}s · ${q.file_size_mb} MB`,
   `Audio track: ${q.audio_present?'Present':'Missing'}`];
  if(q.max_volume_db!==null)lines.push(`Peak audio level (opening): ${q.max_volume_db} dB`);
  for(const b of q.blockers)lines.push('BLOCKER: '+b);
  for(const w of q.warnings)lines.push('CHECK: '+w);
  lines.push('Listen to the entire voiceover and confirm gameplay rights before approval.');
  box.textContent=lines.join('\n');box.style.whiteSpace='pre-line';
 }catch(e){if(selectedReviewId===videoId)box.textContent='Could not inspect video: '+e.message}
}
async function saveReview(){
 const videoId=selectedReviewId;
 if(!videoId)return;
 const btn=el('saveReviewBtn');btn.disabled=true;
 try{
  const updated=await api('/api/library/'+videoId+'/review',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:el('reviewState').value,notes:el('reviewNotes').value})});
  if(selectedReviewId!==videoId){await refreshLibrary();return}
  updateHandoffControls(updated);
  el('reviewFeedback').textContent=updated.review_status==='approved'?'Approved for handoff. Choose Download publishing kit.':'Review decision saved.';
  await refreshLibrary();
 }catch(e){if(selectedReviewId===videoId)el('reviewFeedback').textContent='Review save failed: '+e.message}
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
 el('libraryDownload').href=v.video_url;el('subtitleDownload').hidden=!v.captions_url;if(v.captions_url)el('subtitleDownload').href=v.captions_url;attachCaptionReview(el('libraryPlayer'),el('libraryCaptionReview'),v.id,v.captions_url);el('handoffDownload').href='/api/chatgpt-handoff/'+encodeURIComponent(v.id);
 updateHandoffControls(v);
 el('cloudHandoffResult').hidden=true;
 el('libraryDetails').scrollIntoView({behavior:'smooth',block:'nearest'});
}

function updateHandoffControls(video){
 const approved=video.review_status==='approved' && Boolean(video.approved_video_sha256);
 el('handoffDownload').hidden=!approved;
 el('cloudHandoffBtn').hidden=!approved;
 el('cloudHandoffBtn').textContent='Create private link';
 el('handoffNext').textContent=approved
  ?'Download the kit and upload in YouTube Studio, or create a private link for a supported uploader. You choose when to publish.'
  :'Watch the MP4, then approve this version to unlock the publishing kit.';
}
async function checkHandoff(live){
 const out=el('handoffDiagnostics');
 out.textContent=live?'Checking bucket access…':'Checking local setup…';
 try{
  if(live){
   const result=await api('/api/cloud-handoff/test',{method:'POST'});
   out.textContent=result.message;
   return;
  }
  const settings=await api('/api/cloud-handoff/config');
  out.textContent=settings.configured
   ?'Storage configured. Test the connection'+(settings.auto_upload_on_approval?' · automatic private transfer is enabled.':'.')
   :'To enable private transfer, set '+settings.missing.join(', ')+' in the local .env and restart Shadow Studio. Use a private bucket and add SHADOW_S3_ENDPOINT for S3-compatible providers.';
 }catch(error){out.textContent=error.message}
}
async function createCloudHandoff(){
 const videoId=selectedReviewId;
 if(!videoId)return;
 const btn=el('cloudHandoffBtn'),out=el('cloudHandoffResult');
 out.hidden=false;btn.disabled=true;out.textContent='Preparing approved video…';
 const path='/api/cloud-handoff/'+encodeURIComponent(videoId);
 try{
  let result=await api(path,{method:'POST'});
  while(['queued','checking','uploading'].includes(result.status)){
   if(selectedReviewId!==videoId)return;
   const percent=result.bytes_total?Math.round(result.bytes_sent/result.bytes_total*100):0;
   out.textContent=result.status==='uploading'?'Uploading to private storage · '+percent+'%':'Checking current video and approval…';
   await new Promise(resolve=>setTimeout(resolve,750));
   result=await api(path+'/status');
  }
  if(selectedReviewId!==videoId)return;
  if(result.status!=='uploaded')throw Error(result.error||result.message||'Private link unavailable.');
  out.replaceChildren();
  const intro=document.createElement('p');
  intro.textContent='Temporary link ready. Anyone who has this link can view the video. Use it with a supported uploader and review the draft before publishing.';
  const area=document.createElement('textarea');area.value=result.request_for_chatgpt;area.readOnly=true;area.rows=6;
  const copy=document.createElement('button');copy.textContent='Copy draft request';
  copy.onclick=async()=>{try{await navigator.clipboard.writeText(area.value);copy.textContent='Copied'}catch{area.select()}};
  out.append(intro,area,copy);
 }catch(error){
  if(selectedReviewId===videoId){
   out.textContent=error.message+' You can retry or download the publishing kit.';
   btn.textContent='Retry private link';
  }
 }finally{btn.disabled=false}
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
async function refreshJobs(){
 const list=el('jobList');
 try{
  const {jobs}=await api('/api/jobs');list.replaceChildren();
  if(!jobs.length){list.textContent='Your renders will appear here.';return}
  for(const job of jobs){
   const row=document.createElement('article');row.className='queue-job';
   const title=document.createElement('strong');title.textContent=job.title;
   const status=document.createElement('p');status.textContent=job.message;
   row.append(title,status);
   if(job.state==='running'){
    const progress=document.createElement('progress');progress.value=job.percent;progress.max=100;
    progress.setAttribute('aria-label','Render progress');row.append(progress);
   }
   if(job.video_url){
    const review=document.createElement('button');review.className='subtle';review.textContent='Review video';
    review.onclick=async()=>{latestExportId=job.id;await reviewExport()};row.append(review);
   }
   list.append(row);
  }
 }catch(error){list.textContent=error.message}
}
async function createClip(){el('clipBtn').disabled=true;try{el('clipStatus').textContent='Importing...';const u=await clipSource('clipFile','clipUrl','clipRights');el('clipStatus').textContent='Trimming...';const d=await api('/api/clip',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({background_name:u.name,start:Number(el('clipStart').value),end:Number(el('clipEnd').value),orientation:'vertical'})});const a=document.createElement('a');a.href=d.download_url;a.download='shadow_clip.mp4';a.textContent='Download vertical clip';a.style='color:#a5ccff';el('clipLink').replaceChildren(a);el('clipStatus').textContent='Clip ready'}catch(e){el('clipStatus').textContent='Clip error: '+e.message}finally{el('clipBtn').disabled=false}}
function selectVoice(id, value){
 const select=el(id);
 if(value && ![...select.options].some(option=>option.value===value)){
  const missing=new Option(value.startsWith('elevenlabs:')?'Unavailable saved ElevenLabs voice':'Unavailable saved voice',value);
  missing.disabled=true;
  select.add(missing);
 }
 if(value)select.value=value;
}

for(const id of ['voice','secondVoice']){
 try{
  const saved=localStorage.getItem('shadow-studio-voice-'+id);
  if(saved)selectVoice(id,saved);
 }catch{}
 el(id).addEventListener('change',()=>{
  try{localStorage.setItem('shadow-studio-voice-'+id,el(id).value)}catch{}
 });
}
async function refreshMainVoices(){
 const status=el('voiceAvailability');
 try{
  const data=await api('/api/voices');
  for(const id of ['voice','secondVoice']){
   const select=el(id);
   const current=select.value;
   select.replaceChildren();
   for(const voice of data.voices){
    select.add(new Option(voice.name+(voice.provider==='premium'?' • ElevenLabs credits':''),voice.id));
   }
   selectVoice(id,current||(id==='voice'?'en-US-AriaNeural':'en-US-GuyNeural'));
  }
  status.textContent=(data.neural_available?'✓ Free neural voices installed. ':'⚠️ Neural voices unavailable. ')+
   (data.premium_configured?'ElevenLabs account voices loaded. ':'')+(data.premium_error||'');
  if(['voice','secondVoice'].some(id=>el(id).selectedOptions[0]?.disabled)){
   status.textContent+=' A saved voice is unavailable. Reconnect ElevenLabs or pick another voice.';
  }
 }catch(error){status.textContent='Could not load voices: '+error.message}
}

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
refreshGameplay();checkHandoff(false);showStarters();refreshPublishingDrafts();refreshIntegrationStatus(false);switchView('create');</script></body></html>'''