"""Local split-screen video composer. Only processes uploaded files."""
import subprocess, uuid
from pathlib import Path


def split_screen(top: Path, bottom: Path, output_dir: Path, seconds: int = 15, layout: str = "stacked") -> Path:
    if layout not in ("stacked", "side_by_side"):
        raise ValueError("Choose stacked or side_by_side")
    if not (3 <= seconds <= 120):
        raise ValueError("Duration must be 3 to 120 seconds")
    if not top.is_file() or not bottom.is_file():
        raise ValueError("One of the uploaded videos was not found")
    output_dir.mkdir(exist_ok=True,parents=True)
    out=output_dir/(uuid.uuid4().hex[:12]+'.mp4')
    # The split-screen export intentionally mutes the source videos. Add narration
    # separately in the story workflow rather than accidentally reusing music.
    if layout == 'stacked':
        filtergraph=("[0:v]fps=30,scale=720:640:force_original_aspect_ratio=increase,crop=720:640,setsar=1[top];"
                     "[1:v]fps=30,scale=720:640:force_original_aspect_ratio=increase,crop=720:640,setsar=1[bottom];"
                     "[top][bottom]vstack=inputs=2,format=yuv420p[v]")
    else:
        filtergraph=("[0:v]fps=30,scale=360:1280:force_original_aspect_ratio=increase,crop=360:1280,setsar=1[left];"
                     "[1:v]fps=30,scale=360:1280:force_original_aspect_ratio=increase,crop=360:1280,setsar=1[right];"
                     "[left][right]hstack=inputs=2,format=yuv420p[v]")
    cmd=['ffmpeg','-nostdin','-y','-hide_banner','-loglevel','error',
         '-stream_loop','-1','-i',str(top),'-stream_loop','-1','-i',str(bottom),
         '-filter_complex',filtergraph,'-map','[v]','-t',str(seconds),
         '-an','-c:v','libx264','-preset','veryfast','-crf','23','-pix_fmt','yuv420p',
         '-movflags','+faststart',str(out)]
    try:
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=max(90,seconds*8))
        if p.returncode:
            raise RuntimeError((p.stderr or 'FFmpeg failed')[-650:])
        if not out.is_file() or out.stat().st_size<500:
            raise RuntimeError('No video was produced')
        return out
    except Exception:
        out.unlink(missing_ok=True)
        raise
