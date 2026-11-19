"""Build and verify a downloadable QA candidate without publishing it."""
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile

from migration_acceptance_test import main as exercise_migration, package
from unittest.mock import patch
from update_manager import VERSION

BASE = 'https://shadowchronicle123-cmyk.github.io/shadow-studio-updates/'


def main(destination):
    destination.mkdir(parents=True, exist_ok=True)
    filename = f'shadow-studio-v{VERSION}-source.zip'
    archive = destination / filename
    archive.write_bytes(package())
    contents = archive.read_bytes()
    with zipfile.ZipFile(io.BytesIO(contents)) as bundle:
        assert bundle.testzip() is None
        assert f"VERSION = '{VERSION}'".encode() in bundle.read('shadow-studio/update_manager.py')
    digest = hashlib.sha256(contents).hexdigest()
    manifest = {'version':VERSION, 'url':BASE+filename, 'sha256':digest}
    (destination/'candidate-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n',encoding='utf-8')
    (destination/(filename+'.sha256')).write_text(f'{digest}  {filename}\n', encoding='ascii')
    with patch('migration_acceptance_test.package', return_value=contents):
        exercise_migration()
    print(f'Prepared approval-only candidate v{VERSION}, SHA-256 {digest}')


if __name__ == '__main__':
    main(Path(sys.argv[1]))
