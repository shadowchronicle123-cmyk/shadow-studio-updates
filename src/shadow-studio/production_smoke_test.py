"""Offline, non-destructive Shadow Studio production smoke test.

Tests real FFmpeg export using a synthetic stand-in voice. Does not call
ElevenLabs, Edge, YouTube or cloud services, and uses a temporary directory.
Run: .venv\\Scripts\\python.exe production_smoke_test.py
"""
import json
import math
import shutil
import tempfile
import wave
from pathlib import Path

import renderer
from quality_check import analyze


def synthetic_voice(text, path, voice, rate):
    rate_hz = 16000
    duration = max(0.65, min(2.4, 0.45 + len(text) * 0.025))
    count = round(duration * rate_hz)
    from array import array
    samples = array('h', (int(0.18 * 32767 * math.sin(2 * math.pi * 210 * i / rate_hz)) for i in range(count)))
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate_hz)
        audio.writeframes(samples.tobytes())


def main():
    print('Shadow Studio — offline export smoke test')
    assert shutil.which('ffmpeg'), 'FFmpeg not installed or missing from PATH'
    assert shutil.which('ffprobe'), 'FFprobe not installed or missing from PATH'
    dialogue = [(1, 2.4)] * 10
    holds = renderer.dialogue_holds(dialogue, .5, 2.25, 45)
    assert sum(holds)+300 == 1350
    assert min(renderer.dialogue_holds(dialogue, .5, 2.25, 30)) >= 75
    assert max(renderer.dialogue_holds(dialogue, .5, 2.25, 60)) <= 120
    print('PASS target fitting preserves manual pauses and bounds adjustments')
    original_voice = renderer.create_voice
    renderer.create_voice = synthetic_voice
    try:
        with tempfile.TemporaryDirectory(prefix='shadow_smoke_') as workspace:
            destination = Path(workspace) / 'export'
            story = {'title':'Shadow Studio test', 'lines':[
                {'speaker':'Me','text':'This is an offline export test.'},
                {'speaker':'Friend','text':'The captions and audio must match the scene length.', 'pause_before': 1.5},
            ]}
            video = renderer.generate(story, destination, style='messages', resolution='720p')
            report = analyze(video['path'])
            assert report['passed'], report['blockers']
            assert report['width'] == 720 and report['height'] == 1280
            assert report['fps'] >= 29.9, report['fps']
            captions = (destination / 'captions.srt').read_text(encoding='utf-8')
            assert '-->' in captions and story['lines'][0]['text'] in captions
            assert story['lines'][1]['text'] in ' '.join(captions.split())
            assert '00:00:03,767 --> 00:00:05,492' in captions, captions
            assert '00:00:00,000 --> 00:00:01,225' in captions, captions
            assert report['audio_present'] and report['max_volume_db'] is not None
            print('PASS real vertical 720 x 1280 30 FPS MP4 export')
            print('PASS generated non-silent audio verified by FFprobe')
            print('PASS two scene-aligned SRT cues')
            # Verify that the new guard refuses a silent scene.
            silent_path = Path(workspace) / 'silent.wav'
            with wave.open(str(silent_path), 'wb') as audio:
                audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
                audio.writeframes(b'\x00\x00' * 16000)
            try:
                renderer.validate_narration_audio(silent_path)
            except RuntimeError:
                print('PASS silent narration correctly rejected')
            else:
                raise AssertionError('Silent narration was not rejected')
            # Confirm the alternate Reddit-style renderer doesn't regress.
            reddit = renderer.generate(story, Path(workspace) / 'reddit', style='reddit', resolution='720p')
            reddit_report = analyze(reddit['path'])
            assert reddit_report['passed'] and reddit_report['audio_present']
            print('PASS Reddit-style rendered MP4 with audio')
            # Owned/test footage as a background exercises the split gameplay/text layout.
            from subprocess import run
            background = Path(workspace) / 'synthetic-background.mp4'
            run(['ffmpeg','-y','-loglevel','error','-f','lavfi','-i',
                 'color=c=lime:size=640x360:rate=24','-t','1','-c:v','libx264',
                 '-preset','ultrafast',str(background)], check=True)
            gameplay = renderer.generate(story, Path(workspace) / 'gameplay',
                                         style='messages', background=background,
                                         resolution='720p')
            gameplay_report=analyze(gameplay['path'])
            assert gameplay_report['passed'] and gameplay_report['audio_present']
            from subprocess import check_output
            from PIL import Image
            frame = check_output(['ffmpeg', '-loglevel', 'error', '-i', gameplay['path'],
                                  '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-threads', '1', '-'])
            image = Image.frombytes('RGB', (720, 1280), frame)
            assert image.getpixel((10, 530))[1] < 80
            assert image.getpixel((10, 545))[1] > 200
            print('PASS landscape gameplay fills lower 58% beneath dialogue')
            two_shots = Path(workspace) / 'two-shots.mp4'
            run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
                 'color=red:s=320x180:r=24:d=1', '-f', 'lavfi', '-i',
                 'color=blue:s=320x180:r=24:d=1', '-filter_complex',
                 '[0:v][1:v]concat=n=2:v=1:a=0', '-pix_fmt', 'yuv420p', str(two_shots)], check=True)
            looped = renderer.generate(story, Path(workspace) / 'looped', background=two_shots,
                                       gameplay_start=1.5, resolution='1080p')
            looped_report = analyze(looped['path'])
            assert looped_report['passed'], looped_report
            assert (looped_report['width'], looped_report['height'], looped_report['fps']) == (1080, 1920, 30)
            for timecode, channel in (('0.1', 2), ('0.85', 0)):
                frame = check_output(['ffmpeg', '-loglevel', 'error', '-ss', timecode, '-i', looped['path'],
                                      '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-threads', '1', '-'])
                pixel = Image.frombytes('RGB', (1080, 1920), frame).getpixel((10, 1400))
                assert pixel[channel] > 160, (timecode, pixel)
            print('PASS 1080p gameplay starts at the cursor and loops from the beginning')
            print('QUALITY: '+json.dumps({k:report[k] for k in ['passed','width','height','fps','audio_present','duration_seconds','max_volume_db']}))
    finally:
        renderer.create_voice = original_voice
    print('RESULT: ALL OFFLINE PRODUCTION CHECKS PASSED')


if __name__ == '__main__':
    main()
