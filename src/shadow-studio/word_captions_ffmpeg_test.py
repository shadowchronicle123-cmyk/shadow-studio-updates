"""Offline FFmpeg/libass smoke test: real caption pixels differ from blank footage."""
from pathlib import Path
import shutil
import subprocess
import tempfile
from word_captions_ass import to_ass

ffmpeg = shutil.which("ffmpeg")
if not ffmpeg:
    raise RuntimeError("FFmpeg required for real subtitle-render smoke test")

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    (root / "captions.ass").write_text(
        to_ass([{"text": "HELLO", "start": 0.0, "end": 0.8}],
               facecam=(0.0, 0.0, 0.4, 0.3), width=540, height=960),
        encoding="utf-8",
    )
    def frame_hashes(overlay):
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
               "-f", "lavfi", "-i", "color=c=black:s=540x960:r=5:d=1",
               "-vf", "subtitles=captions.ass" if overlay else "null",
               "-frames:v", "4", "-f", "framemd5", "-"]
        proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=60)
        if proc.returncode:
            raise AssertionError("FFmpeg render failed: " + proc.stderr[-2000:])
        hashes = [line for line in proc.stdout.splitlines() if line and not line.startswith("#")]
        assert len(hashes) == 4, hashes
        return hashes
    baseline = frame_hashes(False)
    captioned = frame_hashes(True)
    assert baseline != captioned, "ASS captions produced no visible video changes"
print("PASS real FFmpeg/libass word captions alter rendered frames offline")
