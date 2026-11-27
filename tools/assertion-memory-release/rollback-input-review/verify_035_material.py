"""Verify fixed cold-build inputs and report natural output hashes without signing."""
import hashlib
import json
from pathlib import Path
import stat
import sys
import tarfile

from lock_graph_npm import plan, validate_downloads
from materialize_035_inputs import exact_hashes, HERE

MAX_FILE=192*1024**2


def digest(p):
    st=p.lstat()
    if not stat.S_ISREG(st.st_mode) or not 0 < st.st_size <= MAX_FILE:
        raise ValueError('Unexpected input or output file')
    with p.open('rb') as h:
        return hashlib.file_digest(h,'sha256').hexdigest()


def verify(root):
    wheel_pins,_=exact_hashes()
    wheelhouse=root/'wheelhouse'
    given={p.name:digest(p) for p in wheelhouse.iterdir()}
    if given!=wheel_pins:
        raise ValueError('Missing or changed exact .35 wheelhouse')
    graph_plan=plan((HERE/'graph-package-lock.json').read_bytes())
    return dict(wheels=given,npm=validate_downloads(graph_plan,root/'npm-inputs'))


def outputs(dist, schema):
    if not stat.S_ISDIR(dist.lstat().st_mode):
        raise ValueError('Missing compiled output')
    files={}
    for p in sorted(dist.rglob('*')):
        if stat.S_ISDIR(p.lstat().st_mode):
            continue
        files[p.relative_to(dist).as_posix()]=digest(p)
    if 'index.html' not in files or len(files)>1000:
        raise ValueError('Unexpected browser output')
    return dict(web=files,catalogue=digest(schema))


def source_archive(path):
    prefixes=('apps/graph_viewer','prototypes/assertion_memory')
    if path.stat().st_size>64*1024**2:
        raise ValueError('Historical source tar exceeds bound')
    seen=set()
    with tarfile.open(path,'r:') as t:
        for item in t:
            name=item.name
            if (name in seen or any(x in ('','.','..') for x in name.split('/'))
                    or not (item.isdir() or item.isfile()) or len(seen)>=5000
                    or not (name in ('apps','prototypes') or any(name==p or name.startswith(p+'/') for p in prefixes))):
                raise ValueError('Unexpected historical Git archive member')
            seen.add(name)
    if not set(prefixes)<=seen:
        raise ValueError('Historical source roots not present')


if __name__=='__main__':
    if sys.argv[1]=='--source':
        source_archive(Path(sys.argv[2]))
        raise SystemExit(0)
    material=verify(Path(sys.argv[1]))
    if len(sys.argv)==4:
        print(json.dumps({'source_revision':'710cf557d29cf91e6ab1550d27e525817df96588',
                          'inputs':material,'outputs':outputs(*map(Path,sys.argv[2:]))},
                         sort_keys=True,separators=(',',':')))
