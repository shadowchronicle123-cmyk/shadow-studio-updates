"""Offline regression tests for optional production neural narrator selection."""
import os
from pathlib import Path
from unittest.mock import patch
from production_neural_voice import configured_narrator, neural_narration


def main():
    calls = []
    def fake_create(text, path, voice, rate):
        calls.append((text, Path(path).name, voice, rate))
    with patch("production_neural_voice.create_voice", side_effect=fake_create):
        with patch.dict(os.environ, {"SHADOW_NEURAL_PRIMARY_VOICE": "en-US-GuyNeural",
                                     "SHADOW_NEURAL_SECONDARY_VOICE": "en-US-JennyNeural"}):
            neural_narration("First line", Path("one.wav"), "en-us", 3)
            neural_narration("Second line", Path("two.wav"), "en-gb", -4)
            neural_narration("Third line", Path("three.wav"), "en-sc", 0)
        assert [x[2] for x in calls] == [
            "en-US-GuyNeural", "en-US-JennyNeural", "en-US-JennyNeural"]
        with patch.dict(os.environ, {"SHADOW_NEURAL_PRIMARY_VOICE": "en-US-GuyNeural",
                                     "SHADOW_NEURAL_SECONDARY_VOICE": "en-US-GuyNeural"}):
            try:
                neural_narration("No", Path("bad.wav"), "en-us", 0)
            except ValueError:
                pass
            else:
                raise AssertionError("Identical voices were accepted")
    with patch.dict(os.environ, {"SHADOW_NARRATION_ENGINE": "edge-neural"}):
        assert configured_narrator() is neural_narration
    with patch.dict(os.environ, {"SHADOW_NARRATION_ENGINE": "unrecognized"}):
        try:
            configured_narrator()
        except ValueError:
            pass
        else:
            raise AssertionError("Unsupported engine accepted")
    print("PASS production neural narrator mapping, stable voices, and fail-closed engine selection")


if __name__ == "__main__":
    main()
