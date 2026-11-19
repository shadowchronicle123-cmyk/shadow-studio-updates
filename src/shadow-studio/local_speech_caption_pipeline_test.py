"""Verify local-only speech model options and end-to-end rendering with a fake model."""
from pathlib import Path
import shutil
import subprocess
import tempfile

from local_speech_caption_pipeline import transcribe_and_render, probe_clip_duration

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
    assert 1.0 < probe_clip_duration(source) < 1.5
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
    class NoSpeechModel(LocalOnlyModel):
        def transcribe(self, source, **kwargs):
            return iter([{"words": []}]), None
    silent_output = root / "silent-should-not-render.mp4"
    try:
        transcribe_and_render(source, silent_output, model_dir,
                              model_factory=NoSpeechModel)
    except ValueError:
        pass
    else:
        raise AssertionError("Empty ASR transcript rendered a video")
    assert not silent_output.exists(), "No-speech input left a rendered artifact"
    class OverlappingSpeechModel(LocalOnlyModel):
        def transcribe(self, source, **kwargs):
            return iter([{"words": [
                {"word": "first", "start": 0.1, "end": 0.7},
                {"word": "second", "start": 0.5, "end": 0.9}]}]), None
    overlap_output = root / "overlap-should-not-render.mp4"
    try:
        transcribe_and_render(source, overlap_output, model_dir,
                              model_factory=OverlappingSpeechModel)
    except ValueError:
        pass
    else:
        raise AssertionError("Overlapping ASR timestamps rendered a video")
    assert not overlap_output.exists(), "Bad ASR timestamps left a rendered artifact"
    class BeyondDurationModel(LocalOnlyModel):
        def transcribe(self, source, **kwargs):
            return iter([{"words": [{"word": "late", "start": 1.0,
                                      "end": 4.0}]}]), None
    late_output = root / "late-should-not-render.mp4"
    try:
        transcribe_and_render(source, late_output, model_dir,
                              model_factory=BeyondDurationModel)
    except ValueError:
        pass
    else:
        raise AssertionError("Caption after clip duration was accepted")
    assert not late_output.exists()
    try:
        probe_clip_duration(root / "missing.mp4")
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid media duration was accepted")
    class MustNotInitialize:
        def __init__(self, *args, **kwargs):
            raise AssertionError("Expensive ASR initialized before input preflight")
    for invalid_target in (source, target, root / "invalid.mov"):
        try:
            transcribe_and_render(source, invalid_target, model_dir,
                                  model_factory=MustNotInitialize)
        except ValueError:
            pass
        else:
            raise AssertionError("Unsafe output accepted before model loading")
    try:
        transcribe_and_render(root / "missing.mp4", root / "unused.mp4",
                              model_dir, model_factory=MustNotInitialize)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("Missing input accepted before model loading")
print("PASS local speech-to-MP4, no-speech, overlap and path safety")
