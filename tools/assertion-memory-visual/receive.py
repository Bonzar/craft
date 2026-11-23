"""Only an independently approved visual archive can change its environment pointer."""
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile

ROOT = Path('/var/lib/assertion-memory-visual')
CONFIG = Path('/etc/assertion-memory-visual')
HEX = re.compile(r'[0-9a-f]{64}\Z')
ITEM = re.compile(r'(?:index\.html|assets/(?:graph\.worker-|index-)[A-Za-z0-9_-]+\.(?:js|css))\Z')
LIMIT = 2_400_000
ARCHIVE_LIMIT = 5_000_000


def digest(data):
    return hashlib.sha256(data).hexdigest()


def install(root, approved, archive_bytes):
    if len(archive_bytes) > ARCHIVE_LIMIT or not HEX.fullmatch(approved) or not hmac.compare_digest(digest(archive_bytes), approved):
        raise ValueError('archive is not independently approved')
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode='r:') as archive:
        for item in archive:
            name = item.name
            if (name != 'manifest.json' and not ITEM.fullmatch(name)) or name in files or len(files) >= 5:
                raise ValueError('archive has unexpected member')
            if not item.isfile() or item.size > (1024 if name == 'manifest.json' else LIMIT):
                raise ValueError('invalid member')
            with archive.extractfile(item) as f:
                files[name] = f.read(item.size + 1)
            if len(files[name]) != item.size:
                raise ValueError('incorrect member size')
    manifest = files.pop('manifest.json', b'')
    if len(files) != 4 or 'index.html' not in files:
        raise ValueError('incomplete visual artifact')
    wanted = {name: digest(value) for name, value in sorted(files.items())}
    if manifest != (json.dumps(wanted, separators=(',', ':')) + '\n').encode():
        raise ValueError('manifest mismatch')
    release = digest(manifest)
    releases = root / 'releases'
    releases.mkdir(mode=0o750, exist_ok=True)
    dest = releases / release
    if not dest.exists():
        stage = Path(tempfile.mkdtemp(prefix='.upload-', dir=releases))
        try:
            stage.chmod(0o2750)
            # setgid keeps installs readable by a shared read-only group.
            (stage / 'assets').mkdir(mode=0o2750)
            for name, value in {**files, 'manifest.json': manifest}.items():
                path = stage / name
                with path.open('xb') as file:
                    file.write(value)
                    file.flush()
                    os.fsync(file.fileno())
                path.chmod(0o640)
            stage.rename(dest)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    for name, value in {**files, 'manifest.json': manifest}.items():
        p = dest / name
        if p.is_symlink() or not p.is_file() or p.read_bytes() != value:
            raise ValueError('retained release was modified')
    fd, tmp = tempfile.mkstemp(prefix='.current-', dir=root)
    try:
        with os.fdopen(fd, 'w') as pointer:
            pointer.write(release + '\n')
            pointer.flush()
            os.fchmod(pointer.fileno(), 0o640)
            os.fsync(pointer.fileno())
        os.replace(tmp, root / 'current')
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return release


def main():
    import pwd
    args = sys.argv[1:]
    if len(args) != 1 or args[0] not in ('test', 'prod'):
        raise ValueError('expected provisioned environment')
    mode, = args
    if pwd.getpwuid(os.getuid()).pw_name != f'assertion-visual-{mode}-deploy':
        raise ValueError('wrong receiver account')
    match = re.fullmatch(r'deploy-visual (test|prod) ([0-9a-f]{64})', os.environ.get('SSH_ORIGINAL_COMMAND', ''))
    approval = CONFIG / mode / 'approved-archive-sha256'
    st = approval.stat()
    if (not match or match[1] != mode or approval.is_symlink() or st.st_uid != 0 or st.st_mode & 0o022):
        raise ValueError('missing independent authorization')
    approved = approval.read_text(encoding='ascii').strip()
    if not hmac.compare_digest(match[2], approved):
        raise ValueError('unapproved release')
    result = install(ROOT / mode, approved, sys.stdin.buffer.read(ARCHIVE_LIMIT + 1))
    print(json.dumps({'environment': mode, 'archive_sha256': approved, 'release': result}))

if __name__ == '__main__':
    main()
