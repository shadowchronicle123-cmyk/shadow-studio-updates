"""Run update installation and rollback in a temporary installation."""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import zipfile

import update_manager as updater

FEED = 'https://updates.example.com/latest.json'
ARCHIVE = 'https://updates.example.com/release.zip'


def bundle(version='9.0.0', **extra):
    files = {'app.py': 'app_version = "new"\n', 'renderer.py': 'renderer_version = "new"\n',
             'update_manager.py': f'VERSION = "{version}"\n', **extra}
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, 'w') as archive:
        for name, data in files.items():
            archive.writestr('shadow-studio/' + name, data)
    return memory.getvalue()


def update(data, checksum=None):
    manifest = {'version': '9.0.0', 'url': ARCHIVE,
                'sha256': checksum or hashlib.sha256(data).hexdigest()}
    with patch.object(updater, '_fetch', side_effect=[json.dumps(manifest).encode(), data]):
        return updater.apply(FEED)


def reject(data, expected, checksum=None):
    try:
        update(data, checksum)
    except updater.UpdateError as exc:
        assert expected in str(exc), str(exc)
    else:
        raise AssertionError('Invalid release was accepted')


def main():
    with (tempfile.TemporaryDirectory() as folder, patch.object(updater,'ROOT',Path(folder)),
          patch.object(updater,'datetime') as clock):
        clock.now.return_value = datetime(2026,11,19,tzinfo=timezone.utc)
        root = Path(folder)
        for name in ('app.py', 'renderer.py'):
            (root / name).write_text('original\n')
        original = {'.env': 'fake-key', 'projects/story.json': 'project', 'uploads/song.wav': 'music',
                    'outputs/video.mp4': 'video', 'gameplay/clip.mp4': 'gameplay'}
        for name, text in original.items():
            path = root / name
            path.parent.mkdir(exist_ok=True)
            path.write_text(text)

        reject(bundle(), 'checksum mismatch', '0' * 64)
        reject(bundle('8.0.0'), 'version does not match')
        reject(bundle(**{'renderer.py': '('}), 'incompatible Python')
        assert not (root / 'update_backups').exists()
        assert (root / 'app.py').read_text() == 'original\n'

        original_replace = updater.os.replace
        replacements = []

        def fail_second(source, destination):
            replacements.append(destination)
            if len(replacements) == 2:
                raise OSError('simulated installation failure')
            original_replace(source, destination)

        try:
            with patch.object(updater.os, 'replace', side_effect=fail_second):
                update(bundle())
        except OSError as exc:
            assert 'simulated' in str(exc)
        else:
            raise AssertionError('Expected simulated write failure')
        for name in ('app.py', 'renderer.py'):
            assert (root / name).read_text() == 'original\n'
        assert not list(root.glob('.shadow-update-*'))
        assert 'Installed 9.0.0' in update(bundle(**{'PROJECTS/story.json': 'exclude', '.env': 'exclude'}))
        assert (root / 'app.py').read_text() == 'app_version = "new"\n'
        assert len(list((root/'update_backups').iterdir())) == 2
        assert any(path.read_text() == 'original\n' for path in (root / 'update_backups').glob('*/app.py'))
        for name, text in original.items():
            assert (root / name).read_text() == text
    print('PASS verified install, validation, failed-write rollback, backups and local data preservation')


if __name__ == '__main__':
    main()
