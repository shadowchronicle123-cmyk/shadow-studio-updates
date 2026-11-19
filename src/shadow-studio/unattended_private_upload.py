"""Fail-closed unattended upload entrypoint with mandatory narration review.

Not scheduled or deployed. Existing private_upload.py CLI remains manual/legacy.
"""
import argparse
import json
import os
from pathlib import Path

from narration_policy import check_unattended_narration
from narration_quality import inspect_wav
from private_upload import upload_daily


def verified_narration_report(root, day, manifest):
    """Bind reviewed voice to a real local audio file, not a claimed pass flag."""
    if not isinstance(manifest, dict):
        raise ValueError("Narration review manifest must be an object")
    voice = manifest.get("voice_id")
    engine = manifest.get("engine")
    approved = manifest.get("listening_review_approved") is True
    relative = manifest.get("narration_wav")
    if not isinstance(relative, str) or not relative:
        raise ValueError("Missing narration WAV")
    expected = Path("renders") / day / "narration.wav"
    if Path(relative) != expected or ".." in Path(relative).parts:
        raise ValueError("Narration WAV must belong to this day's verified render")
    root = Path(root).resolve()
    path = (root / expected).resolve()
    if not path.is_relative_to(root) or path.is_symlink():
        raise ValueError("Invalid narration audio location")
    report = inspect_wav(path)
    decision = check_unattended_narration(
        engine=engine, voice_id=voice, review_approved=approved, audio_report=report)
    if not decision["passed"]:
        raise ValueError("Unattended upload blocked by narration review: " + ", ".join(decision["issues"]))
    return decision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--day", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        from datetime import date
        day = date.fromisoformat(args.day).isoformat()
        expected = (args.state / "renders" / day / "narration-review.json").resolve()
        if args.manifest.resolve() != expected or args.manifest.is_symlink():
            raise ValueError("Review manifest must belong to the requested day")
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        verified_narration_report(args.state, day, manifest)
        result = upload_daily(args.state, day, os.environ)
        print("Private upload result:", result)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print("Unattended private upload blocked:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
