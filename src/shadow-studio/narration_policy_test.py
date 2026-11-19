"""Regression tests: unattended narration cannot silently use robotic voices."""
from narration_policy import check_unattended_narration


def main():
    audio = {"passed": True}
    assert "robotic_or_test_engine_blocked" in check_unattended_narration(
        engine="espeak-ng", voice_id="en-us", review_approved=True, audio_report=audio)["issues"]
    assert "voice_not_listening_approved" in check_unattended_narration(
        engine="edge-neural", voice_id="en-US-AriaNeural", audio_report=audio)["issues"]
    assert "audio_integrity_not_verified" in check_unattended_narration(
        engine="edge-neural", voice_id="en-US-AriaNeural", review_approved=True)["issues"]
    assert not check_unattended_narration(
        engine="unknown", voice_id="voice", review_approved=True, audio_report=audio)["passed"]
    assert check_unattended_narration(
        engine="edge-neural", voice_id="en-US-AriaNeural",
        review_approved=True, audio_report=audio)["passed"]
    print("PASS unattended narration blocks eSpeak, unknown voices, unapproved voices and failed audio")


if __name__ == "__main__":
    main()
