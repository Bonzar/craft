"""Read the existing Base activation boundary; never mutate it.

The receiver's journal is deploy-owned, so it is only a consistency
observation. File names and current are compared to independently admitted
identities; no authorizer decision is derived from filesystem contents.
"""
from hashlib import sha256
import json
import os
from pathlib import Path
import stat

from release_core import PENDING, checked_release, current_target
from terminal_lifecycle import TerminalRequest

PROD_ROOT=Path('/srv/assertion-release/production')

def _read_regular(path,limit):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as stream:
        st=os.fstat(stream.fileno())
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink!=1
                or not os.path.samestat(st,path.lstat())):
            raise ValueError('unexpected native Base observer file')
        data=stream.read(limit+1)
    if len(data)>limit:
        raise ValueError('oversize native Base observer file')
    return data

def _distinct(entries):
    result=dict(entries)
    if len(result)!=len(entries):
        raise ValueError('ambiguous native Base journal')
    return result

def _name(release):
    return release.revision+'-'+release.archive

def _boundary(root, request):
    if type(request) is not TerminalRequest or request.phase not in ('restart','restore'):
        raise ValueError('protected Base transaction required')
    candidate=request.admission.candidate
    old=request.predecessor
    exact=candidate if request.phase=='restart' else old
    expected=checked_release(root,_name(exact))
    if current_target(root)!=str(expected):
        raise ValueError('wrong physical current')
    manifest=_read_regular(expected/'SHA256SUMS',5*1024*1024)
    if sha256(manifest).hexdigest()!=exact.manifest:
        raise ValueError('manifest does not match protected witness')
    pending=root/PENDING
    try:
        raw=_read_regular(pending,1024)
    except FileNotFoundError:
        try:
            pending.lstat()
        except FileNotFoundError:
            journal=None
        else:
            raise ValueError('native Base journal changed')
    else:
        body=json.loads(raw,object_pairs_hook=_distinct)
        if body!={'old':_name(old),'new':_name(candidate)}:
            raise ValueError('native Base journal does not match protected witness')
        journal=(request.admission.run,request.admission.nonce,old,candidate)
    if current_target(root)!=str(expected):
        raise ValueError('native Base current changed')
    return exact,journal

def observe_native_boundary(request):
    """Fixed PROD scope; call only while holding the existing controller lease."""
    return _boundary(PROD_ROOT,request)
