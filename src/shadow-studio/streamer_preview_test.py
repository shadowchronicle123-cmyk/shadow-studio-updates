"""Exercise the rights-aware preview API with real local media."""
from pathlib import Path
import subprocess
import tempfile
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
            fixture.write_bytes(b'changed source')
            try:
                make_preview(root/'projects'/'streamer-clips.sqlite3',data['clip_id'],previews)
            except ValueError as exc:
                assert 'changed after rights intake' in str(exc)
            else:
                raise AssertionError('Changed source produced a clip')
    print("PASS rights evidence, deduplication, source integrity and real 1080p preview download")


if __name__ == "__main__":
    main()
