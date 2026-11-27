"""Acquire exact reviewed public wheels/tarballs on an authorized no-secret runner.

Do not run in the compile namespace. No install, extraction, historical code
or token access. Requires only Python standard library.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler

HERE=Path(__file__).resolve().parent
MAX_FILE=192*1024**2


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Registry redirect forbidden')


def read_url(url, recipient, *, name=None):
    u=urlsplit(url)
    if u.scheme!='https' or u.netloc!=recipient or u.query or u.fragment:
        raise ValueError('Not an approved metadata/package origin')
    if name and u.path.rsplit('/',1)[-1]!=name:
        raise ValueError('Unexpected file URL')
    return build_opener(NoRedirect()).open(url,timeout=30)


def exact_hashes(source=HERE):
    closure=json.loads((source/'base-035-observed-wheel-lock.json').read_bytes())
    npm=json.loads((source/'npm-linux-x64-glibc.json').read_bytes())
    wheels=closure['wheel_sha256']
    if (len(wheels)!=30 or len(npm['packages'])!=31 or any(
            not re.fullmatch(r'[A-Za-z0-9_.-]+\.whl',k) or not re.fullmatch('[a-f0-9]{64}',v)
            for k,v in wheels.items()) or npm['platform']!='linux/x64/glibc'):
        raise ValueError('Unexpected supplied dependency closure')
    return wheels,npm['packages']


def stream_verify(url, host, sink, alg, expected, *, filename):
    p=sink.with_suffix(sink.suffix+'.partial')
    digest=hashlib.new(alg)
    size=0
    try:
        with read_url(url,host,name=filename) as response,p.open('xb') as out:
            while chunk:=response.read(65536):
                size+=len(chunk)
                if size>MAX_FILE:
                    raise ValueError('Oversized public dependency')
                digest.update(chunk)
                out.write(chunk)
        if size==0 or digest.digest()!=expected:
            raise ValueError('Public dependency hash mismatch')
        os.link(p,sink)
    finally:
        p.unlink(missing_ok=True)


def materialize(directory):
    wheel_pins,npm_pins=exact_hashes()
    directory.mkdir(mode=0o700)
    wheels=directory/'wheelhouse'; wheels.mkdir()
    npm=directory/'npm-inputs'; npm.mkdir()
    for name,pin in wheel_pins.items():
        package,version,*_=name.split('-')
        normalized=package.replace('_','-')
        with read_url(f'https://pypi.org/pypi/{normalized}/{version}/json','pypi.org') as m:
            blob=m.read(2*1024**2+1)
        if len(blob)>2*1024**2:
            raise ValueError('Oversized PyPI metadata')
        meta=json.loads(blob)
        match=[x for x in meta['urls'] if x['filename']==name
              and x['packagetype']=='bdist_wheel' and x.get('yanked') is False]
        if len(match)!=1 or match[0]['digests']['sha256']!=pin:
            raise ValueError('Published PyPI checked wheel identity changed')
        stream_verify(match[0]['url'],'files.pythonhosted.org',wheels/name,'sha256',bytes.fromhex(pin),filename=name)
    for entry in npm_pins:
        name=entry['filename']; url=entry['url']; integrity=entry['integrity']
        if (name!=hashlib.sha256(url.encode('ascii')).hexdigest()+'.tgz'
                or not re.fullmatch(r'sha512-[A-Za-z0-9+/]{86}==',integrity)):
            raise ValueError('Malformed exact npm lock control')
        expected=base64.b64decode(integrity.removeprefix('sha512-'),validate=True)
        stream_verify(url,'registry.npmjs.org',npm/name,'sha512',expected,filename=urlsplit(url).path.rsplit('/',1)[-1])


if __name__=='__main__':
    materialize(Path(sys.argv[1]))
