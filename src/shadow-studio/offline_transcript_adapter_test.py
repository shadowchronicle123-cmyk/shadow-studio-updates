"""Offline ASR adapter tests with fake engine; no model download or paid API."""
from types import SimpleNamespace
from offline_transcript_adapter import transcript_words, transcribe_with_faster_whisper

segments = [
    SimpleNamespace(words=[SimpleNamespace(word=" Hello", start=0.1, end=0.4)]),
    {"words": [{"word": "world!", "start": 0.5, "end": 0.9}]},
]
expected = [{"text": "Hello", "start": 0.1, "end": 0.4},
            {"text": "world!", "start": 0.5, "end": 0.9}]
assert transcript_words(segments, clip_duration=1.0) == expected

class FakeLocalModel:
    def transcribe(self, source, **options):
        assert source == "permitted-local.mp4"
        assert options["word_timestamps"] is True
        assert options["vad_filter"] is True
        return iter(segments), None

assert transcribe_with_faster_whisper("permitted-local.mp4", FakeLocalModel()) == expected
invalid = [
    [{"words": [{"word": "no time", "start": None, "end": 0.3}]}],
    [{"words": [{"word": "late", "start": 0.8, "end": 0.9},
                {"word": "early", "start": 0.1, "end": 0.2}]}],
    [{"words": [{"word": "over", "start": 0.1, "end": 0.8},
                {"word": "lap", "start": 0.7, "end": 0.9}]}],
    [{"words": [{"word": "NaN", "start": float("nan"), "end": 0.5}]}],
    [{"words": [{"word": " ", "start": 0.1, "end": 0.5}]}],
    [{"text": "segment-level only", "start": 0.1, "end": 0.5}],
    [],
    [{"words": "not a word list"}],
    [{"words": {"word": "bad"}}],
]
for item in invalid:
    try:
        transcript_words(item)
    except ValueError:
        pass
    else:
        raise AssertionError(f"Invalid transcript accepted: {item}")
try:
    transcript_words(segments, clip_duration=0.8)
except ValueError:
    pass
else:
    raise AssertionError("Transcript beyond clip duration accepted")
for invalid_duration in (float("nan"), float("inf"), -1, 0, True):
    try:
        transcript_words(segments, clip_duration=invalid_duration)
    except ValueError:
        pass
    else:
        raise AssertionError(f"Invalid clip duration accepted: {invalid_duration}")
print("PASS local ASR validation, finite duration, fake model and word collection safety")
