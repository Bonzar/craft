"""Read-only exact guard binding on the existing authorizer socket.

The only newly recognized fixed deploy-UID frame reads already verified
nonsecret release identities. It cannot authorize, advance or settle.
The owned host guard still verifies active artifact/canonical source and
current from its native transaction before acting.
"""
import json
from configured_terminal_state import read_exact, encode

REQUEST=b'{"op":"base-terminal-pair-v1"}\n'
MAX=4096

def _release(r):
    return {'revision':r.revision,'archive':r.archive,'manifest':r.manifest,'mode':r.mode}

def respond(*, raw_request, verified_peer_uid, read_authorizer_state):
    if verified_peer_uid!=986 or raw_request!=REQUEST:
        raise ValueError('fixed native guard lookup denied')
    last,pending,_=read_exact(read_authorizer_state())
    response={'schema':1,'lane':'prod','known':_release(last),'pending':None}
    if pending is not None:
        response['pending']={'run':pending.run,'nonce':pending.nonce,'release':_release(pending.candidate)}
    return encode(response)+b'\n'

def mode_for_existing_guard(proof, *, physical_identity, pending_required=True):
    if type(proof) is not bytes or not 0<len(proof)<=MAX or not proof.endswith(b'\n'):
        raise ValueError('invalid authorizer pair proof')
    body=json.loads(proof)
    if (type(body) is not dict or set(body)!={'schema','lane','known','pending'}
            or type(body['schema']) is not int or body['schema']!=1 or body['lane']!='prod'
            or encode(body)+b'\n'!=proof):
        raise ValueError('wrong authoritative pair shape')
    expected=_release(physical_identity)
    pending=body['pending']
    if pending is not None:
        if type(pending) is not dict or set(pending)!={'run','nonce','release'}:
            raise ValueError('invalid protected pair transaction')
        possible=(body['known'],pending['release'])
    else:
        if pending_required:
            raise ValueError('no protected active rollback transaction')
        possible=(body['known'],)
    if expected not in possible:
        raise ValueError('running native release does not match exact authorizer proof')
    return physical_identity.mode
