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
    # A 10fps fixture tests coarse boundaries; 100fps checks 10ms timing.\n    for frame in (0, 1, 2):
        assert digest(captioned[frame]) != digest(baseline[frame]), f"First word absent at frame {frame}"
    for frame in (7, 8, 9):
        assert digest(captioned[frame]) != digest(baseline[frame]), f"Second word absent at frame {frame}"
    assert digest(captioned[1]) != digest(captioned[8]), "Word transition did not change caption pixels"
    for frame in (3, 4, 5, 6, 10, 11, 12, 13, 14):
        assert digest(captioned[frame]) == digest(baseline[frame]), f"Caption visible outside speech at frame {frame}"
    # Probe fine-grained visibility at 100fps without trusting subtitle metadata.
    # Compare actual decoded frame pixels against identical uncaptioned footage.
    def fine_hashes(overlay):
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
               "-f", "lavfi", "-i", "color=c=black:s=540x960:r=100:d=1.1",
               "-vf", "subtitles=captions.ass" if overlay else "null",
               "-frames:v", "110", "-f", "framemd5", "-"]
        proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=90)
        if proc.returncode:
            raise AssertionError("100fps FFmpeg render failed: " + proc.stderr[-2000:])
        result = [digest(line) for line in proc.stdout.splitlines()
                  if line and not line.startswith("#")]
        assert len(result) == 110
        return result
    plain_fine = fine_hashes(False)
    subtitled_fine = fine_hashes(True)
    for frame in (5, 15, 25, 75, 85, 95):
        assert subtitled_fine[frame] != plain_fine[frame], (
            f"Word caption missing at {frame / 100:.2f}s")
    for frame in (35, 45, 55, 65, 105):
        assert subtitled_fine[frame] == plain_fine[frame], (
            f"Caption leaks into silence at {frame / 100:.2f}s")
print("PASS FFmpeg caption pixels at 10fps and 100fps with silent-gap clearance")
