"""Convert offline ASR word segments into Shadow Studio's timed caption schema.

Accepts faster-whisper-like segment objects with .words, or equivalent JSON dicts.
The caller supplies the local speech-recognition engine and model.
"""
import math

from word_captions import plan_captions


def transcript_words(segments, *, clip_duration=None):
    """Normalize and validate word-level ASR results; never fabricate timestamps."""
    if clip_duration is not None and (type(clip_duration) not in (int, float)
                                      or not math.isfinite(clip_duration) or clip_duration <= 0):
        raise ValueError("Invalid clip duration")
    if not isinstance(segments, (list, tuple)):
        raise ValueError("Expected ASR segments")
    normalized = []
    for segment in segments:
        words = segment.get("words") if isinstance(segment, dict) else getattr(segment, "words", None)
        if words is None:
            raise ValueError("ASR segment missing word timestamps")
        if not isinstance(words, (list, tuple)):
            raise ValueError("ASR words must be a list or tuple")
        for word in words:
            if isinstance(word, dict):
                text, start, end = word.get("word"), word.get("start"), word.get("end")
            else:
                text = getattr(word, "word", None)
                start, end = getattr(word, "start", None), getattr(word, "end", None)
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Invalid ASR word")
            normalized.append({"text": text.strip(), "start": start, "end": end})
    if not normalized:
        raise ValueError("No spoken words found; refuse blank caption output")
    # Reuse the renderer's strict non-overlap and finite-time validation.
    plan_captions(normalized)
    if clip_duration is not None and normalized[-1]["end"] > clip_duration:
        raise ValueError("ASR word extends past clip duration")
    return normalized


def transcribe_with_faster_whisper(source, model):
    """Run an already-installed local faster-whisper model with word timestamps.

    This function never downloads a model. Pass an initialized WhisperModel whose
    weights are already present on disk. It does not upload or publish content.
    """
    segments, _info = model.transcribe(str(source), word_timestamps=True, vad_filter=True)
    return transcript_words(list(segments))
