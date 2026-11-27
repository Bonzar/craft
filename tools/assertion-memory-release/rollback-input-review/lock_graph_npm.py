"""Select exact upstream npm package integrity for Linux x86-64 glibc."""
import base64
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

BLOB = '786ec90f37efebed2ce11355a560a720d2c8f185'


def supported(choices, selector):
    if choices is None:
        return True
    if not isinstance(choices, list) or not choices or not all(type(x) is str for x in choices):
        raise ValueError('Malformed npm platform selector')
    positive = [choice for choice in choices if not choice.startswith('!')]
    return '!'+selector not in choices and (not positive or selector in positive)


def plan(data):
    source = hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()
    if source != BLOB:
        raise ValueError('Wrong historical Git blob')
    obj = json.loads(data)
    if obj.get('lockfileVersion') != 3:
        raise ValueError('Unexpected npm lock format')
    packages = {}
    for path, value in obj['packages'].items():
        if not path or not all(supported(value.get(k),v) for k,v in
                               [('os','linux'),('cpu','x64'),('libc','glibc')]):
            continue
        u = urlsplit(value['resolved'])
        if (u.scheme,u.netloc) != ('https','registry.npmjs.org') or u.fragment or u.query:
            raise ValueError('Noncanonical npm artifact repository')
        integrity = value['integrity']
        if not re.fullmatch(r'sha512-[A-Za-z0-9+/]{86}==',integrity) or len(base64.b64decode(integrity[7:],validate=True)) != 64:
            raise ValueError('Expected upstream canonical SHA512 integrity')
        if not re.fullmatch(r'/(@[a-z0-9_.-]+/)?[a-zA-Z0-9_.-]+/-/[a-zA-Z0-9_.+-]+\.tgz',u.path):
            raise ValueError('Noncanonical npm artifact path')
        key = hashlib.sha256(value['resolved'].encode('ascii')).hexdigest() + '.tgz'
        entry = dict(filename=key, url=value['resolved'], integrity=integrity)
        if key in packages and packages[key] != entry:
            raise ValueError('Conflicting upstream package identities')
        packages[key]=entry
    return dict(git_blob=source,platform='linux/x64/glibc',packages=sorted(packages.values(),key=lambda x:x['url']))


def validate_downloads(inventory, directory):
    paths={p.name:p for p in directory.iterdir()}
    known={x['filename']:x for x in inventory['packages']}
    if paths.keys()!=known.keys():
        raise ValueError('npm tarball download closure differs from reviewed inventory')
    answer={}
    for name,path in paths.items():
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 192*1024**2:
            raise ValueError('Invalid npm tarball')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream,'sha512').digest()!=base64.b64decode(known[name]['integrity'][7:]):
                raise ValueError('npm tarball differs from upstream lock')
            stream.seek(0)
            answer[name]=hashlib.file_digest(stream,'sha256').hexdigest()
    return answer


if __name__=='__main__':
    print(json.dumps(plan(Path(__file__).with_name('graph-package-lock.json').read_bytes()),indent=2))
