"""Fail-closed policy for unattended narration. Technical audio checks do not prove naturalness."""
import os

ROBOTIC_ENGINES = frozenset({"espeak", "espeak-ng", "sapi", "windows-sapi", "synthetic-test"})
REVIEWED_ENGINES = frozenset({"edge-neural", "elevenlabs", "azure-neural", "google-neural"})


def check_unattended_narration(*, engine, voice_id, review_approved=False, audio_report=None):
    """Require an explicitly reviewed voice for automated uploads.

    This is a manual approval gate, not an algorithmic human-likeness detector.
    """
    issues = []
    engine = (engine or "").strip().lower()
    voice_id = (voice_id or "").strip()
    if engine in ROBOTIC_ENGINES:
        issues.append("robotic_or_test_engine_blocked")
    elif engine not in REVIEWED_ENGINES:
        issues.append("unrecognized_voice_engine")
    if not voice_id:
        issues.append("missing_voice_identity")
    if review_approved is not True:
        issues.append("voice_not_listening_approved")
    if not isinstance(audio_report, dict) or audio_report.get("passed") is not True:
        issues.append("audio_integrity_not_verified")
    return {"passed": not issues, "issues": issues, "engine": engine,
            "voice_id": voice_id, "review_approved": review_approved is True}


def from_environment(audio_report):
    """A process may enable this gate, but cannot silently bypass it."""
    return check_unattended_narration(
        engine=os.getenv("SHADOW_NARRATION_ENGINE"),
        voice_id=os.getenv("SHADOW_NARRATION_VOICE_ID"),
        review_approved=os.getenv("SHADOW_NARRATION_REVIEW_APPROVED") == "1",
        audio_report=audio_report,
    )
