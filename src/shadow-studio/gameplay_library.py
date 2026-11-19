"""Disk-backed gameplay collection; only user-provided videos, not scraped footage."""
from __future__ import annotations
import json, re, subprocess, threading
from pathlib import Path

def gameplay_duration(path):
    """Check for playable video frames rather than accepting any media duration."""
    try:
        result = subprocess.run(
            ['ffprobe','-v','error','-select_streams','V:0','-show_entries',
             'stream=width,height:format=duration','-of','json',str(path)],
            capture_output=True,text=True,timeout=30,check=True,
        )
        info = json.loads(result.stdout)
        video = info['streams'][0]
        duration = float(info['format']['duration'])
        if not video.get('width') or not video.get('height'):
            raise ValueError('missing dimensions')
    except FileNotFoundError as exc:
        raise ValueError('FFprobe is required to verify gameplay. Install FFmpeg and try again.') from exc
    except (ValueError, KeyError, IndexError, OSError, subprocess.SubprocessError) as exc:
        raise ValueError('Choose a playable video file with a video track.') from exc
    if not 1 <= duration <= 7200:
        raise ValueError('Gameplay must be 1 second to 2 hours.')
    return duration


def gameplay_window(path, start=None, end=None):
    """Validate an optional trim against the original clip without changing it."""
    if start is None and end is None:
        return None
    if path is None:
        raise ValueError('Choose gameplay before setting a trim.')
    duration = gameplay_duration(path)
    start = 0.0 if start is None else start
    end = duration if end is None else min(end, duration) if end <= duration + .025 else end
    if not 0 <= start < end <= duration or end - start < .5:
        raise ValueError(f'Choose at least 0.5 seconds within this {duration:.2f}-second gameplay clip.')
    return start, end


class GameplayLibrary:
    def __init__(self, root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.index=self.root/'library.json';self.lock=threading.RLock()
    def _read(self):
        try:
            data=json.loads(self.index.read_text(encoding='utf8'))
            return data if isinstance(data,list) else []
        except (OSError,ValueError):return []
    def _write(self, data):
        tmp=self.index.with_suffix('.tmp')
        tmp.write_text(json.dumps(data,indent=2),encoding='utf8');tmp.replace(self.index)
    def register(self, path, tag='general'):
        path=Path(path)
        m=re.fullmatch(r'gameplay_([0-9a-f]{12})\.(mp4|mov|mkv|webm)',path.name)
        if not m:raise ValueError('Invalid gameplay filename')
        duration=gameplay_duration(path)
        item={'id':m.group(1),'filename':path.name,'tag':re.sub(r'[^a-zA-Z0-9 _-]','',tag)[:45] or 'general','duration':round(duration,2),'uses':0,'cursor':0.0}
        with self.lock:
            d=self._read();d.append(item);self._write(d)
        return dict(item)
    def list_clips(self):
        with self.lock:return [dict(x) for x in self._read() if (self.root/x['filename']).is_file()]
    def pick(self, id):
        with self.lock:
            d=self.list_clips()
            if not d:raise ValueError('No gameplay uploaded yet')
            if id=='auto':
                d.sort(key=lambda x:(x['uses'],x['id']))
                item=d[0]
            else:
                match=[x for x in d if x['id']==id]
                if not match:raise ValueError('Gameplay not found')
                item=match[0]
            return {**item,'path':self.root/item['filename']}
    def mark_used(self,id,seconds):
        with self.lock:
            d=self._read()
            for x in d:
                if x['id']==id:
                    x['uses']+=1
                    duration = max(x['duration'], 1)
                    x['cursor'] = round((x['cursor'] + seconds) % duration, 2) % duration
            self._write(d)
