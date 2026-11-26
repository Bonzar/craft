"""Fixed settled guard composition, not installed by application releases."""
import json
import os
from pathlib import Path
import socket
import stat

from guard_lookup import REQUEST,mode_for_existing_guard
from root_terminal_publisher import _pid_uid,_credentialed_response
from transition_witness import Admission,Release
from terminal_lifecycle import TerminalRequest
from configured_terminal_state import _release
from projection_client import selected as projection_selected,query_projection
from root_phase_binding import document
from hashlib import sha256

ROOT=Path('/srv/assertion-release/production')
SWITCH=Path('/etc/assertion-release/production/base-browser-listener.json')
SOCKET=Path('/run/assertion-configured-base-prod-auth/production.sock')
UID=986
BASE='assertion-production-deploy-mcp.service'
GRAPH='assertion-production-deploy-graph.service'

def _file(path,limit,owner=None,mode=None):
    fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as f:
        s=os.fstat(f.fileno())
        if (not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_mode&0o022
                or owner is not None and s.st_uid!=owner
                or mode is not None and stat.S_IMODE(s.st_mode)!=mode
                or not os.path.samestat(s,path.lstat())):
            raise ValueError('unverified settled guard source')
        raw=f.read(limit+1)
    if not 0<len(raw)<=limit: raise ValueError('unverified settled source size')
    return raw

def _query():
    parent=SOCKET.parent.lstat(); inode=SOCKET.lstat()
    if (SOCKET.parent.resolve(strict=True)!=SOCKET.parent or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid!=0 or parent.st_mode&0o022 or not stat.S_ISSOCK(inode.st_mode)
            or inode.st_uid!=0 or stat.S_IMODE(inode.st_mode)!=0o660):
        raise ValueError('unverified configured authorizer endpoint')
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as peer:
        peer.settimeout(4)
        peer.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
        peer.connect(str(SOCKET))
        if _pid_uid(peer) not in (0,980) or not os.path.samestat(inode,SOCKET.lstat()):
            raise ValueError('unexpected configured authorizer peer')
        peer.sendall(REQUEST)
        peer.shutdown(socket.SHUT_WR)
        return _credentialed_response(peer,expected_uid=980,limit=4096)

def _mode(proof, *, active_directory, raw_manifest, marker):
    if active_directory.count('-')!=1:
        raise ValueError('wrong active Base archive')
    rev,archive=active_directory.split('-')
    mode='legacy' if marker is None else 'sidecar'
    if marker not in (None,b'assertion-memory-base-browser-listener-v1\n'):
        raise ValueError('invalid signed Base sidecar marker')
    marker_rows=[row for row in raw_manifest.splitlines()
                if row.partition(b'  ')[2] in (b'BASE_BROWSER_LISTENER_CONTRACT',
                                               b'./BASE_BROWSER_LISTENER_CONTRACT')]
    if marker is None and marker_rows:
        raise ValueError('signed marker is missing')
    if marker is not None and marker_rows!=[sha256(marker).hexdigest().encode()+
                                      b'  ./BASE_BROWSER_LISTENER_CONTRACT']:
        raise ValueError('unsealed Base marker')
    return mode_for_existing_guard(proof,
        physical_identity=Release(rev,archive,sha256(raw_manifest).hexdigest(),mode))

def verified_settled_identity():
    if os.getuid()!=UID or os.geteuid()!=UID:
        raise ValueError('wrong existing settled guard identity')
    try: switch=_file(SWITCH,512,owner=0,mode=0o644)
    except FileNotFoundError:
        return None
    exact={'binding':'dedicated-base-browser-v1','stage':'prod','version':1,
           'retired_graph_unit':GRAPH}
    profile=json.loads(switch)
    if profile!=exact or type(profile.get('version')) is not int:
        raise ValueError('unknown protected Base profile')
    current=ROOT/'current'; physical=current.resolve(strict=True)
    if (ROOT.resolve(strict=True)!=ROOT or (ROOT/'releases').is_symlink()
            or physical.parent!=ROOT/'releases' or not current.is_symlink()
            or os.readlink(current)!=str(physical)):
        raise ValueError('unexpected settled Base target')
    proof=_query()
    raw=_file(physical/'SHA256SUMS',5*1024*1024)
    try: marker=_file(physical/'BASE_BROWSER_LISTENER_CONTRACT',128)
    except FileNotFoundError: marker=None
    mode=_mode(proof,active_directory=physical.name,raw_manifest=raw,marker=marker)
    if mode=='sidecar':
        for name in ('prototypes/assertion_memory/http_server.py',
                     'prototypes/assertion_memory/browser_prod_binding.py'):
            item=physical/name
            if item.resolve(strict=True)!=item:
                raise ValueError('wrong sealed Base serving member path')
            content=_file(item,65536)
            rel=name.encode('ascii')
            entries=[row for row in raw.splitlines() if row.partition(b'  ')[2] in (rel,b'./'+rel)]
            if entries!=[sha256(content).hexdigest().encode('ascii')+b'  ./'+rel]:
                raise ValueError('sealed Base serving member mismatch')
    if current.resolve(strict=True)!=physical:
        raise ValueError('settled active Base changed')
    rev,archive=physical.name.split('-')
    return Release(rev,archive,sha256(raw).hexdigest(),mode)

def verified_settled_mode():
    identity=verified_settled_identity()
    return 'legacy' if identity is None else identity.mode

def restart_selected(*, run,userctl,active):
    """Used only by the exact patched settled routine inside its old fence."""
    if projection_selected():
        # This executes INSIDE the original settled guard's exclusive fence.
        # A projected reply cannot authorize an effect outside this function.
        pair=json.loads(_query())
        pending=pair.get('pending')
        if type(pending) is not dict:
            raise ValueError('no signed native projection transaction')
        expected=TerminalRequest(Admission(pending['run'],pending['nonce'],_release(pending['release'])),
                                 _release(pair['known']),pending['phase'])
        status=query_projection(expected.admission.run,expected.admission.nonce)
        event=status.get('event')
        if (status.get('status')!='ok' or type(event) is not dict
            or event['phase']!=expected.phase or event['kind'] not in ('phase','abort-before-first')):
            raise ValueError('no fresh actionable root phase under native fence')
        identity=verified_settled_identity()
        choice=expected.admission.candidate if expected.phase=='restart' else expected.predecessor
        if identity!=choice:
            raise ValueError('protected native physical identity changed')
        if event['kind']=='abort-before-first':
            if identity.mode!='legacy' or expected.phase!='restore':
                raise ValueError('wrong protected native no-op range')
            return None # inherited readback/smoke still runs
        if event['proof']!=sha256(document(expected)).hexdigest():
            raise ValueError('wrong protected native phase seal')
        mode=identity.mode
    else:
        mode=verified_settled_mode()
    if mode=='legacy':
        return run([*userctl(False),'restart',BASE,GRAPH],timeout=18)
    run([*userctl(False),'stop',GRAPH],timeout=18)
    if active(GRAPH):
        raise ValueError('old Graph not fully retired')
    return run([*userctl(False),'restart',BASE],timeout=18)
