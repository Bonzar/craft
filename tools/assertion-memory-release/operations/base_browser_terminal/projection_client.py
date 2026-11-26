"""Existing configured Base authorizer projection client; no new socket."""
import json
import os
from pathlib import Path
import socket
import stat
from hashlib import sha256

from configured_terminal_state import encode
from root_terminal_publisher import _credentialed_response,_pid_uid,_pairs,AUTHOR_UID
from terminal_projection import READ,PUBLISH,_event,_identity

PROFILE=Path('/etc/assertion-release/configured-base-prod/base-terminal-projection-v1')
SOCKET=Path('/run/assertion-configured-base-prod-auth/production.sock')
SENTINEL=b'assertion-base-terminal-projection-v1\n'

def selected():
    try:
        inode=PROFILE.lstat()
    except FileNotFoundError:
        return False
    if (not stat.S_ISREG(inode.st_mode) or inode.st_uid!=0 or inode.st_gid!=0
            or inode.st_nlink!=1 or stat.S_IMODE(inode.st_mode)!=0o644 or PROFILE.resolve()!=PROFILE):
        raise ValueError('untrusted exact projection selection')
    fd=os.open(PROFILE,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        if not os.path.samestat(inode,os.fstat(f.fileno())) or f.read(128)!=SENTINEL:
            raise ValueError('wrong exact projection selection')
    return True

def _exchange(frame):
    if selected() is not True:
        raise ValueError('protected projection not selected')
    parent=SOCKET.parent.lstat(); sock=SOCKET.lstat()
    if (SOCKET.parent.resolve(strict=True)!=SOCKET.parent or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid!=0 or parent.st_mode&0o022 or not stat.S_ISSOCK(sock.st_mode)
            or sock.st_uid!=0 or stat.S_IMODE(sock.st_mode)!=0o660):
        raise ValueError('untrusted configured authorizer socket')
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as peer:
        peer.settimeout(4)
        peer.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
        peer.connect(str(SOCKET))
        if _pid_uid(peer) not in (0,AUTHOR_UID) or not os.path.samestat(sock,SOCKET.lstat()):
            raise ValueError('unverified configured authorizer socket peer')
        peer.sendall(encode(frame)+b'\n')
        peer.shutdown(socket.SHUT_WR)
        data=_credentialed_response(peer,expected_uid=AUTHOR_UID,limit=1024)
    value=json.loads(data,object_pairs_hook=_pairs)
    if type(value) is not dict:
        raise ValueError('invalid configured projection reply')
    return value

def publish_projection(request,kind,proof):
    if os.getuid()!=0 or os.geteuid()!=0:
        raise ValueError('only existing root observer can send phase evidence')
    event=_event(dict(run=request.admission.run,nonce=request.admission.nonce,
                      phase=request.phase,kind=kind,proof=proof))
    response=_exchange(dict(event,op=PUBLISH))
    if response!={'status':'ok','proof':sha256(encode(event)).hexdigest()}:
        raise ValueError('protected root phase not confirmed')

def query_projection(run,nonce):
    if os.getuid()!=986 or os.geteuid()!=986:
        raise ValueError('only fixed native deploy UID can read projection')
    _identity(dict(run=run,nonce=nonce))
    response=_exchange(dict(op=READ,run=run,nonce=nonce))
    if response.get('status')=='ok':
        if set(response)!={'status','event'} or _identity(_event(response['event']))!=(run,nonce):
            raise ValueError('foreign projected native proof')
    elif response.get('status')=='settled':
        from terminal_projection import HEX
        if (set(response)!={'status','phase','proof'} or response['phase'] not in ('restart','restore')
                or type(response['proof']) is not str or not HEX.fullmatch(response['proof'])):
            raise ValueError('invalid exact native terminal proof')
    elif response!={'status':'waiting'}:
        raise ValueError('exact native projection denied')
    return response
