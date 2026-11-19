"""Offline tests for narration quality metrics; no external audio or voice cloning."""
from array import array
import math
import tempfile
import wave
from pathlib import Path
from narration_quality import inspect_wav, reference_summary


def write(path, samples, rate=16000):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(array("h", samples).tobytes())


def main():
    with tempfile.TemporaryDirectory() as folder:
        p = Path(folder) / "voice.wav"
        write(p, (int(11000 * math.sin(2 * math.pi * 200 * i / 16000)) for i in range(16000)))
        result = inspect_wav(p)
        assert result["passed"] and result["human_likeness_verified"] is False, result
        write(p, (0 for _ in range(16000)))
        assert "too_quiet" in inspect_wav(p)["issues"]
        assert "excessive_silence" in inspect_wav(p)["issues"]
        assert reference_summary([{"creator": "A", "expressive": True}])["positive_observations"]["expressive"] == 1
    print("PASS narration audio checks and non-cloning creator voice observations")


if __name__ == "__main__":
    main()
