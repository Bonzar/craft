"""Disabled by default: minimum projection over the existing PROD authorizer.

The private root snapshots are deliberately not fields in this protocol.
The production binding must supply a durable exact compare-and-set writer
under existing authorizer authority; no alternate socket or permissions.
"""
from hashlib import sha256
import json
import re

from configured_terminal_state import encode,read_exact

PUBLISH='base-terminal-project-v1'
READ='base-terminal-projection-v1'
HEX=re.compile(r'[0-9a-f]{64}\Z')
RUN=re.compile(r'[1-9][0-9]{0,19}\Z')

def _unique(rows):
    result=dict(rows)
    if len(result)!=len(rows):
        raise ValueError('ambiguous exact terminal projection')
    return result

def _frame(raw):
    if type(raw) is not bytes or not 0<len(raw)<=1024 or not raw.endswith(b'\n'):
        raise ValueError('invalid terminal projection frame')
    result=json.loads(raw,object_pairs_hook=_unique)
    if type(result) is not dict or encode(result)+b'\n'!=raw:
        raise ValueError('noncanonical terminal projection frame')
    return result

def _identity(doc):
    if (type(doc.get('nonce')) is not str or not HEX.fullmatch(doc['nonce'])
            or type(doc.get('run')) is not str or not RUN.fullmatch(doc['run'])):
        raise ValueError('invalid exact projection run')
    return doc['run'],doc['nonce']

def _event(doc):
    if (set(doc)!={'run','nonce','phase','kind','proof'} or doc['phase'] not in ('restart','restore')
            or doc['kind'] not in ('phase','precommit','abort-before-first')
            or doc['kind']=='abort-before-first' and doc['phase']!='restore'
            or type(doc['proof']) is not str or not HEX.fullmatch(doc['proof'])):
        raise ValueError('unexpected terminal projection fields')
    _identity(doc)
    return doc

def _advance(old,doc):
    step=(doc['phase'],doc['kind'])
    if old is None:
        if step not in (('restart','phase'),('restore','abort-before-first')):
            raise ValueError('terminal projection cannot skip native phase')
    else:
        old=_event(old)
        if old==doc:return False
        if _identity(old)!=_identity(doc):
            raise ValueError('terminal projection round mismatch')
        next_steps={
            ('restart','phase'):{('restart','precommit'),('restore','phase')},
            ('restart','precommit'):{('restore','phase')},
            ('restore','phase'):{('restore','precommit')},
            ('restore','abort-before-first'):{('restore','precommit')},
            ('restore','precommit'):set(),
        }
        if step not in next_steps[old['phase'],old['kind']]:
            raise ValueError('nonmonotonic terminal projection')
    return True

def dispatch(raw,*,verified_peer_uid,active,read_admission,read_projection,persist_projection,read_locked_view):
    """Only use kernel-proven peer UID; exact active mode is separately reviewed."""
    if active is not True:
        raise ValueError('terminal projection disabled')
    frame=_frame(raw)
    op=frame.get('op')
    if op==PUBLISH and verified_peer_uid==0:
        doc=_event({k:v for k,v in frame.items() if k!='op'})
        run,nonce=_identity(doc)
        state=read_admission()
        _,pending,_=read_exact(state)
        if pending is None or (pending.run,pending.nonce)!=(run,nonce):
            raise ValueError('no matching signed active admission')
        old=read_projection(run,nonce)
        changed=_advance(old,doc)
        # Both branch paths establish exact durability again; loss of ACK
        # after a durable replacement must not replay or progress an effect.
        persist_projection(expected_admission=state,expected=old,replacement=doc,identical=not changed)
        return {'status':'ok','proof':sha256(encode(doc)).hexdigest()}
    if op==READ and verified_peer_uid==986 and set(frame)=={'op','run','nonce'}:
        run,nonce=_identity(frame)
        state,event=read_locked_view(run,nonce)
        _,pending,receipt=read_exact(state)
        if pending is not None and (pending.run,pending.nonce)==(run,nonce):
            if event is None:return {'status':'waiting'}
            event=_event(event)
            if _identity(event)!=(run,nonce):
                raise ValueError('foreign projection state')
            return {'status':'ok','event':event}
        if pending is None and receipt is not None and (receipt['run'],receipt['nonce'])==(run,nonce):
            return {'status':'settled','phase':receipt['phase'],
                    'proof':sha256(encode(receipt)).hexdigest()}
        raise ValueError('no matching signed terminal projection')
    raise ValueError('unexpected exact authorizer projection operation or peer')
