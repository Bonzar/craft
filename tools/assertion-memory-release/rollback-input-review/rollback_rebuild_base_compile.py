"""Current trusted Base-v1 assembler for explicit historical rebuilds.

Historical compilation itself must take place in an isolated no-secret runner.
This assembler never executes historical files. Its Git adapters must address
authenticated objects by the approved full revision.
"""
import gzip
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile

import github_graph_preview as gh
from rollback_rebuild_base_input import (
    HELPERS, REQUIRED_BLOBS, TREE_ROOTS, _source_member, qualify_base_candidate, optional_historical_marker,
)
from rollback_rebuild_origin import Policy

MAX_FILE = 32 * 1024**2
MAX_TOTAL = 300 * 1024**2


def _input(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0<info.st_size<=MAX_FILE:
            raise gh.Reject('Invalid generated Base member')
        out=stream.read(MAX_FILE+1)
        if len(out)!=info.st_size:
            raise gh.Reject('Generated Base member changed')
        return out


def assemble(policy, dist, schema, output, list_paths, read_blob):
    """Build and qualify one fresh byte package at an unused scratch output path."""
    if (type(policy) is not Policy or (policy.profile,policy.layout)!=('base','base-v1')
            or gh.product_of_tag(policy.tag).workflow!='assertion-memory-base.yml'):
        raise gh.Reject('Historical Base policy required')
    revision=gh.exact(gh.SHA,policy.revision)
    root=Path(dist)
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise gh.Reject('Generated Base web root must be a real directory')
    members={}
    size=0

    def add(name, blob):
        nonlocal size
        if type(blob) is not bytes or len(blob)>MAX_FILE or name in members or len(members)>=19_999:
            raise gh.Reject('Invalid or ambiguous Base archive member')
        size+=len(blob)
        if size>MAX_TOTAL:
            raise gh.Reject('Historical Base archive input too large')
        members[name]=blob

    for name in (*[p for p in list_paths(revision,TREE_ROOTS) if _source_member(p)],*REQUIRED_BLOBS):
        add(name,read_blob(revision,name))
    for name,source in HELPERS.items():
        add(name,read_blob(revision,source))
    for name,raw in optional_historical_marker(policy,read_blob).items():
        add(name,raw)
    requirements=read_blob(revision,'apps/graph_viewer/requirements.txt')
    add('requirements-graph-lexical.txt',b''.join(line for line in requirements.splitlines(keepends=True)
                                                  if not line.startswith(b'fastembed==')))
    for path in root.rglob('*'):
        info=path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise gh.Reject('Generated web output contains a nonregular member')
        relative=path.relative_to(root).as_posix()
        if relative.endswith('.map'):
            raise gh.Reject('Generated web output includes a source map')
        if any(part.startswith('.') or not part.isascii() for part in PurePosixPath(relative).parts):
            raise gh.Reject('Invalid generated web path')
        add('apps/graph_viewer/web/dist/'+relative,_input(path))
    add('mcp-tool-schema.json',_input(schema))
    add('REVISION',revision.encode('ascii')+b'\n')
    add('BASE_RELEASE_TAG',policy.tag.encode('ascii')+b'\n')
    add('QA_PROFILE',b'assertion-memory-synthetic-qa-v1\n')
    sums=b''.join(hashlib.sha256(raw).hexdigest().encode()+b'  '+name.encode('ascii')+b'\n'
                  for name,raw in sorted(members.items()))
    add('SHA256SUMS',sums)
    path=Path(output)
    with path.open('xb') as sink:
        try:
            with gzip.GzipFile(filename='',mode='wb',fileobj=sink,mtime=0) as gz:
                with tarfile.open(fileobj=gz,mode='w|',format=tarfile.USTAR_FORMAT) as archive:
                    for name,raw in sorted(members.items()):
                        info=tarfile.TarInfo(name)
                        info.mode,info.size=0o644,len(raw)
                        archive.addfile(info,io.BytesIO(raw))
            sink.flush()
            with path.open('rb') as stream:
                digest=hashlib.file_digest(stream,'sha256').hexdigest()
            qualify_base_candidate(path,policy,digest,list_paths,read_blob)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    return digest
