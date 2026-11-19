"""Private transfer acceptance with a fake storage client and local approved bytes."""
from pathlib import Path
import json
import tempfile
import threading
import time
from unittest.mock import patch

from fastapi.testclient import TestClient
import app
import cloud_handoff as cloud
from approval_security import file_digest


def main():
    client = TestClient(app.app)
    settings = {'SHADOW_S3_BUCKET':'', 'SHADOW_S3_ACCESS_KEY':'', 'SHADOW_S3_SECRET_KEY':'', 'SHADOW_S3_ENDPOINT':''}
    with patch.dict('os.environ', settings):
        status = client.get('/api/cloud-handoff/config').json()
        assert not status['configured'] and len(status['missing']) == 3
        assert client.post('/api/cloud-handoff/test').status_code == 409
        assert client.post('/api/cloud-handoff/' + 'a'*12).status_code == 409

    reached, resume = threading.Event(), threading.Event()
    class FakeStorage:
        broken = False
        calls = 0

        def head_bucket(self, **kwargs):
            assert kwargs['Bucket'] == 'test-bucket'

        def upload_file(self, filename, bucket, key, Callback, ExtraArgs):
            self.calls += 1
            if self.broken:
                raise OSError('provider detail must remain private')
            assert Path(filename).read_bytes() == b'approved fixture bytes'
            Callback(8)
            reached.set()
            if not resume.wait(5):
                raise AssertionError('Timed out waiting for test')
            Callback(14)

        def generate_presigned_url(self, *args, **kwargs):
            return 'https://example.invalid/private.mp4?fake-signature'

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        jid = 'a'*12
        target = root/jid
        target.mkdir()
        movie = target/'short.mp4'
        movie.write_bytes(b'approved fixture bytes')
        record = {'id':jid, 'title':'Test Short', 'review_status':'approved', 'approved_video_sha256':file_digest(movie)}
        meta = target/'metadata.json'
        meta.write_text(json.dumps(record))
        path = '/api/cloud-handoff/'+jid

        def finish():
            for _ in range(200):
                state = client.get(path+'/status').json()
                if state['status'] in ('uploaded','failed'):
                    return state
                time.sleep(.02)
            raise AssertionError('Handoff did not finish')

        fake = FakeStorage()
        with (patch.object(cloud, 'OUT', root), patch.object(cloud, '_client', return_value=fake),
              patch('quality_check.analyze', return_value={'passed':True}),
              patch.dict('os.environ', {**settings,'SHADOW_S3_BUCKET':'test-bucket',
                         'SHADOW_S3_ACCESS_KEY':'fake','SHADOW_S3_SECRET_KEY':'fake'})):
            assert client.post('/api/cloud-handoff/test').json()['status'] == 'reachable'
            fake.broken = True
            assert client.post(path).status_code == 202
            failed = finish()
            assert failed['status'] == 'failed' and 'provider detail' not in failed['error']
            fake.broken = False
            assert client.post(path).status_code == 202
            assert reached.wait(5)
            progress = client.get(path+'/status').json()
            assert progress['status'] == 'uploading' and progress['bytes_sent'] == 8
            assert client.post(path).status_code == 202 and fake.calls == 2
            resume.set()
            ready = finish()
            assert ready['status'] == 'uploaded' and 'before publishing' in ready['request_for_chatgpt']
            assert 'fake-signature' not in (target/'cloud_handoff.json').read_text()
            meta.write_text(json.dumps({**record,'review_status':'needs_changes'}))
            assert client.get(path+'/status').json()['status'] == 'failed'
            assert client.post(path).status_code == 202
            assert finish()['status'] == 'failed' and fake.calls == 2
    print('PASS missing config, read-only probe, private transfer progress, retry, deduplication, approval and secret redaction')


if __name__ == '__main__':
    main()
