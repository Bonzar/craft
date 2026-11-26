"""Fixed root terminal publisher, for the existing configured authorizer only.

An observer running under the already-authorized root controller must perform
actual protected runtime checks; ordinary owner UI is a separate ops gate.
UID 986 never dispatches here.
Nothing in this module reads credentials or controls a service.
"""
from hashlib import sha256
import json
import socket
import struct

from terminal_verifier import terminal
from terminal_lifecycle import TerminalRequest

MAX=8192

def _pid_uid(socket_peer):
    _pid,uid,_gid=struct.unpack('3i',socket_peer.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,
                                                        struct.calcsize('3i')))
    return uid

def _pairs(items):
    result=dict(items)
    if len(items)!=len(result):
        raise ValueError('ambiguous terminal receipt')
    return result

def _release_wire(value):
    return {'revision':value.revision,'archive':value.archive,'manifest':value.manifest,'mode':value.mode}

def canonical_terminal(request,release):
    return json.dumps({'schema':1,'run':request.admission.run,'nonce':request.admission.nonce,
            'lane':'prod','phase':request.phase,'release':_release_wire(release)},
            sort_keys=True,separators=(',',':')).encode('ascii')

def check_root_terminal(*, controller_pending, before, after):
    """Only called where before/after are independently proven, not JSON."""
    if type(controller_pending) is not TerminalRequest:
        raise ValueError('missing exact root terminal transaction')
    release=terminal(admission=controller_pending.admission,
            predecessor=controller_pending.predecessor,phase=controller_pending.phase,
            uid=986,before=before,after=after)
    return canonical_terminal(controller_pending,release)

def verify_and_publish_root_peer(peer, stream, *, read_exact_pending, finish_cas, acknowledge_terminal=None, peer_uid=_pid_uid):
    """Root-only additive dispatch inside old socket; nonce use is exact CAS.

    read_exact_pending yields (exact independently owned record bytes,
    TerminalRequest). finish_cas is closed configured-authorizer persistence
    with the complete prior bytes as the expected value. No writes are made
    simply because the connection came from root.
    """
    if peer_uid(peer)!=0:
        raise ValueError('terminal publication requires protected root peer')
    raw=stream.readline(MAX+1)
    if type(raw) is not bytes or not 0<len(raw)<=MAX or not raw.endswith(b'\n'):
        raise ValueError('invalid terminal frame')
    frame=json.loads(raw,object_pairs_hook=_pairs)
    if type(frame) is not dict or set(frame)!={'op','receipt'} or frame['op']!='base-terminal-v1':
        raise ValueError('unexpected protected operation')
    received=json.dumps(frame['receipt'],sort_keys=True,separators=(',',':')).encode('ascii')
    pending=read_exact_pending()
    if pending is None:
        if acknowledge_terminal is None:
            raise ValueError('missing exact pending or terminal witness')
        expected=sha256(received).hexdigest()
        if acknowledge_terminal(expected_receipt=received)!=expected:
            raise ValueError('idempotent exact terminal acknowledgment unavailable')
        return {'status':'ok','proof':expected}
    record,request=pending
    if (type(record) is not bytes or not record or type(request) is not TerminalRequest
            or request.phase not in ('restart','restore')):
        raise ValueError('no independently admitted pending release')
    release=request.admission.candidate if request.phase=='restart' else request.predecessor
    body=canonical_terminal(request,release)
    if received!=body:
        raise ValueError('receipt does not match exact pending activation')
    finish_cas(expected=record,replacement=body)
    return {'status':'ok','proof':sha256(body).hexdigest()}

AUTHOR_UID=980
DEPLOY_UID=986

def dispatch_existing_authorizer(peer, *, normal, read_exact_pending, finish_cas,
                                  acknowledge_terminal=None, root_projection=None, _uid=_pid_uid):
    """Optional dispatcher for the already configured authorizer socket."""
    import os
    if os.getuid()!=AUTHOR_UID or os.geteuid()!=AUTHOR_UID:
        raise ValueError('wrong configured Base authorizer process')
    who=_uid(peer)
    if who==DEPLOY_UID:
        return normal(peer) # Preserve old challenge+confirm framing and policy.
    if who!=0:
        raise ValueError('unknown protected authorizer peer')
    peer.settimeout(4)
    with peer.makefile('rb') as stream:
        if root_projection is not None:
            from io import BytesIO
            raw=stream.readline(MAX+1)
            response=root_projection(raw)
            if response is not None:
                peer.sendall(json.dumps(response,separators=(',',':')).encode('ascii')+b'\n')
                return response
            stream=BytesIO(raw)
        response=verify_and_publish_root_peer(peer,stream,read_exact_pending=read_exact_pending,
                finish_cas=finish_cas,acknowledge_terminal=acknowledge_terminal,peer_uid=_uid)
    peer.sendall(json.dumps(response,separators=(',',':')).encode('ascii')+b'\n')
    return response

def _credentialed_response(peer, *, expected_uid, limit=512):
    """Verify the process sending bytes, not systemd's listen-socket creator."""
    chunks=bytearray()
    while len(chunks)<=limit:
        raw, ancillary, flags, _ = peer.recvmsg(limit+1-len(chunks),socket.CMSG_SPACE(struct.calcsize('3i')))
        stamps=[struct.unpack('3i',data) for level,kind,data in ancillary
                if level==socket.SOL_SOCKET and kind==socket.SCM_CREDENTIALS
                and len(data)==struct.calcsize('3i')]
        if (not raw or flags & (socket.MSG_CTRUNC|socket.MSG_TRUNC)
                or len(stamps)!=1 or stamps[0][0]<=0 or stamps[0][1]!=expected_uid):
            raise ValueError('unverified configured authorizer response sender')
        chunks.extend(raw)
        if b'\n' in chunks:
            if len(chunks)>limit or chunks.count(b'\n')!=1 or not chunks.endswith(b'\n'):
                break
            return bytes(chunks)
    raise ValueError('invalid configured authorizer response frame')


def publish_with_existing_authorizer(*, controller_pending, before, after):
    """Bind actual witnessed release to existing AF_UNIX over kernel peer UID."""
    import os
    from pathlib import Path
    import stat
    if os.getuid()!=0 or os.geteuid()!=0:
        raise ValueError('not the protected Base terminal collector')
    proof=check_root_terminal(controller_pending=controller_pending,before=before,
                             after=after)
    expected={'status':'ok','proof':sha256(proof).hexdigest()}
    path=Path('/run/assertion-configured-base-prod-auth/production.sock')
    parent=path.parent.lstat()
    sock=path.lstat()
    if (path.parent.resolve(strict=True)!=path.parent or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid!=0 or parent.st_mode&0o022 or not stat.S_ISSOCK(sock.st_mode)
        or sock.st_uid!=0 or stat.S_IMODE(sock.st_mode)!=0o660):
        raise ValueError('untrusted configured authorizer socket')
    frame=b'{"op":"base-terminal-v1","receipt":'+proof+b'}\n'
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as peer:
        peer.settimeout(4)
        peer.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
        peer.connect(str(path))
        if _pid_uid(peer) not in (0,AUTHOR_UID) or not os.path.samestat(sock,path.lstat()):
            raise ValueError('unverified configured authorizer socket peer')
        peer.sendall(frame)
        raw=_credentialed_response(peer,expected_uid=AUTHOR_UID)
        if json.loads(raw,object_pairs_hook=_pairs)!=expected:
            raise ValueError('terminal authorizer publication not confirmed')
    return expected
