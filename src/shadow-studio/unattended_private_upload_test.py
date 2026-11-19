"""Offline tests for unattended upload narration gate; no YouTube calls."""
from array import array
import math
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
                    "narration_wav": f"renders/{day}/narration.wav"}
        assert verified_narration_report(root, day, manifest)["passed"]
        for key, value in (("engine", "espeak-ng"), ("listening_review_approved", False),
                           ("narration_wav", "../narration.wav")):
            bad = dict(manifest, **{key: value})
            try:
                verified_narration_report(root, day, bad)
            except (ValueError, OSError):
                pass
            else:
                raise AssertionError(f"Unsafe narration was accepted: {key}")
    print("PASS unattended private-upload entrypoint rejects robotic and unreviewed narration")


if __name__ == "__main__":
    main()
