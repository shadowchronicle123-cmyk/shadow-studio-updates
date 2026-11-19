"""Offline tests for unattended upload narration gate; no YouTube calls."""
from array import array
import math
import hashlib
import os
from unittest.mock import patch
from pathlib import Path
import tempfile
import wave
from unattended_private_upload import verified_narration_report


def main():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        day = "2026-11-19"
        folder = root / "renders" / day
        folder.mkdir(parents=True)
        wav_path = folder / "narration.wav"
        with wave.open(str(wav_path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(array("h", (int(12000 * math.sin(2 * math.pi * 220 * i / 16000))
                                          for i in range(16000))).tobytes())
        manifest = {"engine": "edge-neural", "voice_id": "en-US-AriaNeural",
                    "listening_review_approved": True,
                    "narration_wav": f"renders/{day}/narration.wav",
                    "narration_sha256": hashlib.sha256(wav_path.read_bytes()).hexdigest()}
        with patch.dict(os.environ, {"SHADOW_APPROVED_VOICE_IDS": "en-US-AriaNeural", "SHADOW_NARRATION_REVIEW_APPROVED": "1"}):
            assert verified_narration_report(root, day, manifest)["passed"]
        with patch.dict(os.environ, {"SHADOW_APPROVED_VOICE_IDS": "", "SHADOW_NARRATION_REVIEW_APPROVED": "1"}):
            try:
                verified_narration_report(root, day, manifest)
            except ValueError:
                pass
            else:
                raise AssertionError("Unapproved voice bypassed owner allowlist")
        for key, value in (("engine", "espeak-ng"), ("listening_review_approved", False),
                           ("narration_wav", "../narration.wav"),
                           ("narration_sha256", "0" * 64)):
            bad = dict(manifest, **{key: value})
            try:
                with patch.dict(os.environ, {"SHADOW_APPROVED_VOICE_IDS": "en-US-AriaNeural"}):
                    verified_narration_report(root, day, bad)
            except (ValueError, OSError):
                pass
            else:
                raise AssertionError(f"Unsafe narration was accepted: {key}")
        with patch.dict(os.environ, {"SHADOW_APPROVED_VOICE_IDS": "en-US-AriaNeural",
                                     "SHADOW_NARRATION_REVIEW_APPROVED": "0"}):
            try:
                verified_narration_report(root, day, manifest)
            except ValueError:
                pass
            else:
                raise AssertionError("Unapproved narration bypassed worker review")
    print("PASS unattended private-upload entrypoint rejects robotic and unreviewed narration")


if __name__ == "__main__":
    main()
