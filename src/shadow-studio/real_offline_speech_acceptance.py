"""Opt-in REAL offline faster-whisper acceptance test.

Usage:
  SHADOW_ASR_MODEL_DIR=/existing/local/model \
  SHADOW_ASR_SPEECH_CLIP=/existing/rights-cleared-speech.mp4 \
  SHADOW_ASR_EXPECTED_WORD=hello \
  python real_offline_speech_acceptance.py

This script requires preinstalled faster-whisper and FFmpeg, downloads nothing,
and creates no YouTube upload. Do not run against unlicensed streamer footage.
The regular CI intentionally uses fake ASR; this opt-in check tests real speech.
"""
import os
from array import array
import math
import re
from sys import byteorder
from pathlib import Path
import shutil
import subprocess
import tempfile

from local_speech_caption_pipeline import transcribe_and_render, probe_clip_duration
from offline_transcript_adapter import transcribe_with_faster_whisper


def require_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Set {name} to run the real offline ASR acceptance test")
    return value


def probe_audio(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_type",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True, timeout=30)
    if "audio" not in result.stdout:
        raise AssertionError("Rendered MP4 has no audio stream")
    decoded = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
         "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", "16000",
         "-f", "s16le", "-"],
        check=True, capture_output=True, timeout=90).stdout
    samples = array("h")
    samples.frombytes(decoded)
    if byteorder != "little":
        samples.byteswap()
    if not samples:
        raise AssertionError("Decoded audio is empty")
    return len(samples) / 16000, sum(sample * sample for sample in samples) / len(samples)


def run():
    model_dir = Path(require_env("SHADOW_ASR_MODEL_DIR")).resolve(strict=True)
    speech_clip = Path(require_env("SHADOW_ASR_SPEECH_CLIP")).resolve(strict=True)
    expected = require_env("SHADOW_ASR_EXPECTED_WORD").casefold()
    if not model_dir.is_dir() or not speech_clip.is_file():
        raise ValueError("Model must be a local directory and speech clip a local file")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("Local FFmpeg and FFprobe required")
    from faster_whisper import WhisperModel
    model = WhisperModel(str(model_dir), device="cpu", compute_type="int8",
                         local_files_only=True)
    duration = probe_clip_duration(speech_clip)
    words = transcribe_with_faster_whisper(speech_clip, model, clip_duration=duration)
    recognized = " ".join(w["text"] for w in words).casefold()
    def tokens(value):
        return re.findall(r"\\w+", value.casefold(), flags=re.UNICODE)
    expected_tokens = tokens(expected)
    heard_tokens = tokens(recognized)
    if not expected_tokens:
        raise ValueError("Expected phrase must contain at least one word")
    found = any(heard_tokens[i:i + len(expected_tokens)] == expected_tokens
                for i in range(len(heard_tokens) - len(expected_tokens) + 1))
    if not found:
        raise AssertionError(f"Expected spoken phrase {expected!r} not recognized: {recognized!r}")
    with tempfile.TemporaryDirectory(prefix="shadow-real-asr-") as directory:
        output = Path(directory) / "captioned.mp4"
        # Test orchestration through the same real local model; no download.
        # Reuse the exact initialized offline model instead of loading weights twice.
        def existing_model_factory(path, **options):
            if Path(path) != model_dir or options != {
                    "device": "cpu", "compute_type": "int8",
                    "local_files_only": True}:
                raise AssertionError("Unexpected model path or nonlocal model options")
            return model
        transcribe_and_render(speech_clip, output, model_dir,
                              model_factory=existing_model_factory)
        if not output.is_file() or output.stat().st_size == 0:
            raise AssertionError("Captioned MP4 not created")
        source_duration, before = probe_audio(speech_clip)
        rendered_duration, after = probe_audio(output)
        if abs(source_duration - rendered_duration) > 0.10:
            raise AssertionError("Caption rendering truncated or extended audio")
        if not math.isfinite(after) or before <= 100 or not 0.5 < after / before < 1.5:
            raise AssertionError("Caption rendering muted or significantly changed audio")
    print("PASS real local speech recognition, timed captions, MP4 and audible preserved audio")


if __name__ == "__main__":
    run()
