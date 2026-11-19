"""Safe local setup diagnostics: no uploads, speech credits, or media generation."""
import importlib.util
import json
import shutil
import subprocess
import sys

checks=[]
def check(name, passed, detail=''):
    checks.append((name,bool(passed),detail))
    print(('PASS' if passed else 'FAIL')+' '+name+(' — '+detail if detail else ''))

check('Python 3.11 or newer',sys.version_info >= (3,11),sys.version.split()[0])
for name in ['ffmpeg','ffprobe']:
    check(name+' on PATH',bool(shutil.which(name)))
for mod in ['fastapi','uvicorn','PIL','requests','dotenv','edge_tts']:
    check(mod+' installed',importlib.util.find_spec(mod) is not None)
try:
    from fastapi.testclient import TestClient
    import app
    client=TestClient(app.app)
    for endpoint in ['/','/api/health','/api/launch-readiness','/api/starter-stories','/api/voices','/api/library']:
        r=client.get(endpoint)
        check(endpoint,r.status_code==200,str(r.status_code))
    report=client.get('/api/launch-readiness').json()
    check('Export tools ready',report['ready_to_attempt_render'])
    print('Status: '+json.dumps(report['videos']))
except Exception as exc:
    check('App imports and self-test endpoints',False,str(exc)[:300])
print('\nRESULT: '+('All checks passed' if all(row[1] for row in checks) else 'Some checks failed — fix those above before rendering'))
print('A real voice preview and rendered video review are still required before publishing.')
sys.exit(0 if all(row[1] for row in checks) else 1)
