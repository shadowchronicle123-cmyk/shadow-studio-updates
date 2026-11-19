"""Offline local transcription and captioned MP4 pipeline.

Requires a preinstalled faster-whisper package and a locally available model.
No model downloads are initiated: local_files_only is enforced.
"""
import argparse
from pathlib import Path

from offline_transcript_adapter import transcribe_with_faster_whisper
from render_word_captions import render_local_clip


def transcribe_and_render(source, output, model_path, facecam=None, model_factory=None):
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
    words = transcribe_with_faster_whisper(source, model)
    return render_local_clip(source, words, output, facecam=facecam)


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
