"""Fail-closed unattended upload entrypoint with mandatory narration review.

Not scheduled or deployed. Existing private_upload.py CLI remains manual/legacy.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

from narration_policy import check_unattended_narration
from narration_quality import inspect_wav
from private_upload import upload_daily
from production_worker import ledger


def verified_narration_report(root, day, manifest):
    """Bind reviewed voice to a real local audio file, not a claimed pass flag."""
    if not isinstance(manifest, dict):
        raise ValueError("Narration review manifest must be an object")
    voice = manifest.get("voice_id")
    engine = manifest.get("engine")
    # Generated manifests are always unapproved; approval is worker-controlled.
    approved = os.getenv("SHADOW_NARRATION_REVIEW_APPROVED") == "1"
    # The render manifest is producer-controlled. Only the owner-controlled
    # worker allowlist may authorize a voice for unattended uploading.
    allowlist = {value.strip() for value in os.getenv("SHADOW_APPROVED_VOICE_IDS", "").split(",") if value.strip()}
    if not isinstance(voice, str) or voice not in allowlist:
        raise ValueError("Narrator is not on the owner-approved voice allowlist")
    relative = manifest.get("narration_wav")
    if not isinstance(relative, str) or not relative:
        raise ValueError("Missing narration WAV")
    expected = Path("renders") / day / "narration.wav"
    if Path(relative) != expected or ".." in Path(relative).parts:
        raise ValueError("Narration WAV must belong to this day's verified render")
    root = Path(root).resolve()
    source_path = root / expected
    path = source_path.resolve()
    if source_path.is_symlink() or not path.is_relative_to(root):
        raise ValueError("Invalid narration audio location")
    expected_digest = manifest.get("narration_sha256")
    if not isinstance(expected_digest, str) or len(expected_digest) != 64:
        raise ValueError("Missing narration SHA-256")
    with path.open("rb") as audio_file:
        actual_digest = hashlib.file_digest(audio_file, "sha256").hexdigest()
    if actual_digest != expected_digest:
        raise ValueError("Narration WAV differs from its verified export manifest")
    # The owner approves the exact audio bytes, not just a reusable voice ID.
    approved_digest = os.getenv("SHADOW_APPROVED_NARRATION_SHA256", "").lower()
    if len(approved_digest) != 64 or approved_digest != actual_digest:
        raise ValueError("This exact narration audio has not been approved by the owner")
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
        # Approval must bind the finished MP4 as well as its clean narration.
        with ledger(args.state) as db:
            row = db.execute("""SELECT v.sha256, j.artifact FROM verified_exports v
                                JOIN jobs j USING(day) WHERE v.day=? AND j.state='ready'""",
                             (day,)).fetchone()
        if row is None or row['artifact'] != f'renders/{day}/short.mp4':
            raise ValueError("No ready, verified MP4 export for this date")
        mp4_source = args.state / row['artifact']
        mp4 = mp4_source.resolve()
        if mp4_source.is_symlink() or not mp4.is_relative_to(args.state.resolve()):
            raise ValueError("Invalid verified MP4 location")
        with mp4.open("rb") as video_file:
            mp4_sha = hashlib.file_digest(video_file, "sha256").hexdigest()
        if mp4_sha != row['sha256']:
            raise ValueError("Finished MP4 does not match the verified export ledger")
        if os.getenv("SHADOW_APPROVED_VIDEO_SHA256", "").lower() != mp4_sha:
            raise ValueError("This exact finished MP4 has not been owner-approved")
        result = upload_daily(args.state, day, os.environ)
        print("Private upload result:", result)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print("Unattended private upload blocked:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
