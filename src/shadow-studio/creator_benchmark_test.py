"""Offline competitor metadata and brand consistency regression tests."""
from creator_benchmark import evaluate, compare_creators

STORY = {"title": "The surprise guest", "lines": [
    {"speaker": "Me", "text": "My girlfriend invited her ex to our wedding."},
    {"speaker": "Friend", "text": "Did she tell you why?"},
    {"speaker": "Me", "text": "She said it was a surprise."},
    {"speaker": "Friend", "text": "That sounds uncomfortable."},
    {"speaker": "Me", "text": "Then I found the seating chart."},
    {"speaker": "Friend", "text": "What did you do next?"},
]}


def main():
    assert evaluate(STORY, duration=45, resolution=(1080, 1920), fps=30)["passed"]
    bad = {"lines": STORY["lines"][:1]}
    assert not evaluate(bad)["passed"]
    repeated = {"lines": STORY["lines"][:5] + STORY["lines"][:1]}
    assert "repeated_dialogue" in evaluate(repeated)["issues"]
    assert "wrong_resolution" in evaluate(STORY, resolution=(1920, 1080))["issues"]
    assert "duration_out_of_range" in evaluate(STORY, duration=90)["issues"]
    summary = compare_creators([
        {"creator": "Example A", "duration_seconds": 35, "views": 1000, "hook_type": "conflict"},
        {"creator": "Example B", "duration_seconds": 55, "views": 3000, "hook_type": "question"},
    ])
    assert summary["count"] == 2 and summary["median_duration_seconds"] == 45
    assert summary["hook_types"] == {"conflict": 1, "question": 1}
    try:
        compare_creators([{"creator": "Example", "duration_seconds": 20, "views": -1}])
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid metadata accepted")
    print("PASS original-story brand consistency and descriptive competitor benchmarks")


if __name__ == "__main__":
    main()
