"""Conservative narration checks; acoustic measurements are not proof of human-like speech.

Competitor reference measurements are supplied with permission, not scraped or cloned.
"""
import math
import wave
from pathlib import Path


def inspect_wav(path):
    """Analyze uncompressed mono/stereo 16-bit WAV; reject unusable audio."""
    from array import array
    with wave.open(str(path), "rb") as stream:
        if stream.getsampwidth() != 2 or stream.getnchannels() not in (1, 2):
            raise ValueError("Expected 16-bit mono or stereo PCM")
        rate, channels, frames = stream.getframerate(), stream.getnchannels(), stream.getnframes()
        if rate < 8000 or frames == 0:
            raise ValueError("Invalid narration sample rate or length")
        samples = array("h")
        samples.frombytes(stream.readframes(frames))
    if channels == 2:
        samples = array("h", ((samples[i] + samples[i + 1]) // 2 for i in range(0, len(samples), 2)))
    duration = frames / rate
    window = max(1, round(rate * 0.05))
    levels = []
    for start in range(0, len(samples), window):
        part = samples[start:start + window]
        levels.append(math.sqrt(sum(x * x for x in part) / len(part)) / 32768)
    peak = max(abs(x) for x in samples) / 32768
    rms = math.sqrt(sum(x * x for x in samples) / len(samples)) / 32768
    silence_fraction = sum(level < 0.008 for level in levels) / len(levels)
    issues = []
    if duration < 0.25:
        issues.append("too_short")
    if rms < 0.004:
        issues.append("too_quiet")
    if peak >= 0.999:
        issues.append("possible_clipping")
    if silence_fraction > 0.65:
        issues.append("excessive_silence")
    return {"passed": not issues, "issues": issues, "duration_seconds": round(duration, 3),
            "rms_dbfs": round(20 * math.log10(max(rms, 1e-10)), 1),
            "peak": round(peak, 4), "silence_fraction": round(silence_fraction, 3),
            "human_likeness_verified": False}


def reference_summary(examples):
    """Metadata-only comparison of consented creator narration reviews."""
    if not isinstance(examples, list) or len(examples) > 100:
        raise ValueError("Supply up to 100 consented reference reviews")
    allowed = {"expressive", "natural_pauses", "clear_pronunciation", "consistent_voice"}
    counts = {key: 0 for key in allowed}
    for item in examples:
        if not isinstance(item, dict) or not item.get("creator"):
            raise ValueError("Creator name required")
        if set(item) - (allowed | {"creator", "source_url"}):
            raise ValueError("Unknown review field")
        for key in allowed:
            value = item.get(key)
            if value is not None and not isinstance(value, bool):
                raise ValueError("Review fields must be booleans")
            counts[key] += int(value is True)
    return {"references": len(examples), "positive_observations": counts,
            "note": "Human observations, not a validated voice-naturalness score; do not clone creator voices."}
