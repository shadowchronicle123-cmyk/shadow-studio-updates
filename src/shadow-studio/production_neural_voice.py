"""Optional no-paid-key neural narration for the production worker.

Never silently fall back to eSpeak when neural synthesis fails.
"""
import os
from pathlib import Path
from voice_engine import create_voice

DEFAULT_VOICES = ("en-US-GuyNeural", "en-US-JennyNeural")


def neural_narration(text, path, voice, rate):
    """Adapter for production_worker.run_day's narrator signature.

    Existing eSpeak speaker slots map to two stable neural voices; do not rotate
    identities between daily videos. Requires internet and edge-tts.
    """
    primary = os.getenv("SHADOW_NEURAL_PRIMARY_VOICE", DEFAULT_VOICES[0])
    secondary = os.getenv("SHADOW_NEURAL_SECONDARY_VOICE", DEFAULT_VOICES[1])
    if primary == secondary:
        raise ValueError("Use two distinct neural narrator voices for dialogue")
    # Legacy speaker slots: en-us, en-gb, en-sc. Maintain deterministic mapping.
    selected = primary if voice == "en-us" else secondary
    if voice not in ("en-us", "en-gb", "en-sc"):
        raise ValueError("Unknown legacy speaker slot")
    create_voice(text, Path(path), selected, rate=max(-30, min(30, int(rate))))


def configured_narrator():
    engine = os.getenv("SHADOW_NARRATION_ENGINE", "espeak").strip().lower()
    if engine == "edge-neural":
        return neural_narration
    if engine == "espeak":
        from production_worker import local_narration
        return local_narration
    raise ValueError("Unsupported production narration engine")
