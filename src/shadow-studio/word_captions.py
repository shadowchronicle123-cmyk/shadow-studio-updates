"""Dependency-free word-caption timeline and facecam-safe placement planner.

Consumes word-level transcription timestamps (does not transcribe or render video).
"""
import json
import math


def plan_captions(words, facecam=None, width=1080, height=1920):
    """Return safe, non-overlapping timed word cards for vertical Shorts.

    facecam: normalized (left, top, right, bottom) rectangle, or None.
    """
    if type(width) is not int or type(height) is not int or width < 320 or height < 480:
        raise ValueError("Invalid canvas")
    if facecam is not None:
        if (not isinstance(facecam, (list, tuple)) or len(facecam) != 4
                or any(type(v) not in (float, int) or not math.isfinite(v) for v in facecam)):
            raise ValueError("Invalid facecam rectangle")
        l, t, r, b = facecam
        if not (0 <= l < r <= 1 and 0 <= t < b <= 1):
            raise ValueError("Invalid facecam rectangle")
    if not isinstance(words, list):
        raise ValueError("Expected word list")
    previous_end = 0
    result = []
    for word in words:
        if not isinstance(word, dict) or not isinstance(word.get("text"), str):
            raise ValueError("Invalid word")
        text = word["text"].strip()
        start, end = word.get("start"), word.get("end")
        if (not text or len(text) > 40 or type(start) not in (int, float)
                or type(end) not in (int, float) or not math.isfinite(start)
                or not math.isfinite(end) or start < previous_end or end <= start):
            raise ValueError("Invalid or overlapping word timestamps")
        previous_end = end
        # Preferred central caption zones, chosen to avoid facecam and UI margins.
        zones = [(0.24, 0.58, 0.76, 0.68), (0.24, 0.73, 0.76, 0.83),
                 (0.24, 0.36, 0.76, 0.46)]
        def overlap(a, b):
            return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
                0, min(a[3], b[3]) - max(a[1], b[1]))
        candidates = [z for z in zones if facecam is None or overlap(z, facecam) == 0]
        if not candidates:
            raise ValueError("No facecam-safe caption zone available")
        zone = candidates[0]
        result.append({"text": text, "start": start, "end": end,
                       "highlight": True, "rect": [round(zone[0] * width),
                       round(zone[1] * height), round(zone[2] * width),
                       round(zone[3] * height)]})
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("words_json")
    parser.add_argument("--facecam", help="JSON [left,top,right,bottom] normalized")
    args = parser.parse_args()
    with open(args.words_json, encoding="utf-8") as stream:
        words = json.load(stream)
    print(json.dumps(plan_captions(words, json.loads(args.facecam) if args.facecam else None)))
