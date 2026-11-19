"""Opt-in, safe updater for the locally installed Shadow Studio app.

Updates are sourced only from an operator-configured HTTPS JSON release feed.
An external launcher performs the update while the web server is stopped.
"""
from __future__ import annotations
import hashlib, io, json, os, re, shutil, sys, tempfile, urllib.parse, urllib.request, zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = '0.36.0'
PRESERVE = {'.env', '.venv', 'outputs', 'projects', 'uploads', 'gameplay', 'clips', 'media', 'backups', 'update_backups', 'config.json'}
MANIFEST_LIMIT = 32768
ZIP_LIMIT = 80 * 1024 * 1024

class UpdateError(Exception): pass

def feed_url():
    # Configuring a URL is an owner action, never accept it from a browser request.
    from dotenv import dotenv_values
    return (os.environ.get('SHADOW_UPDATE_FEED') or dotenv_values(ROOT/'.env').get('SHADOW_UPDATE_FEED') or '').strip()

def _https(url):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise UpdateError('Update URLs must use HTTPS without credentials or fragments')
    if parsed.port not in (None,443):
        raise UpdateError('Only standard HTTPS update URLs are allowed')
    if parsed.hostname in ('localhost','127.0.0.1','::1'):
        raise UpdateError('Local update servers are not permitted')
    return parsed

def _fetch(url, limit):
    _https(url)
    req=urllib.request.Request(url,headers={'User-Agent':'ShadowStudioUpdater/'+VERSION})
    try:
        with urllib.request.urlopen(req,timeout=25) as response:
            _https(response.geturl())
            value=response.read(limit+1)
    except Exception as exc:
        raise UpdateError('Unable to retrieve the configured update feed over HTTPS') from exc
    if len(value)>limit:
        raise UpdateError('Update exceeded its permitted size')
    return value

def _version(v):
    if not re.fullmatch(r'\d+\.\d+(?:\.\d+)?',str(v)):
        raise UpdateError('Invalid update version')
    return tuple(int(x) for x in (str(v).split('.')+['0'])[:3])

def check(feed=None):
    feed = feed or feed_url()
    if not feed:
        return {'configured':False,'installed':VERSION,'update_available':False,
                'message':'Set SHADOW_UPDATE_FEED in your local .env once a trusted release feed is hosted.'}
    _https(feed)
    try:
        obj=json.loads(_fetch(feed,MANIFEST_LIMIT))
        version=str(obj['version'])
        sha=str(obj['sha256']).lower()
        url=str(obj['url'])
        if not re.fullmatch('[a-f0-9]{64}',sha): raise UpdateError('Invalid release SHA-256')
        _https(url)
        if urllib.parse.urlsplit(url).hostname != urllib.parse.urlsplit(feed).hostname:
            raise UpdateError('Update ZIP and manifest must use the same trusted hostname')
        available=_version(version)>_version(VERSION)
        return {'configured':True,'installed':VERSION,'latest':version,'update_available':available,
                'message':('Update available. Close Shadow Studio and run update_windows.bat.' if available else 'Already up to date.')}
    except (ValueError,KeyError,TypeError,UpdateError) as exc:
        raise UpdateError('Invalid or unavailable update feed: '+str(exc)) from exc

def apply(feed=None):
    """Update code only; rollback edited files on failure. Run with app STOPPED."""
    feed=feed or feed_url()
    if not feed: raise UpdateError('An HTTPS release feed has not been configured')
    _https(feed)
    obj=json.loads(_fetch(feed,MANIFEST_LIMIT))
    version=str(obj['version']); expected=str(obj['sha256']).lower(); url=str(obj['url'])
    if _version(version)<=_version(VERSION): return 'Already up to date'
    if not re.fullmatch('[a-f0-9]{64}',expected): raise UpdateError('Invalid checksum in update feed')
    if _https(url).hostname != _https(feed).hostname:raise UpdateError('Release host does not match feed host')
    data=_fetch(url,ZIP_LIMIT)
    if hashlib.sha256(data).hexdigest()!=expected: raise UpdateError('Release checksum mismatch. No files changed.')
    with tempfile.TemporaryDirectory(prefix='shadow_update_') as td:
        staging=Path(td)/'new';staging.mkdir()
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            listed=bundle.infolist()
            if not listed or len(listed)>500: raise UpdateError('Invalid release archive')
            for entry in listed:
                if entry.is_dir():continue
                raw=entry.filename.replace('\\','/')
                parts=Path(raw).parts
                if parts and parts[0]=='shadow-studio':parts=parts[1:]
                if not parts or any(p in ('..','.') for p in parts) or Path(*parts).is_absolute():
                    raise UpdateError('Unsafe archive path')
                rel=Path(*parts)
                if rel.parts[0] in PRESERVE or rel.name in PRESERVE or rel.suffix.lower() not in ('.py','.bat','.md','.txt','.html','.css','.js','.jpg','.png','.json'):
                    continue
                if entry.file_size>10*1024*1024:raise UpdateError('Oversized application file')
                # ZIP Unix symlinks must not be followed.
                if (entry.external_attr>>16)&0o170000==0o120000:raise UpdateError('Release contains symlinks')
                target=staging/rel;target.parent.mkdir(parents=True,exist_ok=True)
                with bundle.open(entry) as src, target.open('wb') as dst:shutil.copyfileobj(src,dst)
        if not (staging/'app.py').is_file() or not (staging/'renderer.py').is_file():raise UpdateError('Release is missing app.py or renderer.py')
        touched=[]; backup=ROOT/'update_backups'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup.mkdir(parents=True,exist_ok=False)
        try:
            for source in staging.rglob('*'):
                if not source.is_file():continue
                rel=source.relative_to(staging);dest=ROOT/rel
                if dest.exists():
                    b=backup/rel;b.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(dest,b)
                dest.parent.mkdir(parents=True,exist_ok=True)
                tmp=dest.with_name(dest.name+'.update-tmp')
                shutil.copy2(source,tmp);os.replace(tmp,dest);touched.append(rel)
        except Exception:
            for rel in reversed(touched):
                old=backup/rel
                if old.exists():shutil.copy2(old,ROOT/rel)
                else:(ROOT/rel).unlink(missing_ok=True)
            raise
    return f'Installed {version}. Backup saved in {backup}. Projects, videos and credentials preserved.'

if __name__=='__main__':
    try:
        print(apply() if len(sys.argv)>1 and sys.argv[1]=='install' else json.dumps(check(),indent=2))
    except Exception as error:
        print('Update error:',error,file=sys.stderr)
        sys.exit(1)
