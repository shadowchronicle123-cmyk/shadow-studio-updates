"""Exercise fake local ASR -> validated words -> actual offline captioned MP4.

No speech model or media is downloaded, and no upload occurs. The fake ASR
engine isolates orchestration; it does not validate real speech recognition.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile

from offline_transcript_adapter import transcribe_with_faster_whisper
from render_word_captions import render_local_clip

ffmpeg = shutil.which("ffmpeg")
ffprobe = shutil.which("ffprobe")
assert ffmpeg and ffprobe, "Offline FFmpeg tools required"


class FakeOfflineASR:
    def transcribe(self, source, *, word_timestamps, vad_filter):
        assert Path(source).is_file()
        assert word_timestamps and vad_filter
        return iter([
            {"words": [{"word": " hello", "start": 0.1, "end": 0.4},
                       {"word": " world", "start": 0.6, "end": 1.0}]}
        ]), {"language": "en"}


with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    source = root / "approved-clip.mp4"
    output = root / "captioned-clip.mp4"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                    "-f", "lavfi", "-i", "color=c=black:s=540x960:r=10:d=1.5",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=1.5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(source)],
                   check=True, timeout=60)
    words = transcribe_with_faster_whisper(source, FakeOfflineASR())
    assert [word["text"] for word in words] == ["hello", "world"]
    assert Path(render_local_clip(source, words, output)).is_file()
    probe = subprocess.run([ffprobe, "-v", "error", "-show_entries",
                            "stream=codec_type", "-of", "csv=p=0", str(output)],
                           check=True, capture_output=True, text=True, timeout=30)
    assert sorted(probe.stdout.split()) == ["audio", "video"], probe.stdout
    assert source.is_file() and output.stat().st_size > 0
print("PASS mocked local-ASR to timed captions to actual MP4 with audio")
