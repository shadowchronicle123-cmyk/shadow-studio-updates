"""Local audio mixing for licensed music and synthesized effects."""

from array import array
from math import exp, pi, sin
from pathlib import Path
import subprocess
import wave

EFFECTS = ('none', 'message', 'impact', 'transition')
SAMPLE_RATE = 16000


def line_sound(line):
    """Return an effect name and its offset from dialogue onset in seconds."""
    effect = line.get('sound_effect', 'none')
    if effect not in EFFECTS:
        raise ValueError('Choose a supported sound effect')
    try:
        offset = float(line.get('sound_offset', 0))
    except (TypeError, ValueError) as exc:
        raise ValueError('Sound offset must be between -2 and 2 seconds') from exc
    if not -2 <= offset <= 2:
        raise ValueError('Sound offset must be between -2 and 2 seconds')
    return effect, offset


def _make_effects(destination, cues, duration):
    samples = array('h', [0]) * round(duration * SAMPLE_RATE)
    designs = {
        'message': (0.14, 760, -120, 17),
        'impact': (0.38, 108, -145, 8),
        'transition': (0.27, 320, 1900, 8),
    }
    for effect, start in cues:
        length, frequency, sweep, decay = designs[effect]
        first = round(max(0, start) * SAMPLE_RATE)
        for index in range(min(round(length * SAMPLE_RATE), len(samples) - first)):
            t = index / SAMPLE_RATE
            envelope = min(t * 140, 1) * exp(-decay * t)
            phase = 2 * pi * (frequency * t + sweep * t * t / 2)
            sample = samples[first + index] + round(7200 * envelope * sin(phase))
            samples[first + index] = max(-32768, min(32767, sample))
    with wave.open(str(destination), 'wb') as track:
        track.setparams((1, 2, SAMPLE_RATE, 0, 'NONE', 'not compressed'))
        track.writeframes(samples.tobytes())


def mix_audio(narration, music, cues, duration, voice_volume, music_volume):
    """Return a WAV of the mix, using narration to duck the music bed."""
    if not music and not cues and voice_volume == 100:
        return narration
    folder = Path(narration).parent
    destination = folder / 'mix.wav'
    args = ['ffmpeg', '-y', '-loglevel', 'error', '-i', str(narration)]
    filters = []
    inputs = ['[voice]']
    voice_input = '[0:a]'
    if music and music_volume:
        args.extend(['-stream_loop', '-1', '-i', str(music)])
        filters.extend([
            '[0:a]asplit=2[dry][key]',
            '[1:a]aformat=channel_layouts=mono,aresample=16000[bed]',
            f'[bed][key]sidechaincompress=threshold=0.022:ratio=8:attack=5:release=250,volume={music_volume / 100}[music]',
        ])
        inputs.append('[music]')
        voice_input = '[dry]'
    filters.append(f'{voice_input}volume={voice_volume / 100}[voice]')
    if cues:
        effects = folder / 'effects.wav'
        _make_effects(effects, cues, duration)
        index = len(inputs)
        args.extend(['-i', str(effects)])
        inputs.append(f'[{index}:a]')
    joined = ''.join(inputs)
    filters.append(f'{joined}amix=inputs={len(inputs)}:duration=first:normalize=0,alimiter=limit=0.95:level=false:latency=true[mix]')
    result = subprocess.run([*args, '-filter_complex', ';'.join(filters), '-map', '[mix]',
                             '-ar', str(SAMPLE_RATE), '-ac', '1', '-c:a', 'pcm_s16le', str(destination)],
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('Audio mix failed: ' + result.stderr[-600:])
    return destination
