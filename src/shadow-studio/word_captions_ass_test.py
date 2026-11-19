"""Offline ASS subtitle output regression checks."""
from word_captions_ass import _time, to_ass

words = [{"text": "WHAT?", "start": 0.1, "end": 0.4},
         {"text": "{\\pos(0,0)}", "start": 0.4, "end": 0.8}]
ass = to_ass(words, facecam=(0.2, 0.55, 0.8, 0.7))
assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
assert "&H0000FFFF" in ass  # yellow text in ASS BGR color order
assert "Dialogue: 0,0:00:00.10,0:00:00.40" in ass
assert ass.count("Dialogue:") == 2
assert "\\pos(540,1498)" in ass
assert "｛＼pos(0,0)｝" in ass  # transcript cannot inject positioning
assert _time(61.25) == "0:01:01.25"
for bad in ([{"text": "tiny", "start": 0.101, "end": 0.102}],
            [{"text": "x", "start": float("inf"), "end": 2}]):
    try:
        to_ass(bad)
    except ValueError:
        pass
    else:
        raise AssertionError(f"Invalid timing accepted: {bad}")
print("PASS ASS timing, yellow highlighting, facecam positioning, escaping and precision")
