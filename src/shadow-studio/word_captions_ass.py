"""Export planned word captions to ASS subtitles for FFmpeg/libass rendering.

No transcription, media processing, or upload is performed by this module.
"""
import math
from word_captions import plan_captions


def _time(seconds):
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError("Invalid timestamp")
    centiseconds = round(seconds * 100)
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    whole, fraction = divmod(remainder, 100)
    if hours > 9:
        raise ValueError("ASS subtitles exceed supported duration")
    return f"{hours}:{minutes:02d}:{whole:02d}.{fraction:02d}"


def _escape(text):
    # Prevent untrusted transcript words from injecting ASS override tags.
    return text.replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\n", " ").replace("\r", " ")


def to_ass(words, facecam=None, width=1080, height=1920):
    """Create one yellow, black-outlined, centered subtitle per spoken word."""
    planned = plan_captions(words, facecam, width, height)
    header = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\n"
              "ScaledBorderAndShadow: yes\nWrapStyle: 2\n\n"
              "[V4+ Styles]\n"
              "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
              "Style: Highlight,Arial,72,&H0000FFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,1,5,30,30,30,1\n\n"
              "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    lines = []
    for word in planned:
        left, top, right, bottom = word["rect"]
        x, y = (left + right) // 2, (top + bottom) // 2
        start, end = _time(word["start"]), _time(word["end"])
        if start == end:
            raise ValueError("Word duration is shorter than subtitle timing precision")
        lines.append(f"Dialogue: 0,{start},{end},Highlight,,0,0,0,,{{\\an5\\pos({x},{y})}}{_escape(word['text'])}")
    return header + "\n".join(lines) + ("\n" if lines else "")


if __name__ == "__main__":
    import argparse
    import json
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("words_json")
    parser.add_argument("output_ass")
    parser.add_argument("--facecam", help="Normalized JSON rectangle [left,top,right,bottom]")
    args = parser.parse_args()
    words = json.loads(Path(args.words_json).read_text(encoding="utf-8"))
    output = to_ass(words, json.loads(args.facecam) if args.facecam else None)
    Path(args.output_ass).write_text(output, encoding="utf-8")
