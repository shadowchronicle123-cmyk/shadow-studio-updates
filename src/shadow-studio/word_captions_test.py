"""Regression checks for the dependency-free caption timeline planner."""
from word_captions import plan_captions

words = [{"text": "THAT'S", "start": 0.1, "end": 0.4},
         {"text": "CRAZY!", "start": 0.4, "end": 0.8}]
result = plan_captions(words, facecam=(0.0, 0.0, 0.4, 0.3))
assert len(result) == 2
assert result[0]["highlight"] is True
assert result[0]["end"] == result[1]["start"]
assert result[0]["rect"][2] <= 1080
assert plan_captions(words, facecam=(0.2, 0.55, 0.8, 0.7))[0]["rect"][1] >= 0.7 * 1920
for invalid in ([{"text": "bad", "start": 0.1, "end": 0.1}],
                [{"text": "bad", "start": True, "end": 1}],
                [{"text": "bad", "start": float("nan"), "end": 1}],
                [words[1], words[0]]):
    try:
        plan_captions(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError(f"Invalid captions accepted: {invalid}")
try:
    plan_captions(words, facecam=(0.0, 0.0, 1.0, 1.0))
except ValueError:
    pass
else:
    raise AssertionError("Fully obscured canvas accepted")
print("PASS word timing, highlighted words, facecam-safe positioning and invalid input rejection")
