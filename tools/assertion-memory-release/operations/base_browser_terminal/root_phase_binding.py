"""Fixed root-attested native phase record. No receiver-supplied phase."""
from dataclasses import asdict
import os
from pathlib import Path
import stat

from configured_terminal_state import encode,read_exact
from native_boundary import observe_native_boundary
from terminal_lifecycle import TerminalRequest

from root_run_records import record

MAX=2048

def document(request):
    if type(request) is not TerminalRequest or request.phase not in ('restart','restore'):
        raise ValueError('invalid native phase')
    return encode(dict(schema=1,lane='prod',phase=request.phase,
        run=request.admission.run,nonce=request.admission.nonce,
        candidate=asdict(request.admission.candidate),predecessor=asdict(request.predecessor)))

def observe_record(raw,state):
    active,admission,_=read_exact(state)
    if admission is None or type(raw) is not bytes or not 0<len(raw)<=MAX:
        raise ValueError('no admitted native phase')
    possible=[p for p in ('restart','restore') if raw==document(TerminalRequest(admission,active,p))]
    if len(possible)!=1:
        raise ValueError('stale or foreign root native phase')
    return possible[0]

def _safe_parent(path):
    if not path.is_absolute() or path.resolve()!=path:
        raise ValueError('foreign root phase path')
    for parent in [path,*path.parents]:
        s=parent.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid!=0 or s.st_gid!=0 or s.st_mode&0o022:
            raise ValueError('unprotected root phase ancestor')

def _read_root_phase(admission):
    path=record(admission,'phase')
    _safe_parent(path.parent)
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as f:
        s=os.fstat(f.fileno())
        if (not stat.S_ISREG(s.st_mode) or s.st_uid!=0 or s.st_gid!=0 or s.st_nlink!=1
                or stat.S_IMODE(s.st_mode)!=0o644 or not os.path.samestat(s,path.lstat())):
            raise ValueError('not a root-published native phase')
        return f.read(MAX+1)

def read_phase(state):
    _,admission,_=read_exact(state)
    return observe_record(_read_root_phase(admission),state)

def phase_image_from_native(request):
    """Called only by the existing protected controller, after pointer swap.

    Returns the exact bytes to its existing fenced, durable publication.
    The deploy receiver is not privileged to publish this image.
    """
    if os.getuid()!=0 or os.geteuid()!=0:
        raise ValueError('phase publication requires current root controller')
    exact,journal=observe_native_boundary(request)
    if journal is None:
        raise ValueError('missing protected native transition')
    return document(request)
