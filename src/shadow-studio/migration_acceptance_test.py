"""Exercise the shipped v0.42 installer against a candidate source ZIP."""
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

from update_manager import VERSION, _version

SOURCE = Path(__file__).resolve().parent
REPO = SOURCE.parents[1]
BASELINE = '8bb860b41496bfef560aac6f21a474c1cb908be0d9db001d46463232f7812808'
FEED = 'https://example.test/latest.json'
EXCLUDED = {'.env','.venv','__pycache__','outputs','projects','uploads','gameplay','clips','media','update_backups','node_modules','publishing_drafts','browser-evidence','advanced','candidate','backups','.git'}


def package():
    data = io.BytesIO()
    names = subprocess.check_output(['git','ls-files','-z','src/shadow-studio'], cwd=REPO).decode().split('\0')
    with zipfile.ZipFile(data,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(filter(None,names)):
            source = REPO/path
            relative = source.relative_to(SOURCE)
            if EXCLUDED.intersection(relative.parts) or source.is_symlink() or not source.is_file():
                continue
            if relative.suffix.lower() in {'.mp4','.mov','.wav','.mp3','.pyc','.zip'}:
                continue
            entry = zipfile.ZipInfo('shadow-studio/'+relative.as_posix())
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
    return data.getvalue()


def main():
    assert _version(VERSION) > _version('0.42.0')
    baseline = REPO/'docs'/'shadow-studio-v0.42.0-source.zip'
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
            'publishing_drafts/aabbccddeeff.json':b'test local draft',
            'outputs/aabbccddeeff/short.mp4':b'test output',
            'projects/aabbccddeeff.json':b'{"id":"aabbccddeeff","title":"Saved from v0.42","lines":[{"speaker":"A","text":"A fixture line"},{"speaker":"B","text":"Another line"}]}',
        }
        for name, content in local_files.items():
            path = target/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(content)
        spec = importlib.util.spec_from_file_location('baseline_updater',target/'update_manager.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.VERSION=='0.42.0'
        replace = module.os.replace
        count = 0

        def fail_third(source, destination):
            nonlocal count
            count += 1
            if count==3:
                raise OSError('simulated installation error')
            replace(source,destination)

        first = module.datetime(2026,11,19,12,0,0,tzinfo=module.timezone.utc)
        with (patch.object(module,'_fetch',side_effect=[manifest,candidate]),
              patch.object(module.os,'replace',side_effect=fail_third),
              patch.object(module,'datetime') as clock):
            clock.now.return_value = first
            try:
                module.apply(FEED)
            except OSError as exc:
                assert 'simulated' in str(exc)
            else:
                raise AssertionError('Expected failed update')
        assert app_path.read_bytes()==before
        # The old updater allocated directories by clock tick. Simulate its
        # documented later retry without removing its backup or changing real time.
        elapsed = module.datetime(2026,11,19,12,0,1,tzinfo=module.timezone.utc)
        assert (target/'studio.css').is_file()
        assert all((target/name).read_bytes()==data for name,data in local_files.items())
        with (patch.object(module,'_fetch',side_effect=[manifest,candidate]),
              patch.object(module,'datetime') as retry_clock):
            retry_clock.now.return_value = elapsed
            assert f'Installed {VERSION}' in module.apply(FEED)
        assert all((target/name).read_bytes()==content for name,content in local_files.items())
        assert any(path.read_bytes()==before for path in (target/'update_backups').glob('*/app.py'))
        smoke = f"""from fastapi.testclient import TestClient
import app, update_manager
client = TestClient(app.app)
assert update_manager.VERSION == {VERSION!r}
assert 'v{VERSION}' in client.get('/').text and client.get('/studio.css').status_code == 200
assert client.get('/api/projects/aabbccddeeff').json()['title'] == 'Saved from v0.42'
"""
        subprocess.run([sys.executable,'-c',smoke],cwd=target,check=True)
    print(f'PASS verified v0.42.0 baseline ZIP, simulated rollback, v{VERSION} launch and local byte preservation')


if __name__=='__main__':
    main()
