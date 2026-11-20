"""Exercise the rights-aware preview API with real local media."""
from pathlib import Path
import hashlib
import io
import json
import subprocess
import tempfile
import zipfile
from unittest.mock import patch

from fastapi.testclient import TestClient
import app
from quality_check import analyze
from streamer_clips import make_preview


def main():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        media = root / "licensed"
        media.mkdir()
        item = {"creator": "Example streamer", "source_url": "https://www.twitch.tv/example",
                "permission_reference": "Written permission, agreement #123",
                "license_scope": "commercial-edit-and-repost", "file": "clip.mp4"}
        previews = root/"previews"
        previews.mkdir()
        fixture = media/"background_123456abcdef.mp4"
        subprocess.run(['ffmpeg','-y','-loglevel','error',
                        '-f','lavfi','-i','testsrc2=s=320x180:r=24:d=4',
                        '-f','lavfi','-i','sine=f=440:d=4',
                        '-c:v','libx264','-c:a','aac',str(fixture)],check=True)
        request = {**item,'background_name':fixture.name,'start_seconds':0,'end_seconds':3.5,'anchor':'right'}
        request.pop('file')
        with patch.multiple(app,UPLOADS=media,CLIPS=previews), patch.object(app.studio_tools,'PROJECTS',root/'projects'):
            client=TestClient(app.app)
            rejected=client.post('/api/streamer-clips/preview',json={**request,'license_scope':'repost'})
            assert rejected.status_code==422
            response=client.post('/api/streamer-clips/preview',json=request)
            assert response.status_code==200,response.text
            data=response.json()
            assert data['state']=='review_required'
            path=previews/(data['video_url'].rsplit('/',1)[-1]+'.mp4')
            assert client.get(data['video_url']).content==path.read_bytes()
            report=analyze(path)
            assert report['passed'] and (report['width'],report['height'],report['fps'])==(1080,1920,30)
            assert 3.3 < report['duration_seconds'] < 3.7
            review_url = '/api/streamer-clips/review'
            rows = client.get(review_url).json()
            assert len(rows) == 1 and rows[0]['state'] == 'review_required'
            assert rows[0]['creator'] == item['creator']
            assert 'permission_reference' not in rows[0] and 'file' not in rows[0]
            decision_url = review_url + '/' + rows[0]['video_id']
            handoff_url = '/api/streamer-clips/handoff/' + rows[0]['video_id']
            assert client.get(handoff_url).status_code == 409
            assert client.post(decision_url,json={'decision':'published'}).status_code == 422
            assert client.post(decision_url,json={'decision':'approved'}).status_code == 200
            handoff = client.get(handoff_url)
            assert handoff.status_code == 200, handoff.text
            with zipfile.ZipFile(io.BytesIO(handoff.content)) as archive:
                assert set(archive.namelist()) == {'short.mp4', 'manifest.json', 'README.txt'}
                manifest = json.loads(archive.read('manifest.json'))
                assert archive.read('short.mp4') == path.read_bytes()
                assert manifest['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
                assert manifest['channel'] == 'ShadowChronicles' and manifest['visibility'] == 'private'
                assert 'permission_reference' not in manifest and 'source_url' not in manifest
            original = path.read_bytes()
            path.write_bytes(b'changed before handoff')
            assert client.get(handoff_url).status_code == 409
            path.write_bytes(original)
            assert TestClient(app.app).get(review_url).json()[0]['state'] == 'approved'
            assert client.post(decision_url,json={'decision':'rejected'}).status_code == 200
            assert client.get(handoff_url).status_code == 409
            path.write_bytes(b'changed rendered clip')
            assert client.post(decision_url,json={'decision':'approved'}).status_code == 409
            assert client.get(review_url).json()[0]['state'] == 'rejected'
            assert client.post(review_url+'/000000000000',json={'decision':'approved'}).status_code == 404
            fixture.write_bytes(b'changed source')
            try:
                make_preview(root/'projects'/'streamer-clips.sqlite3',data['clip_id'],previews)
            except ValueError as exc:
                assert 'changed after rights intake' in str(exc)
            else:
                raise AssertionError('Changed source produced a clip')
    print("PASS rights evidence, deduplication, source integrity, real 1080p preview and persistent clip review")


if __name__ == "__main__":
    main()
