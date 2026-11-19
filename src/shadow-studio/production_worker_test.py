"""Production ledger and real 1080p worker acceptance without external services."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import sys
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch
import wave

from fastapi.testclient import TestClient
import app
import production_worker as worker
from production_smoke_test import synthetic_voice

IDEAS = [
    'My girlfriend invited her ex to our wedding on Saturday at the Lake House.',
    'My roommate overcharged me for rent just before our landlord asked for the October statement.',
    'My coworker blamed me for changing a second shift work order, but I was on first shift.',
]


def longer_fixture(text, path, voice, rate):
    synthetic_voice(text, path, voice, rate)
    with wave.open(str(path),'rb') as source:
        params = source.getparams()
        samples = source.readframes(source.getnframes())
    frames = round(4.5 * params.framerate)
    with wave.open(str(path),'wb') as destination:
        destination.setparams(params)
        destination.writeframes((samples*6)[:frames*params.sampwidth])


def main(real=False):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        media = root/'owned'
        media.mkdir()
        for name, color in [('first','green'),('second','blue')]:
            subprocess.run(['ffmpeg','-y','-loglevel','error','-f','lavfi','-i',
                            f'color={color}:s=320x180:r=24:d=1.5','-c:v','libx264',str(media/(name+'.mp4'))],check=True)
        selected = tuple(sorted(media.glob('*.mp4')))
        before = {p:hashlib.sha256(p.read_bytes()).digest() for p in selected}
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _:worker.reserve(root/'race','2026-11-19',IDEAS,selected)[0],range(2)))
        assert sorted(results)==['daily_limit','reserved']
        assert worker.reserve(root/'race','2026-11-20',IDEAS,selected)[0]=='active_job'
        interrupted = root/'interrupted'
        assert worker.reserve(interrupted,'2026-11-19',IDEAS,selected,invocation='original')[0]=='reserved'
        assert worker.recover_service(interrupted,'other') == 0
        assert worker.recover_service(interrupted,'original') == 1
        assert worker.recover_service(interrupted,'original') == 0
        assert worker.reserve(interrupted,'2026-11-19',IDEAS,selected)[0]=='daily_limit'
        assert worker.reserve(interrupted,'2026-11-20',IDEAS,selected)[0]=='reserved'
        controls = root/'control'
        with patch.dict('os.environ',{'SHADOW_PRODUCTION_STATE':str(controls)}):
            client=TestClient(app.app)
            assert client.get('/api/production/status').json()['configured']
            assert client.post('/api/production/control',json={'stopped':True}).json()['stopped']
            assert worker.run_day(controls,IDEAS,media,day='2026-11-19')=='stopped'
            assert client.post('/api/production/control',json={'stopped':False}).status_code==200

        options = {} if real else {'narrator':longer_fixture}
        implementation = worker.generate
        def render(*args, **kwargs):
            if not real and calls[0] == 0:
                calls[0] += 1
                raise RuntimeError('test transient error')
            return implementation(*args, **kwargs)
        calls = [0]
        with patch.object(worker,'generate',side_effect=render):
            result=worker.run_day(controls,IDEAS,media,day='2026-11-19',**options)
        record=worker.status(controls)['jobs'][0]
        assert result=='ready',record
        assert 30 <= record['duration'] <= 60
        assert record['attempts']==2 or real
        assert (controls/record['artifact']).is_file()
        with worker.ledger(controls) as db:
            assert db.execute('SELECT sha256 FROM verified_exports WHERE day=?', (record['day'],)).fetchone()[0] == (
                hashlib.sha256((controls/record['artifact']).read_bytes()).hexdigest()
            )
        with patch.dict('os.environ',{'SHADOW_PRODUCTION_STATE':str(controls)}):
            downloaded = TestClient(app.app).get('/api/production/render/2026-11-19')
            assert downloaded.status_code == 200 and downloaded.content == (controls/record['artifact']).read_bytes()
        assert worker.run_day(controls,IDEAS,media,day='2026-11-19',**options)=='daily_limit'
        state,plan = worker.reserve(controls,'2026-11-20',IDEAS,selected)
        assert state=='reserved' and plan[0]==IDEAS[1] and plan[2]==selected[1] and plan[1]!=worker.VOICES[0]
        assert all(hashlib.sha256(path.read_bytes()).digest()==digest for path,digest in before.items())

        def stop_early(*args, **kwargs):
            worker.set_stop(root/'stop-mid',True)
            kwargs['progress'](10,'Checking stop')
        with patch.object(worker,'generate',side_effect=stop_early):
            assert worker.run_day(root/'stop-mid',IDEAS,media,day='2026-11-19',**options)=='stopped'
        assert not list((root/'stop-mid'/'renders').glob('**/*.mp4'))
    print('PASS daily ledger, uniqueness, rotation, zero paid providers, stop, retries and verified 1080p production'
          + (' with real eSpeak' if real else ' with offline test narration'))


if __name__=='__main__':
    main('--real' in sys.argv)
