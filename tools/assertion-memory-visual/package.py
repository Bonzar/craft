"""Emit an immutable, deterministic, data-free archive from the actual viewer build."""
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import tarfile

ITEM = re.compile(r'(?:index\.html|assets/(?:graph\.worker-|index-)[A-Za-z0-9_-]+\.(?:js|css))\Z')
LIMIT = 2_400_000


def digest(data):
    return hashlib.sha256(data).hexdigest()


def build(directory, destination):
    paths = list(directory.rglob('*'))
    if any(path.is_symlink() or not (path.is_file() or path == directory/'assets') for path in paths):
        raise ValueError('unexpected viewer tree')
    members = {}
    for path in paths:
        if not path.is_file():
            continue
        name = path.relative_to(directory).as_posix()
        if not ITEM.fullmatch(name) or path.stat().st_size > LIMIT:
            raise ValueError('unexpected path or size')
        members[name] = path.read_bytes()
    if len(members) != 4 or set(Path(k).suffix for k in members) != {'.html','.js','.css'}:
        raise ValueError('incomplete viewer')
    html = members['index.html'].decode('utf8')
    if re.search(r'<base\b', html, re.I):
        raise ValueError('HTML base would unpin assets')
    deps = re.findall(r'<(?:script|link)\b[^>]*?(?:src|href)=(?:"([^"]+)"|\x27([^\x27]+)\x27)', html)
    if len(deps) != 2 or any(not (a or b).startswith('./assets/') or (a or b)[2:] not in members for a,b in deps):
        raise ValueError('HTML dependencies must remain within the selected version')
    main = next(k for k in members if k.startswith('assets/index-') and k.endswith('.js'))
    worker = next(k for k in members if k.startswith('assets/graph.worker-'))
    js = members[main].decode('utf8')
    if '/'+worker in js or Path(worker).name not in js or not re.search(r'(?:import\.meta\.url|\bURL\()',js):
        raise ValueError('Web Worker must remain within the selected version')
    manifests = {name:digest(data) for name,data in sorted(members.items())}
    manifest = (json.dumps(manifests,separators=(',',':'))+'\n').encode()
    members['manifest.json'] = manifest
    with destination.open('xb') as stream,tarfile.open(fileobj=stream,mode='w',format=tarfile.USTAR_FORMAT) as tar:
        for name,data in sorted(members.items()):
            item = tarfile.TarInfo(name)
            item.size = len(data)
            item.mode = 0o640
            tar.addfile(item,io.BytesIO(data))
    if destination.stat().st_size > 5_000_000:
        raise ValueError('archive exceeds receiver cap')
    return {'release_digest':digest(manifest),'archive_sha256':digest(destination.read_bytes()),'archive_bytes':destination.stat().st_size}


if __name__ == '__main__':
    source, destination = map(Path,sys.argv[1:])
    print(json.dumps(build(source,destination),separators=(',',':')))
