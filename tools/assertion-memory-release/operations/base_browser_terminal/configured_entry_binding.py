"""Fixed opt-in entry bindings; legacy wire stays unchanged before rollout."""
from functools import partial
from pathlib import Path
import stat

from configured_base_prod_gate import permit,read_frame,request,serve_peer
from configured_authorizer_binding import serve_exact,terminal_frame
from configured_terminal_state import _release
from authorizer_cas import read_owned_record
from root_phase_binding import read_phase
from transition_witness import Admission

MARKER=Path('/etc/assertion-release/production/base-browser-listener.json')
def active():
    try:
        inode=MARKER.lstat()
    except FileNotFoundError:
        return False
    if (inode.st_uid!=0 or inode.st_gid!=0 or inode.st_nlink!=1
            or not stat.S_ISREG(inode.st_mode) or inode.st_mode&0o022
            or MARKER.resolve()!=MARKER):
        raise ValueError('invalid protected Base rollout marker')
    return True

def authorize(peer,*,policy,ledger,writer_uid,peer_uid,keys,now):
    normal=partial(serve_peer,policy=policy,ledger=ledger,writer_uid=writer_uid,
                   peer_uid=peer_uid,issuer_keys=keys,now=now)
    return serve_exact(peer,normal=normal,protected_marker_active=active,
        independently_observed_phase=lambda:read_phase(read_owned_record()),
        sealed_terminal_dispatch=lambda frame:terminal_frame(frame,policy=policy,ledger=ledger,issuer_keys=keys,now=now()))

def receive_terminal(stream,authorize_frame,activate):
    frame=read_frame(stream)
    op=frame.get('op')
    if op not in ('terminal-challenge-v3','terminal-confirm-v3'):
        raise ValueError('terminal proof required')
    challenge=op=='terminal-challenge-v3'
    expected={'op','intent','candidate'} if challenge else {'op','intent','candidate','nonce','token'}
    if set(frame)!=expected:
        raise ValueError('invalid terminal carrier fields')
    candidate=_release(frame['candidate'])
    normalized={k:v for k,v in frame.items() if k!='candidate'}
    normalized['op']='challenge' if challenge else 'confirm'
    p,_=request(normalized)
    if p.release.revision!=candidate.revision or p.content.sha256!=candidate.archive:
        raise ValueError('invalid sealed candidate carrier')
    if challenge and stream.read(1)!=b'':
        raise ValueError('unexpected terminal challenge payload')
    response=authorize_frame(frame)
    if challenge:
        nonce=response.get('nonce')
        if (set(response)!={'status','nonce'} or response['status']!='ok'
                or not isinstance(nonce,str) or len(nonce)!=64
                or any(c not in '0123456789abcdef' for c in nonce)):
            raise ValueError('terminal challenge denied')
        return response
    if response!=permit(p):
        raise ValueError('terminal authorization denied')
    result=activate(candidate.revision,candidate.archive,stream,
                    admission=Admission(p.run_id,frame['nonce'],candidate))
    if result!=permit(p):
        raise ValueError('wrong exact activation response')
    return result
