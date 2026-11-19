"""Real end-to-end local video caption render and no-overwrite QA."""
from pathlib import Path
import shutil
import subprocess
import tempfile

from render_word_captions import render_local_clip

ffmpeg = shutil.which("ffmpeg")
ffprobe = shutil.which("ffprobe")
assert ffmpeg and ffprobe, "FFmpeg and FFprobe required"
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    source = root / "source.mp4"
    output = root / "captioned.mp4"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                    "-f", "lavfi", "-i", "color=c=black:s=540x960:r=10:d=1.5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
                   check=True, timeout=60)
    words = [{"text": "HELLO", "start": 0.0, "end": 0.4},
             {"text": "WORLD", "start": 0.7, "end": 1.0}]
    assert Path(render_local_clip(source, words, output)).is_file()
    probe = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", str(output)],
                           capture_output=True, text=True, check=True, timeout=30)
    assert probe.stdout.strip() == "540,960", probe.stdout
    assert output.stat().st_size > 0
    original = output.read_bytes()
    try:
        render_local_clip(source, words, output)
    except ValueError:
        pass
    else:
        raise AssertionError("Existing video overwritten")
    assert output.read_bytes() == original
    assert source.is_file()
print("PASS offline local MP4 caption render, FFprobe validation and overwrite protection")
