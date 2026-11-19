"""Offline local transcription and captioned MP4 pipeline.

Requires a preinstalled faster-whisper package and a locally available model.
No model downloads are initiated: local_files_only is enforced.
"""
import argparse
import math
import shutil
import subprocess
from pathlib import Path

from offline_transcript_adapter import transcribe_with_faster_whisper
from render_word_captions import render_local_clip


def probe_clip_duration(source):
    """Read actual media duration, refusing invalid or unprobeable input."""
    if shutil.which("ffprobe") is None:
        raise RuntimeError("FFprobe is required for transcript timing validation")
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(source)],
        capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError("Cannot probe input media duration")
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise ValueError("Invalid input media duration") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid input media duration")
    return duration


def transcribe_and_render(source, output, model_path, facecam=None, model_factory=None):
    # Reject invalid paths before loading a potentially expensive local model.
    source_path = Path(source).resolve(strict=True)
    target_path = Path(output).resolve()
    if (not source_path.is_file() or source_path.suffix.lower() not in
            {".mp4", ".mov", ".mkv", ".webm"}):
        raise ValueError("Input must be an existing local video")
    if (target_path.suffix.lower() != ".mp4" or source_path == target_path
            or target_path.exists()):
        raise ValueError("Output must be a new MP4 distinct from input")
    duration = probe_clip_duration(source_path)
    model_dir = Path(model_path).resolve(strict=True)
    if not model_dir.is_dir():
        raise ValueError("Model path must be an existing local directory")
    if model_factory is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("Install faster-whisper separately before using local transcription") from exc
        model_factory = WhisperModel
    model = model_factory(str(model_dir), device="cpu", compute_type="int8",
                          local_files_only=True)
    words = transcribe_with_faster_whisper(source_path, model)
    if words[-1]["end"] > duration + 0.05:
        raise ValueError("Transcribed words extend beyond source video duration")
    return render_local_clip(source_path, words, target_path, facecam=facecam)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Approved local streamer clip")
    parser.add_argument("output", help="New MP4 path")
    parser.add_argument("--model-dir", required=True, help="Existing local faster-whisper model directory")
    parser.add_argument("--facecam", help="Normalized JSON rectangle")
    args = parser.parse_args()
    import json
    facecam = json.loads(args.facecam) if args.facecam else None
    print(transcribe_and_render(args.source, args.output, args.model_dir, facecam))
