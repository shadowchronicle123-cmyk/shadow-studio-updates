"""Owner-controlled local production tools. None posts content externally."""
from pathlib import Path
import subprocess, json, re, uuid, math
from PIL import Image, ImageDraw, ImageFont

MEDIA_ROOT=Path(__file__).resolve().parent/'advanced';MEDIA_ROOT.mkdir(exist_ok=True)

def run(cmd, timeout=180):
    p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=timeout)
    if p.returncode:raise RuntimeError((p.stderr or 'Processing failed')[-650:])
    return p

def media_duration(path):
    p=run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nokey=1:noprint_wrappers=1',str(path)],20)
    return float(p.stdout.strip())

def scene_candidates(path, threshold=.27, max_results=20):
    duration=media_duration(path)
    cmd=['ffmpeg','-hide_banner','-nostdin','-i',str(path),'-vf',f"scale=320:-2,select='gt(scene,{threshold})',showinfo",'-an','-vsync','vfr','-f','null','-']
    try:
        p=subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,timeout=110)
        times=[float(t) for t in re.findall(r'pts_time:([0-9.]+)',p.stderr)]
    except subprocess.TimeoutExpired:times=[]
    selected=[]
    for t in times:
        if t>2 and t<duration-2 and (not selected or t-selected[-1]>2):selected.append(t)
        if len(selected)>=max_results:break
    if not selected:
        # Silent/simple gameplay won't have cuts: fixed windows are fallback, NOT AI-selected highlights.
        selected=[round(float(x),2) for x in range(5,int(duration),20)]
    return {'duration':round(duration,2),'scene_changes':selected,'method':'scene-change detection; fixed intervals if no cuts','note':'Suggestions are visual cut points, not semantic viral-moment scoring.'}

def transcribe_local(path, model_name='tiny', lang='en'):
    try:from faster_whisper import WhisperModel
    except ImportError:raise RuntimeError('Install optional transcription dependency: python -m pip install faster-whisper')
    if model_name not in ('tiny','base','small'):raise ValueError('Unsupported model')
    model=WhisperModel(model_name,device='cpu',compute_type='int8')
    segments,info=model.transcribe(str(path),language=lang,vad_filter=True)
    transcript=[{'start':round(s.start,2),'end':round(s.end,2),'text':s.text.strip()} for s in segments]
    return {'segments':transcript,'language':info.language,'text':' '.join(x['text'] for x in transcript)}

def make_title_card(text,size=(720,1280)):
    f=MEDIA_ROOT/(uuid.uuid4().hex[:12]+'.png')
    img=Image.new('RGBA',size,(0,0,0,0));d=ImageDraw.Draw(img)
    # Text card in lower quarter, transparent otherwise. Pillow rasterization avoids ffmpeg drawtext escaping.
    xmargin=int(size[0]*.07); card=(xmargin,int(size[1]*.69),size[0]-xmargin,int(size[1]*.90))
    d.rounded_rectangle(card,radius=24,fill=(15,10,19,219),outline=(173,35,71,215),width=3)
    try:font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',int(size[0]*.046))
    except OSError:
        try:font=ImageFont.truetype('C:/Windows/Fonts/arialbd.ttf',int(size[0]*.046))
        except OSError:font=ImageFont.load_default()
    maxw=size[0]*.76; lines=[];line=''
    for w in text.split():
        attempt=(line+' '+w).strip()
        if d.textbbox((0,0),attempt,font=font)[2]>maxw and line:
            lines.append(line);line=w
        else:line=attempt
    if line:lines.append(line)
    lines=lines[:4]; lineh=int(size[0]*.058);y=(card[1]+card[3]-lineh*len(lines))//2
    for ln in lines:
        bbox=d.textbbox((0,0),ln,font=font);x=(size[0]-(bbox[2]-bbox[0]))//2
        d.text((x,y),ln,fill='#FFFFFF',font=font,stroke_width=1,stroke_fill='#120a13');y+=lineh
    img.save(f)
    return f

def edit_video(source,start,end,caption='',mute=False):
    duration=media_duration(source)
    if not(0<=start<end<=duration+.05):raise ValueError('Trim range exceeds video duration')
    if end-start>180:raise ValueError('Maximum editor duration is 180 seconds')
    f=MEDIA_ROOT/(uuid.uuid4().hex[:12]+'.mp4')
    cmd=['ffmpeg','-hide_banner','-loglevel','error','-y','-nostdin','-ss',str(start),'-i',str(source),'-t',str(end-start)]
    if caption.strip():
        card=make_title_card(caption.strip()[:150]);cmd+=['-loop','1','-i',str(card),'-filter_complex','[0:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=30[bg];[bg][1:v]overlay=shortest=1:format=auto,format=yuv420p[v]','-map','[v]']
    else:cmd+=['-vf','scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=30','-map','0:v:0']
    if not mute:cmd+=['-map','0:a:0?','-c:a','aac','-b:a','128k']
    else:cmd+=['-an']
    cmd+=['-c:v','libx264','-preset','veryfast','-crf','23','-pix_fmt','yuv420p','-movflags','+faststart',str(f)]
    run(cmd,timeout=200)
    return f

def motion_from_image(image,seconds=8):
    if not 2<=seconds<=30:raise ValueError('Choose between 2 and 30 seconds')
    result=MEDIA_ROOT/(uuid.uuid4().hex[:12]+'.mp4')
    # A simple cinematic pan-and-zoom, not AI video synthesis.
    frames=int(seconds*30)
    vf=(f'scale=900:1600:force_original_aspect_ratio=increase,crop=900:1600,'
       f'zoompan=z=\'min(zoom+0.0004,1.12)\':d={frames}:s=720x1280:fps=30,format=yuv420p')
    run(['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-y','-loop','1','-i',str(image),'-vf',vf,'-frames:v',str(frames),'-c:v','libx264','-preset','ultrafast','-movflags','+faststart',str(result)],180)
    return result
