"""Production ledger and real 1080p worker acceptance without external services."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sys
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch
import wave

from fastapi.testclient import TestClient
import app
import private_upload
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
        folder = (controls/record['artifact']).parent
        stem = folder/'narration.wav'
        review = json.loads((folder/'narration-review.json').read_text(encoding='utf-8'))
        assert stem.is_file()
        assert review['listening_review_approved'] is False
        assert review['narration_sha256'] == hashlib.sha256(stem.read_bytes()).hexdigest()
        assert review['narration_wav'] == f"renders/{record['day']}/narration.wav"
        packaging = json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
        assert packaging['content_type']=='ai_story' and packaging['title']==record['title']
        assert '#Shorts' in packaging['description'] and 'shorts' in packaging['tags']
        blank = root/'blank.mp4'
        subprocess.run(['ffmpeg','-y','-loglevel','error','-f','lavfi','-i',
                        'color=black:s=320x180:r=30:d=2','-c:v','libx264',str(blank)],check=True)
        report = {'passed':True,'width':1080,'height':1920,'fps':30,'duration_seconds':60}
        with patch.object(worker,'analyze',return_value=report):
            try:
                worker._check_export({'path':blank,'captions_path':folder/'captions.srt'})
            except ValueError as exc:
                assert 'near-black' in str(exc)
            else:
                raise AssertionError('Blank video passed the frame check')
            invalid = root/'invalid.srt'
            invalid.write_text('1\n00:00:02,000 --> 00:00:01,000\nInvalid cue\n',encoding='utf-8')
            try:
                worker._check_export({'path':blank,'captions_path':invalid})
            except ValueError as exc:
                assert 'captions' in str(exc)
            else:
                raise AssertionError('Backwards caption cue passed')
        with worker.ledger(controls) as db:
            assert db.execute('SELECT sha256 FROM verified_exports WHERE day=?', (record['day'],)).fetchone()[0] == (
                hashlib.sha256((controls/record['artifact']).read_bytes()).hexdigest()
            )
        channel = 'UC'+'X'*22
        transport = Mock()
        transport.post.side_effect = [
            SimpleNamespace(status_code=200,json=lambda:{'token_type':'Bearer','access_token':'test-access'}),
            SimpleNamespace(status_code=200,headers={
                'Location':'https://www.googleapis.com/upload/youtube/v3/videos?upload_id=test'}),
        ]
        transport.get.return_value = SimpleNamespace(status_code=200,json=lambda:{'items':[{'id':channel}]})
        transport.put.return_value = SimpleNamespace(status_code=201,json=lambda:{
            'id':'example1234','snippet':{'channelId':channel},'status':{'privacyStatus':'private'}})
        env = {'SHADOW_YOUTUBE_PRIVATE_AUTOMATION':'1','SHADOW_YOUTUBE_AUDIENCE':'not-made-for-kids',
               'SHADOW_YOUTUBE_CHANNEL_ID':channel,'SHADOW_YOUTUBE_CLIENT_ID':'test-client-id',
               'SHADOW_YOUTUBE_CLIENT_SECRET':'test-client-secret','SHADOW_YOUTUBE_REFRESH_TOKEN':'test-refresh'}
        try:
            private_upload.upload_daily(controls,record['day'],env,transport)
        except ValueError:
            pass
        else:
            raise AssertionError('Legacy daily upload bypassed narration approval')
        assert transport.put.call_count == 0
        assert private_upload.upload(controls,record['day'],hashlib.sha256((controls/record['artifact']).read_bytes()).hexdigest(),
                                     'test-access',channel_id=channel,confirmed=True,for_kids=False,http=transport)=='example1234'
        assert transport.put.call_args.kwargs['data']==(controls/record['artifact']).read_bytes()
        assert private_upload.upload(controls,record['day'],hashlib.sha256((controls/record['artifact']).read_bytes()).hexdigest(),
                                     'test-access',channel_id=channel,confirmed=True,for_kids=False,http=transport)=='private'
        assert transport.put.call_count==1
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
