"""Validate private upload and duplicate guards with a fake YouTube transport."""
import hashlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock
import requests

import private_upload
from authorize_youtube import write_config
from production_worker import ledger, set_stop, status

CHANNEL = 'UC' + 'X'*22


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
                db.execute('INSERT INTO verified_exports VALUES (?,?)',(day,digest))
            return digest
        day = '2026-11-19'
        digest = fixture(day)
        transport = Mock()
        token = 'fake-oauth-fixture'
        transport.get.return_value = SimpleNamespace(status_code=200,json=lambda:{'items':[{'id':CHANNEL}]})
        def send(day, digest, *, confirmed=True):
            return private_upload.upload(root,day,digest,token,channel_id=CHANNEL,
                                         confirmed=confirmed,for_kids=False,http=transport)
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
        transport.put.return_value=SimpleNamespace(status_code=201,json=lambda:{
            'id':'example1234','snippet':{'channelId':CHANNEL},'status':{'privacyStatus':'private'}})
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
        assert transport.post.call_count==2 and transport.put.call_count==3
        assert transport.put.call_args.kwargs['headers']['Content-Range'].startswith('bytes */')
        assert status(root)['jobs'][0]['upload_state']=='needs_reconciliation'

        day='2026-11-21'
        digest=fixture(day)
        transport.post.return_value=SimpleNamespace(status_code=200,headers={'Location':'https://example.invalid/anything'})
        blocked(lambda:send(day,digest))
        assert transport.put.call_count==3
        assert status(root)['jobs'][0]['upload_state']=='retryable'
        transport.put.side_effect=None
        transport.post.return_value=SimpleNamespace(status_code=200,headers={'Location':location})
        assert send(day,digest)=='example1234'

        day='2026-11-22'
        digest=fixture(day)
        transport.get.return_value=SimpleNamespace(status_code=200,json=lambda:{'items':[{'id':'UC'+'Y'*22}]})
        calls=transport.post.call_count
        blocked(lambda:send(day,digest))
        assert transport.post.call_count==calls and status(root)['jobs'][0]['upload_state'] is None
        transport.get.return_value=SimpleNamespace(status_code=200,json=lambda:{'items':[{'id':CHANNEL}]})

        env={'SHADOW_YOUTUBE_PRIVATE_AUTOMATION':'1','SHADOW_YOUTUBE_AUDIENCE':'not-made-for-kids',
             'SHADOW_YOUTUBE_CHANNEL_ID':CHANNEL,'SHADOW_YOUTUBE_CLIENT_ID':'fake-client-id',
             'SHADOW_YOUTUBE_CLIENT_SECRET':'fake-client-secret','SHADOW_YOUTUBE_REFRESH_TOKEN':'fake-refresh'}
        blocked(lambda:private_upload.upload_daily(root,day,{},transport))
        set_stop(root,True)
        assert private_upload.upload_daily(root,day,env,transport)=='stopped'
        set_stop(root,False)
        assert private_upload.upload_daily(root,'2026-11-23',env,transport)=='no_verified_export_today'
        assert transport.post.call_count==calls
        transport.post.side_effect=[
            SimpleNamespace(status_code=200,json=lambda:{'token_type':'Bearer','access_token':token}),
            SimpleNamespace(status_code=200,headers={'Location':location}),
        ]
        path=root/'renders'/day/'short.mp4'
        original=path.read_bytes()
        path.write_bytes(b'changed')
        blocked(lambda:private_upload.upload_daily(root,day,env,transport))
        assert transport.post.call_count==calls+1
        path.write_bytes(original)
        transport.post.side_effect=[
            SimpleNamespace(status_code=200,json=lambda:{'token_type':'Bearer','access_token':token}),
            SimpleNamespace(status_code=200,headers={'Location':location}),
        ]
        assert private_upload.upload_daily(root,day,env,transport)=='example1234'
        assert private_upload.upload_daily(root,day,env,transport)=='private'
        assert transport.post.call_count==calls+3
        with ledger(root) as db:
            assert all(not any(credential in str(tuple(row)) for credential in (token,'fake-client-secret','fake-refresh'))
                       for row in db.execute('SELECT * FROM private_uploads'))
        credentials = SimpleNamespace(token=token,refresh_token='fake-refresh',
                                      client_id='fake-client-id',client_secret='fake-client-secret')
        config = root/'youtube.json'
        verify = Mock()
        write_config(config,credentials,CHANNEL,'not-made-for-kids',False,verify=verify)
        verify.assert_called_once()
        saved=private_upload.read_worker_settings(config)
        assert saved=={**env,'SHADOW_YOUTUBE_PRIVATE_AUTOMATION':'0'}
        if os.name=='posix':
            assert config.stat().st_mode & 0o777 == 0o600
        try:
            write_config(config,credentials,CHANNEL,'not-made-for-kids',True,verify=verify)
        except FileExistsError:
            pass
        else:
            raise AssertionError('Expected existing credentials to be preserved')
        assert saved==private_upload.read_worker_settings(config)

        day='2026-11-24'
        digest=fixture(day)
        contents=(root/'renders'/day/'short.mp4').read_bytes()
        transport.post.side_effect=None
        complete=transport.put.return_value
        transport.put.side_effect=[
            requests.Timeout('response dropped'),
            SimpleNamespace(status_code=308,headers={'Range':'bytes=0-7'}),
            complete,
        ]
        calls=transport.post.call_count
        assert send(day,digest)=='example1234'
        assert transport.post.call_count==calls+1
        assert transport.put.call_args.kwargs['headers']['Content-Range']==f'bytes 8-{len(contents)-1}/{len(contents)}'
        assert transport.put.call_args.kwargs['data']==contents[8:]

        day='2026-11-25'
        digest=fixture(day)
        transport.put.side_effect=[requests.Timeout('final response dropped'),complete]
        calls=transport.put.call_count
        assert send(day,digest)=='example1234'
        assert transport.put.call_count==calls+2
        assert transport.put.call_args.kwargs['data']==b''

    print('PASS channel pinning, OAuth refresh, daily opt-in, exact checksum, pause and safe upload retries')


if __name__=='__main__':
    main()
