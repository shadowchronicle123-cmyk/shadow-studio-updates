"""Offline batch-render acceptance test with temporary media and stand-in voices."""

import subprocess, tempfile, time
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import app, renderer
from gameplay_library import GameplayLibrary
from production_smoke_test import synthetic_voice

c=TestClient(app.app)

def complete(response):
    assert response.status_code == 200,response.text
    batch_id=response.json()['id']
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        batch=c.get('/api/job/'+batch_id).json()
        if batch['state']!='running':
            return batch
        time.sleep(.2)
    raise AssertionError(batch)

def main():
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder)
        library=GameplayLibrary(root/'gameplay')
        for identity in ('a'*12,'b'*12):
            clip=library.root/f'gameplay_{identity}.mp4'
            subprocess.run(['ffmpeg','-y','-loglevel','error','-f','lavfi','-i',
                            'color=c=lime:size=640x360:rate=24','-t','1.2',
                            '-c:v','libx264',str(clip)],check=True)
            library.register(clip)
        with patch.multiple(app,OUT=root,GAMEPLAY=library),patch.object(renderer,'create_voice',side_effect=synthetic_voice) as voice:
            assert c.post('/api/batch',json={'ideas':['valid idea','x']}).status_code==400
            assert c.post('/api/batch-ready',json={'story_indices':[0,0]}).status_code==400
            opts={'count':2,'genre':'comedy','target':30,'gameplay_id':'auto','pacing':'snappy',
                  'second_voice':'en-US-JennyNeural','captions':True}
            batch=complete(c.post('/api/batch-random',json=opts))
            assert batch['state']=='complete' and len(batch['video_ids'])==2,batch
            assert {call.args[2] for call in voice.call_args_list}=={'en-US-AriaNeural','en-US-JennyNeural'}
            assert all(clip['uses']==1 for clip in library.list_clips())
            videos=c.get('/api/library').json()['videos']
            assert len({v['title'] for v in videos})==2
            assert all(v['review_status']=='needs_review' and v['captions_url'] for v in videos)
            assert all(c.get(v['captions_url']).status_code==200 for v in videos)
            for video in videos:
                assert c.get('/api/chatgpt-handoff/'+video['id']).status_code==409
            ready=complete(c.post('/api/batch-ready',json={'story_indices':[0],'captions':False}))
            assert ready['state']=='complete',ready
            index={v['id']:v for v in c.get('/api/library').json()['videos']}
            assert index[ready['video_ids'][0]]['captions_url'] is None
    print('PASS random batch, two voices, gameplay rotation, caption links, starter render and review gating')


if __name__ == "__main__":
    main()
