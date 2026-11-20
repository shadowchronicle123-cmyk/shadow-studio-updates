"""Produce one isolated offline sample and a readable acceptance report."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys


def run(root):
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11 or newer is required.")
    for executable in ("ffmpeg", "ffprobe"):
        if not shutil.which(executable):
            raise RuntimeError(f"{executable} is missing from PATH. Open a new terminal after installing FFmpeg.")
        subprocess.run([executable, "-version"], stdout=subprocess.DEVNULL, check=True, timeout=15)

    from captions import read_captions
    from production_worker import _check_export, local_narration
    from renderer import generate
    from story_engine import make_story

    story = make_story("A wedding planner finds an extra place card with her own childhood nickname.", target=45)
    background = root / "test-background.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "testsrc2=size=320x180:rate=24:duration=2", "-c:v", "libx264",
                    str(background)], check=True, timeout=30)
    result = generate(story, root, background=background, resolution="1080p", target_seconds=45,
                      voice="windows-default" if os.name == "nt" else "en-us",
                      second_voice="windows-default" if os.name == "nt" else "en-gb",
                      voice_factory=None if os.name == "nt" else local_narration)
    quality = _check_export(result)
    video = Path(result["path"])
    with video.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return [
        "PASS Python and render dependencies, FFmpeg and FFprobe",
        f"PASS Local narration ({'Windows SAPI' if os.name == 'nt' else 'eSpeak'})",
        f"PASS {quality['width']}x{quality['height']} at {quality['fps']} FPS, "
        f"{quality['duration_seconds']} seconds, non-silent audio",
        f"PASS {len(read_captions(result['captions_path']))} timed cues and media integrity",
        f"MP4: {video.name}",
        f"SHA-256: {digest}",
        *(f"Review warning: {warning}" for warning in quality["warnings"]),
        "Manual review: play the MP4 and check caption readability, speech timing and pacing.",
    ]


def main():
    try:
        root = Path(tempfile.mkdtemp(prefix="ShadowStudio-acceptance-"))
    except OSError:
        print("FAIL: could not create a writable temporary acceptance folder.")
        return 1
    print(f"Acceptance output: {root}", flush=True)
    try:
        lines = run(root)
        code = 0
    except ModuleNotFoundError as exc:
        lines = [f"FAIL Missing Python dependency: {exc.name}. Use the separately prepared test install."]
        code = 1
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        lines = [f"FAIL {exc}"]
        code = 1
    lines.append("Separate checks: application startup, update rollback, and Windows Task Scheduler.")
    report = "\n".join(lines) + "\n"
    try:
        (root / "acceptance-report.txt").write_text(report, encoding="utf-8")
    except OSError:
        print("FAIL: could not write acceptance-report.txt.")
        code = 1
    print(report)
    return code


if __name__ == "__main__":
    sys.exit(main())
