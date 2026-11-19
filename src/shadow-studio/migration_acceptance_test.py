"""Exercise the shipped v0.41 installer against a candidate source ZIP."""
import hashlib
import importlib.util
import io
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch
import zipfile

from update_manager import VERSION

SOURCE = Path(__file__).resolve().parent
REPO = SOURCE.parents[1]
BASELINE = '3c4c8d84b6d9b3736c37f6e8518a2861e6ec4c5e3add390b308d1a852c5ff587'
FEED = 'https://example.test/latest.json'
EXCLUDED = {'.env','.venv','__pycache__','outputs','projects','uploads','gameplay','clips','media','update_backups','node_modules'}


def package():
    data = io.BytesIO()
    names = subprocess.check_output(['git','ls-files','-z','src/shadow-studio'], cwd=REPO).decode().split('\0')
    with zipfile.ZipFile(data,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in filter(None,names):
            source = REPO/path
            relative = source.relative_to(SOURCE)
            if not EXCLUDED.intersection(relative.parts) and source.is_file():
                archive.write(source,'shadow-studio/'+relative.as_posix())
    return data.getvalue()


def main():
    assert VERSION == '0.42.0'
    baseline = REPO/'docs'/'shadow-studio-v0.41.0-source.zip'
    assert hashlib.sha256(baseline.read_bytes()).hexdigest() == BASELINE
    candidate = package()
    with zipfile.ZipFile(io.BytesIO(candidate)) as archive:
        assert archive.testzip() is None and 'shadow-studio/studio.css' in archive.namelist()
    manifest = json.dumps({'version':VERSION,'url':'https://example.test/shadow.zip',
                           'sha256':hashlib.sha256(candidate).hexdigest()}).encode()
    with tempfile.TemporaryDirectory(prefix='shadow_upgrade_') as folder:
        target = Path(folder)/'shadow-studio'
        with zipfile.ZipFile(baseline) as archive:
            archive.extractall(folder)
        # Represent a prior installation with caches older than the update.
        for path in target.glob('*.py'):
            os.utime(path, (1_700_000_000, 1_700_000_000))
        app_path = target/'app.py'
        before = app_path.read_bytes()
        local_files = {
            '.env':b'# local preferences test',
            'gameplay/test.mp4':b'test uploaded game',
            'uploads/song.wav':b'test music',
            'outputs/aabbccddeeff/short.mp4':b'test output',
            'projects/aabbccddeeff.json':b'{"id":"aabbccddeeff","title":"Saved from v0.41","lines":[{"speaker":"A","text":"A fixture line"},{"speaker":"B","text":"Another line"}]}',
        }
        for name, content in local_files.items():
            path = target/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(content)
        spec = importlib.util.spec_from_file_location('baseline_updater',target/'update_manager.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.VERSION=='0.41.0'
        replace = module.os.replace
        count = 0

        def fail_third(source, destination):
            nonlocal count
            count += 1
            if count==3:
                raise OSError('simulated installation error')
            replace(source,destination)

        with patch.object(module,'_fetch',side_effect=[manifest,candidate]), patch.object(module.os,'replace',side_effect=fail_third):
            try:
                module.apply(FEED)
            except OSError as exc:
                assert 'simulated' in str(exc)
            else:
                raise AssertionError('Expected failed update')
        assert app_path.read_bytes()==before
        assert not (target/'studio.css').exists()
        with patch.object(module,'_fetch',side_effect=[manifest,candidate]):
            assert 'Installed 0.42.0' in module.apply(FEED)
        assert all((target/name).read_bytes()==content for name,content in local_files.items())
        assert any(path.read_bytes()==before for path in (target/'update_backups').glob('*/app.py'))
        smoke = """from fastapi.testclient import TestClient
import app, update_manager
client = TestClient(app.app)
assert update_manager.VERSION == '0.42.0'
assert client.get('/').status_code == 200 and client.get('/studio.css').status_code == 200
assert client.get('/api/projects/aabbccddeeff').json()['title'] == 'Saved from v0.41'
"""
        subprocess.run([sys.executable,'-c',smoke],cwd=target,check=True)
    print('PASS verified v0.41.0 package, simulated rollback, v0.42.0 launch and local byte preservation')


if __name__=='__main__':
    main()
