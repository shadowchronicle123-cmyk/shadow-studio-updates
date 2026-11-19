"""Render an approved local clip with existing word-level captions, entirely offline.

This helper does not download clips, transcribe speech, or publish anything.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from word_captions_ass import to_ass


def render_local_clip(source, words, output, facecam=None, ffmpeg="ffmpeg"):
    source = Path(source).resolve(strict=True)
    output = Path(output).resolve()
    if not source.is_file() or source.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm"}:
        raise ValueError("Input must be an existing local video")
    if output.suffix.lower() != ".mp4" or source == output or output.exists():
        raise ValueError("Output must be a new MP4 path distinct from input")
    if shutil.which(ffmpeg) is None:
        raise RuntimeError("FFmpeg is not installed")
    ass = to_ass(words, facecam)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Keep temporary ASS beside the output so FFmpeg can use a relative path on Windows.
    with tempfile.TemporaryDirectory(prefix="shadow-captions-") as temp:
        ass_path = Path(temp) / "captions.ass"
        ass_path.write_text(ass, encoding="utf-8")
        temp_output = Path(temp) / "rendered.mp4"
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                   "-n", "-i", str(source), "-vf", "subtitles=captions.ass",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-movflags", "+faststart", str(temp_output)]
        completed = subprocess.run(command, cwd=temp, capture_output=True, text=True, timeout=300)
        if completed.returncode or not temp_output.is_file() or temp_output.stat().st_size == 0:
            raise RuntimeError("Offline caption render failed: " + completed.stderr[-2000:])
        # Do not overwrite an existing output; copy with exclusive creation.
        with temp_output.open("rb") as incoming, output.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
    return str(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("words_json")
    parser.add_argument("output")
    parser.add_argument("--facecam", help="Normalized [left,top,right,bottom] JSON")
    args = parser.parse_args()
    words = json.loads(Path(args.words_json).read_text(encoding="utf-8"))
    print(render_local_clip(args.source, words, args.output,
                            json.loads(args.facecam) if args.facecam else None))
