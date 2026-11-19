"""Validate private upload and duplicate guards with a fake YouTube transport."""
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock
import requests

import private_upload
from production_worker import ledger, set_stop, status


def main():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        def fixture(day):
            folder = root/'renders'/day
            folder.mkdir(parents=True)
            content = b'synthetic video contents ' + day.encode()
            (folder/'short.mp4').write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            with ledger(root) as db:
                db.execute("""INSERT INTO jobs(day,state,idea_hash,title,voice,clip,artifact)
                           VALUES (?,'ready',?,'Test video','free','licensed',?)""",
                           (day,digest,f'renders/{day}/short.mp4'))
            return digest
        day = '2026-11-19'
        digest = fixture(day)
        transport = Mock()
        token = 'fake-oauth-fixture'
        def send(day, digest, *, confirmed=True):
            return private_upload.upload(root,day,digest,token,confirmed=confirmed,for_kids=False,http=transport)
        def blocked(action):
            try:
                action()
            except (ValueError,RuntimeError):
                return
            raise AssertionError('Expected handoff to stop')
        blocked(lambda:send(day,digest,confirmed=False))
        blocked(lambda:send(day,'0'*64))
        set_stop(root,True)
        blocked(lambda:send(day,digest))
        set_stop(root,False)
        assert transport.post.call_count==0
        location='https://www.googleapis.com/upload/youtube/v3/videos?upload_id=test-session'
        transport.post.return_value=SimpleNamespace(status_code=200,headers={'Location':location})
        transport.put.return_value=SimpleNamespace(status_code=201,json=lambda:{'id':'example1234','status':{'privacyStatus':'private'}})
        assert send(day,digest)=='example1234'
        assert transport.post.call_args.kwargs['json']['status']['privacyStatus']=='private'
        blocked(lambda:send(day,digest))
        assert transport.post.call_count==1 and transport.put.call_count==1
        assert status(root)['jobs'][0]['video_id']=='example1234'

        day='2026-11-20'
        digest=fixture(day)
        transport.put.side_effect=requests.Timeout('secret provider response')
        blocked(lambda:send(day,digest))
        blocked(lambda:send(day,digest))
        assert transport.post.call_count==2 and transport.put.call_count==2
        assert status(root)['jobs'][0]['upload_state']=='needs_reconciliation'

        day='2026-11-21'
        digest=fixture(day)
        transport.post.return_value=SimpleNamespace(status_code=200,headers={'Location':'https://example.invalid/anything'})
        blocked(lambda:send(day,digest))
        assert transport.put.call_count==2
        with ledger(root) as db:
            assert all(token not in str(tuple(row)) for row in db.execute('SELECT * FROM private_uploads'))

    print('PASS explicit consent, exact checksum, private-only request, emergency stop and no retries after ambiguous uploads')


if __name__=='__main__':
    main()
