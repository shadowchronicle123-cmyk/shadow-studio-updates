"""Offline, non-billable integration smoke tests."""
import os
from unittest.mock import patch, Mock
from fastapi.testclient import TestClient
import app

client=TestClient(app.app)

def mock_response(json_body=None, status=200):
    r=Mock();r.ok=(status==200);r.status_code=status;r.json.return_value=json_body or {}
    r.raise_for_status.side_effect=None if status==200 else ValueError('bad')
    return r

def run():
    env={'SHADOW_OLLAMA_MODEL':'llama3.2:3b','ELEVENLABS_API_KEY':'secret-do-not-return',
         'ELEVENLABS_VOICE_ID':'XrExE9yKIg1WjnnlVkGX','SHADOW_AGENT_TOKEN':'z'*40}
    def fake_get(url, **kwargs):
        if '11434' in url:return mock_response({'models':[{'name':'llama3.2:3b'}]})
        if 'elevenlabs' in url:return mock_response({'voices':[{'voice_id':'XrExE9yKIg1WjnnlVkGX'}]})
        if '7860' in url:return mock_response({'sd_model_checkpoint':'test'})
        raise AssertionError('Unexpected URL '+url)
    with patch.dict(os.environ,env), patch('requests.get',side_effect=fake_get):
        r=client.post('/api/integrations/check');assert r.status_code==200
        d=r.json();rows={x['name']:x for x in d['integrations']}
        assert rows['Local AI stories · Ollama']['reachable'] is True
        assert rows['Premium voice · ElevenLabs']['reachable'] is True
        assert rows['AI images · Stable Diffusion WebUI']['reachable'] is True
        assert rows['Private ChatGPT MCP connector']['configured'] is True
        assert 'secret-do-not-return' not in r.text
        assert 'z'*40 not in r.text
        assert client.get('/api/integrations',headers={'Host':'attacker.com'}).status_code==400
        assert client.post('/api/integrations/check',headers={'Origin':'https://attacker.com'}).status_code==403
    with patch.dict(os.environ,{'SHADOW_OLLAMA_MODEL':'wrong-model', 'ELEVENLABS_API_KEY':'secret', 'ELEVENLABS_VOICE_ID':'voice'},clear=False),patch('requests.get',side_effect=fake_get):
        rows={x['name']:x for x in client.post('/api/integrations/check').json()['integrations']}
        assert rows['Local AI stories · Ollama']['reachable'] is False
        assert rows['Premium voice · ElevenLabs']['reachable'] is False
    print('PASS: live probe responses, missing models/voices, protected secrets, unsafe host/origin rejection')

if __name__=='__main__':run()
