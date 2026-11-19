"""Local, read-only export preflight. No network or uploads."""
import json
import math
import re
import subprocess
from pathlib import Path


def _run(args, timeout=25):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError('FFmpeg could not inspect this video')
    return result


def analyze(path: Path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size < 1024:
        raise ValueError('Video file is missing or empty')
    info = json.loads(_run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(path)]).stdout)
    videos = [s for s in info.get('streams',[]) if s.get('codec_type')=='video']
    audios = [s for s in info.get('streams',[]) if s.get('codec_type')=='audio']
    warnings=[]
    blockers=[]
    if not videos:
        blockers.append('MP4 contains no video track')
        width=height=0; fps=0
    else:
        video=videos[0]
        width=int(video.get('width') or 0);height=int(video.get('height') or 0)
        rate=video.get('avg_frame_rate') or '0/1'
        try:
            a,b=rate.split('/');fps=round(float(a)/float(b),2)
        except (ValueError,ZeroDivisionError):fps=0
        if width==0 or height==0:blockers.append('Could not read video dimensions')
        elif width>=height: warnings.append('Video is not vertical 9:16')
        elif abs(width/height-9/16)>0.025:warnings.append('Aspect ratio is not exactly 9:16')
        if fps<24:warnings.append('Low frame rate: target at least 24 FPS')
        if width<720 or height<1280:warnings.append('Resolution below 720×1280')
    if not audios:
        blockers.append('MP4 contains no narration/audio track')
    elif videos:
        try:
            delta = abs(float(videos[0]['duration']) - float(audios[0]['duration']))
        except (KeyError, TypeError, ValueError):
            delta = 0
        if delta > 1:
            blockers.append('Audio and picture end more than a second apart')
        elif delta > .35:
            warnings.append('Audio and picture end at different times. Watch the ending before approval.')
    # Metadata alone is insufficient: corrupted video frames may have valid
    # streams, duration and audio. Decode the full video stream without writing
    # another file; -xerror makes corruption fail instead of being concealed.
    if videos:
        try:
            decoded = subprocess.run(
                ['ffmpeg', '-hide_banner', '-nostats', '-v', 'error', '-xerror',
                 '-i', str(path), '-map', '0:v:0', '-an', '-f', 'null', '-'],
                capture_output=True, text=True, timeout=120, check=False)
            if decoded.returncode:
                blockers.append('Video contains damaged or undecodable frames')
        except (subprocess.TimeoutExpired, OSError):
            blockers.append('Video frames could not be fully decoded within the safety limit')
    duration=float(info.get('format',{}).get('duration') or 0)
    if duration<3:blockers.append('Video is shorter than three seconds')
    elif duration>180:warnings.append('Video exceeds three minutes; check Shorts requirements')
    size_mb=round(path.stat().st_size/1024**2,2)
    if size_mb>250:warnings.append('Large file: may upload slowly on mobile')
    max_volume=None;mean_volume=None
    overall_max_volume=None
    if audios:
        # Sample the beginning for speed. These are signals, not a guarantee of good narration.
        sample=min(duration,12)
        try:
            proc=subprocess.run(['ffmpeg','-hide_banner','-nostats','-i',str(path),'-t',str(sample),'-vn','-af','volumedetect','-f','null','-'],capture_output=True,text=True,timeout=28)
            m=re.search(r'max_volume:\s*(-?[\d.]+) dB',proc.stderr)
            n=re.search(r'mean_volume:\s*(-?[\d.]+) dB',proc.stderr)
            if m:max_volume=float(m.group(1))
            if n:mean_volume=float(n.group(1))
            if max_volume is not None and max_volume < -42: warnings.append('Audio is very quiet or nearly silent in the opening')
            if max_volume is not None and max_volume > -0.1: warnings.append('Audio approaches clipping; listen for distortion')
        except (subprocess.TimeoutExpired, OSError):warnings.append('Opening audio loudness could not be measured')
        # Inspect the whole audio stream. An encoded AAC track can exist while
        # containing nothing but silence; that must not pass publishing QA.
        try:
            whole=subprocess.run(['ffmpeg','-hide_banner','-nostats','-i',str(path),
                                  '-vn','-af','volumedetect','-f','null','-'],
                                 capture_output=True,text=True,timeout=50)
            found=re.search(r'max_volume:\s*(-?inf|-?[\d.]+) dB',whole.stderr,re.I)
            if whole.returncode != 0 or found is None:
                blockers.append('Full narration audio could not be verified')
            else:
                overall_max_volume = float(found.group(1).lower())
                if overall_max_volume < -55:
                    blockers.append('Entire audio track is silent or effectively silent')
                if not math.isfinite(overall_max_volume):
                    overall_max_volume = None
        except (subprocess.TimeoutExpired,OSError):
            blockers.append('Full narration audio could not be verified')
    return {'passed':not blockers,'status':'blocked' if blockers else 'review_required' if warnings else 'technical_pass',
            'duration_seconds':round(duration,2),'width':width,'height':height,'fps':fps,'file_size_mb':size_mb,
            'audio_present':bool(audios),'max_volume_db':max_volume,'mean_volume_db':mean_volume, 'overall_max_volume_db':overall_max_volume,
            'blockers':blockers,'warnings':warnings,
            'human_checks':['Listen for natural narration and correct timing','Watch captions and gameplay through to the ending','Confirm rights to gameplay, voices, and music','Approve title and description before publishing']}
