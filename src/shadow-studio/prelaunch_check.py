"""Shadow Studio v0.25 — non-destructive Windows prelaunch verification.

Run from the extracted shadow-studio directory:
    .venv\\Scripts\\python.exe prelaunch_check.py

Uses a synthetic audio fixture, not cloud voices. Makes no network requests,
never publishes, and writes video test files only to a temporary directory.
"""
import hashlib
import io
import json
import math
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
import zipfile
from pathlib import Path
from unittest.mock import patch


def synthetic_voice(text, destination, voice, rate):
    hz = 16000
    seconds = max(.85, min(2.5, 0.6 + len(text) * .026))
    n = round(seconds * hz)
    with wave.open(str(destination), 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(hz)
        f.writeframes(b''.join(struct.pack('<h', int(4800 * math.sin(2*math.pi*260*i/hz))) for i in range(n)))


def main():
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise RuntimeError('FFmpeg and FFprobe must be on PATH')
    try:
        from fastapi.testclient import TestClient
        import app
        import renderer
        from quality_check import analyze
    except ImportError as exc:
        raise RuntimeError('Install requirements first: python -m pip install -r requirements.txt') from exc

    app_client = TestClient(app.app)
    assert app_client.get('/api/health').status_code == 200
    assert app_client.get('/api/health', headers={'Host':'external.example'}).status_code == 400
    assert app_client.post('/api/story', json={'idea':'Test idea'},
                           headers={'Origin':'https://external.example'}).status_code == 403
    assert app_client.get('/').headers['X-Frame-Options'] == 'DENY'
    print('PASS: local API works, cross-site requests and unsafe hosts are blocked')

    with tempfile.TemporaryDirectory(prefix='shadow_prelaunch_') as dirname:
        work = Path(dirname)
        story = {'title':'Shadow Studio readiness test', 'lines':[
            {'speaker':'Me','text':'Can we finish the video before tonight?'},
            {'speaker':'Friend','text':'Yes, the caption timing and voice track look good.'},
        ]}
        with patch.object(renderer, 'create_voice', side_effect=synthetic_voice):
            rendered = renderer.generate(story, work/'sample', resolution='720p')
        movie = Path(rendered['path'])
        report = analyze(movie)
        assert report['passed'], report
        assert (report['width'], report['height'], report['fps']) == (720,1280,30)
        assert report['audio_present']
        subtitles = (movie.parent/'captions.srt').read_text(encoding='utf-8')
        from captions import read_captions
        cues = read_captions(movie.parent/'captions.srt')
        transcript = ' '.join(' '.join(cue['text'].split()) for cue in cues)
        assert transcript == ' '.join(line['text'] for line in story['lines'])
        assert len(cues) >= len(story['lines']) and all(cue['start'] < cue['end'] for cue in cues)
        print('PASS: real vertical MP4, non-silent audio and timed captions')

        original_out = app.OUT
        app.OUT = work/'library'
        app.OUT.mkdir()
        jid = '012345abcdef'
        folder = app.OUT/jid
        folder.mkdir()
        shutil.copy2(movie, folder/'short.mp4')
        shutil.copy2(movie.parent/'captions.srt', folder/'captions.srt')
        try:
            app.archive_video(jid, story['title'], 'Synthetic fixture; not for publishing',
                              rendered['duration'], 'messages', '720p', 'offline-test')
            assert app_client.get(f'/api/chatgpt-handoff/{jid}').status_code == 409
            review = app_client.patch(f'/api/library/{jid}/review',
                                      json={'status':'approved','notes':'offline test only'})
            assert review.status_code == 200, review.text
            handoff = app_client.get(f'/api/chatgpt-handoff/{jid}')
            assert handoff.status_code == 200, handoff.text[:250]
            with zipfile.ZipFile(io.BytesIO(handoff.content)) as archive:
                manifest = json.loads(archive.read('handoff.json'))
                assert manifest['sha256'] == hashlib.sha256(movie.read_bytes()).hexdigest()
                assert archive.read('short.mp4') == movie.read_bytes()
                assert archive.read('captions.srt') == subtitles.encode('utf-8')
                assert manifest['not_authorized_for_public_publishing'] is True
            print('PASS: unapproved videos blocked; approved MP4 and SRT handoff verified')
            # MP4 trailer edits can leave a technically valid file but still
            # change the approved content bytes. They must require reapproval.
            with (folder/'short.mp4').open('ab') as f:
                f.write(b'\nshadow-post-approval-modification')
            assert analyze(folder/'short.mp4')['passed'], 'Valid MP4 fixture was unexpectedly rejected'
            assert app_client.get(f'/api/chatgpt-handoff/{jid}').status_code == 409
            assert app_client.get('/api/publishing-kit').status_code == 404
            from approval_security import approved_unchanged
            assert not approved_unchanged(json.loads((folder/'metadata.json').read_text()), folder/'short.mp4')
            print('PASS: technically valid but modified video invalidates publishing approval')
            shutil.copy2(movie,folder/'short.mp4')
            # Reapprove the restored video before corruption test.
            assert app_client.patch(f'/api/library/{jid}/review',json={'status':'approved'}).status_code == 200


            # Alter encoded payload but keep readable MP4 container and audio.
            packets = json.loads(subprocess.check_output(['ffprobe','-v','error',
                         '-select_streams','v:0','-show_packets','-show_entries',
                         'packet=pos,size','-of','json',str(folder/'short.mp4')]))['packets']
            candidate = next((p for p in packets[2:] if int(p['size']) > 250), None)
            if candidate:
                b = bytearray((folder/'short.mp4').read_bytes())
                pos, size = int(candidate['pos']), int(candidate['size'])
                end = min(pos+size,pos+500)
                b[pos+10:end] = b'\x00'*(end-pos-10)
                (folder/'short.mp4').write_bytes(b)
                corrupt = analyze(folder/'short.mp4')
                assert not corrupt['passed'], corrupt
                assert app_client.get(f'/api/chatgpt-handoff/{jid}').status_code == 409
                print('PASS: damaged encoded video blocked even after earlier approval')
            else:
                print('SKIP: no sufficiently large encoded video packet for corruption test')
        finally:
            app.OUT = original_out
    print('RESULT: PRELAUNCH OFFLINE CHECK PASSED')
    print('LIMITATIONS: Natural voice quality, real Windows device, cloud upload and vidIQ are NOT tested.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('PRELAUNCH CHECK FAILED:', type(exc).__name__, str(exc))
        sys.exit(1)
