"""Versioned configured-authorizer record; no SQLite or external writes.

Only the already protected authorizer drives this after real v3 admission.
The fixed CAS/write backend must be authorizer_cas, not a deploy-owned
file. No caller-supplied prior/active identity is accepted.
"""
import json
from hashlib import sha256

from root_terminal_publisher import canonical_terminal
from terminal_lifecycle import TerminalRequest
from transition_witness import Admission,Release

def encode(doc):
    return json.dumps(doc,sort_keys=True,separators=(',',':')).encode('ascii')

def _unique(items):
    out=dict(items)
    if len(out)!=len(items):
        raise ValueError('duplicate configured terminal record key')
    return out

def _release(raw):
    if type(raw) is not dict or set(raw)!={'revision','archive','manifest','mode'}:
        raise ValueError('invalid configured terminal release')
    return Release(**raw)

def _dump_release(value):
    return dict(revision=value.revision,archive=value.archive,manifest=value.manifest,mode=value.mode)

def read_exact(raw):
    if type(raw) is not bytes or not 0<len(raw)<=32768:
        raise ValueError('missing protected terminal state')
    state=json.loads(raw,object_pairs_hook=_unique)
    if (type(state) is not dict or set(state)!={'schema','active','pending','receipt'}
       or type(state['schema']) is not int or state['schema']!=1 or encode(state)!=raw):
        raise ValueError('invalid protected terminal state')
    active=_release(state['active'])
    pending=state['pending']
    admission=None
    if pending is not None:
        if type(pending) is not dict or set(pending)!={'run','nonce','candidate'}:
            raise ValueError('invalid exact signed terminal intent')
        admission=Admission(pending['run'],pending['nonce'],_release(pending['candidate']))
    receipt=state['receipt']
    if receipt is not None:
        if (type(receipt) is not dict or set(receipt)!={'schema','run','nonce','lane','phase','release'}
            or type(receipt['schema']) is not int or receipt['schema']!=1 or receipt['lane']!='prod'
            or receipt['phase'] not in ('restart','restore') or _release(receipt['release'])!=active):
            raise ValueError('invalid sealed terminal acknowledgment')
    return active,admission,receipt

def initial(*, trusted_prior):
    # Called only during independent protected bootstrap with non-secret
    # physically verified last installed artifact and prior manifest digest.
    if type(trusted_prior) is not Release:
        raise ValueError('independently verified predecessor required')
    return encode(dict(schema=1,active=_dump_release(trusted_prior),pending=None,receipt=None))

def admit(raw, *, verified_admission):
    if type(verified_admission) is not Admission:
        raise ValueError('existing verified v3 authority required')
    active,pending,receipt=read_exact(raw)
    if (pending is not None or verified_admission.candidate==active
         or receipt is not None and verified_admission.run==receipt['run']):
        raise ValueError('overlap or previously settled terminal admission')
    record=dict(run=verified_admission.run,nonce=verified_admission.nonce,
                candidate=_dump_release(verified_admission.candidate))
    return encode(dict(schema=1,active=_dump_release(active),pending=record,receipt=receipt))

def pending_request(raw, *, verified_root_phase):
    active,admission,_=read_exact(raw)
    if admission is None or verified_root_phase not in ('restart','restore'):
        raise ValueError('no exact useable admitted transaction')
    return TerminalRequest(admission,active,verified_root_phase)

def commit(raw, *, proven_root_request, terminal_receipt):
    if type(proven_root_request) is not TerminalRequest:
        raise ValueError('root terminal publication required')
    req=pending_request(raw,verified_root_phase=proven_root_request.phase)
    if req!=proven_root_request:
        raise ValueError('terminal identity conflicts with admitted state')
    selected=req.admission.candidate if req.phase=='restart' else req.predecessor
    exact=canonical_terminal(req,selected)
    if terminal_receipt!=exact:
        raise ValueError('terminal receipt does not match exact admitted phase')
    return encode(dict(schema=1,active=_dump_release(selected),pending=None,receipt=json.loads(exact)))

def acknowledge_exact(raw, *, expected_receipt):
    _,pending,receipt=read_exact(raw)
    if pending is not None or receipt is None or encode(receipt)!=expected_receipt:
        raise ValueError('existing exact terminal receipt absent')
    return sha256(expected_receipt).hexdigest()
