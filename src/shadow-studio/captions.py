"""Write narration-aligned subtitles and expose them for local review."""

from html import escape
from pathlib import Path
import re
import textwrap

TIMING = re.compile(r'(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})')


def srt_time(seconds):
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f'{hours:02}:{minutes:02}:{secs:02},{millis:03}'


def save_scene_subtitles(story, durations, destination, narration=None):
    """Split long text without extending cues into dialogue pauses."""
    position = 0.0
    blocks = []
    for index, (line, duration) in enumerate(zip(story['lines'], durations)):
        text = ' '.join(str(line['text']).split())
        offset, spoken = narration[index] if narration is not None else (0, duration)
        start = position + offset
        position += duration
        if not text:
            continue
        rows = textwrap.wrap(text, width=40, break_on_hyphens=False)
        chunks = ['\n'.join(rows[i:i+2]) for i in range(0, len(rows), 2)]
        total = sum(map(len, chunks))
        elapsed = 0
        for chunk in chunks:
            end = elapsed + len(chunk)
            cue_start = start + spoken * elapsed / total
            cue_end = start + spoken * end / total
            blocks.append(f'{len(blocks)+1}\n{srt_time(cue_start)} --> {srt_time(cue_end)}\n{chunk}\n')
            elapsed = end
    Path(destination).write_text('\n'.join(blocks), encoding='utf-8')


def _seconds(timestamp):
    hours, minutes, fractional = timestamp.replace(',', '.').split(':')
    return int(hours) * 3600 + int(minutes) * 60 + float(fractional)


def read_captions(path):
    """Read Shadow Studio's current or legacy SRT."""
    result = []
    for block in re.split(r'\n[ \t]*\n', Path(path).read_text(encoding='utf-8').strip()):
        lines = block.splitlines()
        if len(lines) < 3:
            continue
        match = TIMING.fullmatch(lines[1].strip())
        if match:
            result.append({'start': _seconds(match[1]), 'end': _seconds(match[2]),
                           'text': '\n'.join(lines[2:])})
    return result


def as_webvtt(cues):
    blocks = ['WEBVTT']
    for cue in cues:
        start = srt_time(cue['start']).replace(',', '.')
        end = srt_time(cue['end']).replace(',', '.')
        blocks.append(f'{start} --> {end}\n{escape(cue["text"], quote=False)}')
    return '\n\n'.join(blocks) + '\n'
