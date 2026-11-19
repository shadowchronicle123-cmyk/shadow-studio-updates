"""Offline audio import and mix regression test. Run with Python and FFmpeg installed."""

import io
import math
import subprocess
import tempfile
import time
import wave
from array import array
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import app
import renderer
from production_smoke_test import synthetic_voice

def tone(samples, frequency, level):
    return array('h', (round(level * math.sin(2*math.pi*frequency*i/16000)) for i in range(samples))).tobytes()

def music():
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as file:
        file.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        file.writeframes(tone(16000, 440, 9000))
    return stream.getvalue()

def amplitude(samples, start, end, frequency):
    return abs(sum(value * math.sin(2 * math.pi * frequency * i / 16000)
                   for i, value in enumerate(samples[round(16000*start):round(16000*end)]))) / (16000*(end-start))

def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        c = TestClient(app.app)
        with patch.multiple(app, OUT=root, UPLOADS=root), patch.object(renderer,'create_voice', side_effect=synthetic_voice):
            files = {'file': ('test audio.wav', music(), 'audio/wav')}
            assert c.post('/api/music', files=files).status_code == 400
            assert c.post('/api/music', files={'file': ('x.wav', b'bad', 'audio/wav')}, data={'licensed':True}).status_code == 400
            added = c.post('/api/music', files=files, data={'licensed':True})
            assert added.status_code == 200, added.text
            track = added.json()['name']
            assert c.get('/api/music').json()['tracks'][0]['name'] == track
            story = {'title':'Sound timeline','lines':[
                {'speaker':'A','text':'This is the first narrated message.'},
                {'speaker':'B','text':'This is the second narrated message.', 'pause_before':2, 'sound_effect':'message', 'sound_offset':-1},
            ]}
            options = {'story':story,'music_name':track,'music_volume':50,'voice_volume':80}
            assert c.post('/api/render',json={**options,'music_name':'../.env'}).status_code == 400
            r = c.post('/api/render',json=options)
            assert r.status_code == 200,r.text
            jobid = r.json()['id']
            for _ in range(100):
                job = c.get('/api/job/'+jobid).json()
                if job['state'] != 'running': break
                time.sleep(.2)
            assert job['state'] == 'complete',job
            pcm = subprocess.check_output(['ffmpeg','-loglevel','error','-i',str(root/jobid/'short.mp4'),'-vn','-ac','1','-ar','16000','-f','s16le','-'])
            values = array('h',pcm)
            quiet_bed = amplitude(values, .5, 1, 440)
            full_bed = amplitude(values, 3.6, 4.1, 440)
            voice = amplitude(values, .5, 1, 210)
            tap = amplitude(values, 3.267, 3.367, 755)
            assert full_bed > quiet_bed * 2, (quiet_bed, full_bed)
            assert 1900 < voice < 2800, voice
            assert tap > 250 and tap > 5 * amplitude(values, 3.1, 3.2, 755), tap
            assert c.get('/api/chatgpt-handoff/'+jobid).status_code == 409
            print('PASS licensed import, invalid file rejection, voice level, automatic ducking, timed message sound, review gating')


if __name__ == "__main__":
    main()
