"""Generate optional static fallback only from the previously approved Visual artifact."""
import argparse
import base64
import gzip
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import tempfile

from release_core import unpack
from visual_policy import archive_spec, validate


def generate(stream, archive_sha, release_digest):
    if not all(isinstance(v,str) and re.fullmatch('[0-9a-f]{64}',v) for v in (archive_sha,release_digest)):
        raise ValueError('expected original Visual archive and digest')
    with tempfile.TemporaryDirectory() as tmp:
        payload=unpack(stream,Path(tmp),archive_sha,archive_spec())
        if validate(payload)!=release_digest:
            raise ValueError('expected exact approved Visual fallback digest')
        mapping={}
        for name,hash_value in json.loads((payload/'manifest.json').read_bytes()).items():
            raw=(payload/name).read_bytes()
            if sha256(raw).hexdigest()!=hash_value:
                raise ValueError('Visual fallback changed while staging')
            compressed=io.BytesIO()
            with gzip.GzipFile(fileobj=compressed,mode='wb',mtime=0,filename='') as out:
                out.write(raw)
            mapping[name]={'bytes':len(raw),'sha':hash_value,
                           'gz':base64.b64encode(compressed.getvalue()).decode('ascii')}
    data=json.dumps({'digest':release_digest,'files':mapping},ensure_ascii=True,sort_keys=True,separators=(',',':'))
    return ('// Generated from the independently verified Visual release archive.\n'
            'export default Object.freeze('+data+');\n').encode('ascii')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('archive','output'):
        p.add_argument('--'+k,type=Path,required=True)
    p.add_argument('--archive-sha256',required=True)
    p.add_argument('--release-digest',required=True)
    a=p.parse_args()
    if a.archive.is_symlink() or not a.archive.is_file():
        raise ValueError('expected a fixed local Visual release archive')
    with a.archive.open('rb') as stream:
        raw=generate(stream,a.archive_sha256,a.release_digest)
    with a.output.open('xb') as out:
        out.write(raw)


if __name__=='__main__':
    main()
