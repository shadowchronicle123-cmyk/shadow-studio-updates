"""Local project, metadata, and clipping helpers for Shadow Studio."""
import json, re, subprocess, uuid
from pathlib import Path
from renderer import line_pause
from audio_mix import line_sound

BASE=Path(__file__).resolve().parent
PROJECTS=BASE/'projects'; PROJECTS.mkdir(exist_ok=True)
CLIPS=BASE/'clips'; CLIPS.mkdir(exist_ok=True)

def save_project(data):
    if not isinstance(data,dict): raise ValueError('Project must be an object')
    title=str(data.get('title','Untitled'))[:95]
    lines=data.get('lines',[])
    if not isinstance(lines,list) or not (2<=len(lines)<=40): raise ValueError('Project needs 2–40 lines')
    cleaned=[]
    for l in lines:
        if not isinstance(l,dict):raise ValueError('Invalid line')
        s=str(l.get('speaker','Narrator')).strip()[:24]
        t=str(l.get('text','')).strip()[:240]
        if not t:raise ValueError('Empty line')
        line = {'speaker':s or 'Narrator','text':t,'pause_before':line_pause(l)}
        if isinstance(l.get('alternate'), str) and l['alternate'].strip():
            line['alternate'] = l['alternate'].strip()[:240]
        effect, offset = line_sound(l)
        line.update(sound_effect=effect, sound_offset=offset)
        cleaned.append(line)
    id=uuid.uuid4().hex[:12]
    payload={'id':id,'title':title,'lines':cleaned,'style':data.get('style','messages'),'voice':data.get('voice','en-US-AriaNeural'),'voice_rate':int(data.get('voice_rate',0)),'resolution':data.get('resolution','720p')}
    for field in ('second_voice', 'gameplay_id', 'pacing', 'captions', 'target', 'fit_duration', 'genre', 'ending', 'music_name', 'voice_volume', 'music_volume'):
        if field in data:
            payload[field] = data[field]
    (PROJECTS/f'{id}.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    return payload

def list_projects():
    result=[]
    for f in sorted(PROJECTS.glob('*.json'),key=lambda x:x.stat().st_mtime,reverse=True)[:100]:
        try:
            d=json.loads(f.read_text(encoding='utf-8'))
            result.append({'id':d['id'],'title':d['title'],'style':d.get('style','messages')})
        except (ValueError,KeyError):pass
    return result

def get_project(id):
    if not re.fullmatch('[a-f0-9]{12}',id):raise ValueError('Invalid project ID')
    p=PROJECTS/f'{id}.json'
    if not p.is_file():raise FileNotFoundError(id)
    return json.loads(p.read_text(encoding='utf-8'))

def clip_media(source:Path,start:float,end:float,orientation:str='vertical'):
    if not (0<=start<end<=36000) or end-start>180:raise ValueError('Clips must be 0–180 seconds long')
    if orientation not in ('vertical','source'):raise ValueError('Invalid orientation')
    dest=CLIPS/(uuid.uuid4().hex[:12]+'.mp4')
    cmd=['ffmpeg','-y','-hide_banner','-loglevel','error','-ss',str(start),'-i',str(source),'-t',str(end-start)]
    if orientation=='vertical':cmd+=['-vf','scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1,fps=30']
    cmd+=['-c:v','libx264','-preset','veryfast','-crf','23','-c:a','aac','-b:a','128k','-movflags','+faststart',str(dest)]
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=600)
    if p.returncode:raise RuntimeError(p.stderr[-700:])
    return dest
