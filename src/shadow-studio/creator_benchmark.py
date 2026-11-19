"""Offline original-story consistency and opt-in competitor benchmark checks.

Competitor examples are metadata only, never downloaded or copied.
"""
from collections import Counter
from pathlib import Path
import json
import re

DEFAULT_PROFILE = {
    "brand": "ShadowChronicles",
    "min_duration": 30,
    "max_duration": 60,
    "min_lines": 6,
    "min_speakers": 2,
    "max_hook_words": 28,
    "max_line_words": 45,
    "required_resolution": [1080, 1920],
    "fps": 30,
}


def words(value):
    return re.findall(r"[\w']+", value.casefold())


def evaluate(story, *, duration=None, resolution=None, fps=None, profile=None):
    profile = profile or DEFAULT_PROFILE
    lines = story.get("lines", [])
    errors = []
    if not isinstance(lines, list) or len(lines) < profile["min_lines"]:
        errors.append("too_few_story_beats")
    if not lines or any(not isinstance(x, dict) or not isinstance(x.get("text"), str)
                        or not x["text"].strip() for x in lines):
        errors.append("invalid_dialogue")
        return {"passed": False, "issues": errors}
    if len({str(x.get("speaker", "")).strip().casefold() for x in lines}) < profile["min_speakers"]:
        errors.append("insufficient_speakers")
    if len(words(lines[0]["text"])) > profile["max_hook_words"]:
        errors.append("slow_opening")
    if any(len(words(x["text"])) > profile["max_line_words"] for x in lines):
        errors.append("long_dialogue")
    normalized = [" ".join(words(x["text"])) for x in lines]
    if len(set(normalized)) != len(normalized):
        errors.append("repeated_dialogue")
    if duration is not None and not profile["min_duration"] <= duration <= profile["max_duration"]:
        errors.append("duration_out_of_range")
    if resolution is not None and list(resolution) != profile["required_resolution"]:
        errors.append("wrong_resolution")
    if fps is not None and abs(fps - profile["fps"]) > 0.1:
        errors.append("wrong_fps")
    return {"passed": not errors, "issues": errors,
            "hook_words": len(words(lines[0]["text"])),
            "dialogue_lines": len(lines), "speaker_count": len({str(x.get("speaker", "")).casefold() for x in lines})}


def compare_creators(examples):
    """Summarize owner-supplied public metadata; no scraping, ranking, or copying content."""
    if not isinstance(examples, list) or len(examples) > 100:
        raise ValueError("Supply up to 100 creator metadata examples")
    cleaned = []
    for item in examples:
        if not isinstance(item, dict) or not isinstance(item.get("creator"), str):
            raise ValueError("Each example needs a creator")
        duration = item.get("duration_seconds")
        views = item.get("views")
        if (not isinstance(duration, (int, float)) or isinstance(duration, bool)
            or not 1 <= duration <= 180 or not isinstance(views, int)
            or isinstance(views, bool) or views < 0):
            raise ValueError("Invalid duration or view count")
        hook = item.get("hook_type", "unspecified")
        if hook not in ("question", "conflict", "surprise", "unspecified"):
            raise ValueError("Unknown hook type")
        cleaned.append({"creator": item["creator"][:80], "duration": duration, "views": views, "hook": hook})
    if not cleaned:
        return {"count": 0, "median_duration_seconds": None, "hook_types": {}}
    durations = sorted(x["duration"] for x in cleaned)
    mid = len(durations) // 2
    median = durations[mid] if len(durations) % 2 else (durations[mid-1] + durations[mid]) / 2
    return {"count": len(cleaned), "median_duration_seconds": median,
            "hook_types": dict(Counter(x["hook"] for x in cleaned)),
            "note": "Descriptive metadata only; views do not establish causal success."}


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--story", type=Path)
    parser.add_argument("--examples", type=Path)
    args = parser.parse_args()
    if not args.story and not args.examples:
        parser.error("Supply --story or --examples")
    if args.story:
        result = evaluate(json.loads(args.story.read_text(encoding="utf-8")))
        print(json.dumps(result, indent=2))
        if not result["passed"]:
            return 1
    if args.examples:
        print(json.dumps(compare_creators(json.loads(args.examples.read_text(encoding="utf-8"))), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
