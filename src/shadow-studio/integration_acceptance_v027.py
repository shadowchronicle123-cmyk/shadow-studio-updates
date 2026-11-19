"""No-credits acceptance tests: voice preview caching, local MCP, rendering + handoff.
Run on Windows after installing dependencies and FFmpeg:
    .venv\\Scripts\\python.exe integration_acceptance_v027.py
No secrets, online provider calls, or YouTube publishing are performed.
"""
import io, json, os, sys, tempfile, wave, math, struct
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

def fake_voice(text, path, voice, rate):
    path=Path(path)
    with wave.open(str(path),'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000)
        f.writeframes(b''.join(struct.pack('<h',round(4700*math.sin(2*math.pi*250*i/16000))) for i in range(16000)))

def main():
    import app, shadow_mcp, renderer
    from prelaunch_check import main as test_video
    c=TestClient(app.app)
    # Voice test: second request should use the file created by the first one.
    with tempfile.TemporaryDirectory() as d:
        previous=app.OUT
        app.OUT=Path(d)
        try:
            with patch.object(app,'create_voice',side_effect=fake_voice) as mock:
                a=c.post('/api/voice-preview',json={'voice':'en-US-AriaNeural','rate':0})
                b=c.post('/api/voice-preview',json={'voice':'en-US-AriaNeural','rate':0})
                assert a.status_code==b.status_code==200,(a.text[:150],b.text[:150])
                assert a.content == b.content and mock.call_count==1
            print('PASS: preview audio reusable without repeat provider call')
        finally: app.OUT=previous
    # Bridge cannot be used with no token; correct token exposes only read tools.
    client=TestClient(shadow_mcp.app)
    with patch.dict(os.environ,{'SHADOW_AGENT_TOKEN':'z'*40,'SHADOW_MCP_AUTH_MODE':'token'}):
        assert client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/list'}).status_code==401
        assert client.post('/mcp',headers={'Authorization':'Bearer bad'},json={'jsonrpc':'2.0','id':1,'method':'tools/list'}).status_code==401
        headers={'Authorization':'Bearer '+'z'*40}
        result=client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':1,'method':'initialize','params':{}})
        assert result.status_code==200 and result.json()['result']['serverInfo']['name']=='shadow-studio-private'
        result=client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}})
        assert result.status_code==200
        tools={v['name'] for v in result.json()['result']['tools']}
        assert tools=={'list_approved_videos','get_approved_video','get_approved_video_link'},tools
        result=client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'list_approved_videos','arguments':{}}})
        assert not result.json()['result']['isError']
        print('PASS: MCP authorization, initialization, read-only tools')
    test_video()
    print('RESULT: v0.27 OFFLINE INTEGRATION ACCEPTANCE PASSED')

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('FAIL:',type(e).__name__,str(e));sys.exit(1)
