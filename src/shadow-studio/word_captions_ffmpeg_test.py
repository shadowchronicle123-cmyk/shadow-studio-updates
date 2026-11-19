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
        to_ass([{"text": "HELLO", "start": 0.0, "end": 0.3},
                {"text": "WORLD", "start": 0.7, "end": 1.0}],
               facecam=(0.0, 0.0, 0.4, 0.3), width=540, height=960),
        encoding="utf-8",
    )
    def frame_hashes(overlay):
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
               "-f", "lavfi", "-i", "color=c=black:s=540x960:r=10:d=1.5",
               "-vf", "subtitles=captions.ass" if overlay else "null",
               "-frames:v", "15", "-f", "framemd5", "-"]
        proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=60)
        if proc.returncode:
            raise AssertionError("FFmpeg render failed: " + proc.stderr[-2000:])
        hashes = [line for line in proc.stdout.splitlines() if line and not line.startswith("#")]
        assert len(hashes) == 15, hashes
        return hashes
    baseline = frame_hashes(False)
    captioned = frame_hashes(True)
    def digest(line):
        return line.split(",")[-1].strip()
    assert digest(captioned[1]) != digest(baseline[1]), "First word not visible"
    assert digest(captioned[8]) != digest(baseline[8]), "Second word not visible"
    assert digest(captioned[1]) != digest(captioned[8]), "Word transition did not change caption pixels"
    for frame in (4, 5, 11, 14):
        assert digest(captioned[frame]) == digest(baseline[frame]), f"Caption visible outside speech at frame {frame}"
print("PASS FFmpeg renders distinct words and clears captions in silence and after speech")
