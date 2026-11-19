"""Verify local-only speech model options and end-to-end rendering with a fake model."""
from pathlib import Path
import shutil
import subprocess
import tempfile

from local_speech_caption_pipeline import transcribe_and_render

ffmpeg = shutil.which("ffmpeg")
ffprobe = shutil.which("ffprobe")
assert ffmpeg and ffprobe

class LocalOnlyModel:
    def __init__(self, path, **kwargs):
        assert Path(path).is_dir()
        assert kwargs == {"device": "cpu", "compute_type": "int8",
                          "local_files_only": True}
    def transcribe(self, source, **kwargs):
        assert Path(source).is_file()
        assert kwargs == {"word_timestamps": True, "vad_filter": True}
        return iter([{"words": [{"word": " Test", "start": 0.1, "end": 0.6}]}]), None

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    model_dir = root / "existing-model"
    model_dir.mkdir()
    source = root / "clip.mp4"
    target = root / "captioned.mp4"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                    "-f", "lavfi", "-i", "color=c=black:s=540x960:r=10:d=1.2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1.2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(source)], check=True, timeout=60)
    assert Path(transcribe_and_render(source, target, model_dir,
                                     model_factory=LocalOnlyModel)).is_file()
    streams = subprocess.run([ffprobe, "-v", "error", "-show_entries",
                              "stream=codec_type", "-of", "csv=p=0", str(target)],
                             capture_output=True, text=True, check=True, timeout=30)
    assert sorted(streams.stdout.split()) == ["audio", "video"]
    try:
        transcribe_and_render(source, root / "should-not-exist.mp4",
                              root / "missing-model", model_factory=LocalOnlyModel)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("Missing model directory accepted")
    assert not (root / "should-not-exist.mp4").exists()
print("PASS local-only model initialization and speech-to-captioned-MP4 pipeline")
