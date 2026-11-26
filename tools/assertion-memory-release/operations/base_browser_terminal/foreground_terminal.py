"""Pure one-run foreground terminal transitions over independently read state."""
from dataclasses import dataclass,replace,asdict
import json
from configured_terminal_state import encode
from root_phase_binding import document
from terminal_verifier import Snapshot,Process
from transition_witness import Release
from root_terminal_publisher import check_root_terminal

@dataclass(frozen=True)
class RootPrecommit:
    request: object
    observation: object

def observe_before_unlink(request, observe):
    first=observe(request)
    again=observe(request)
    if first!=again or first.journal is None:
        raise ValueError('no stable live native precommit')
    # Shape, exact admitted identity, PID, boot, listeners, DB all use the
    # canonical terminal validator. No receipt is published at this stage.
    check_root_terminal(controller_pending=request,before=first,
                        after=replace(again,journal=None))
    return RootPrecommit(request,first)

def observe_after_unlink(request, precommit, observe, fsync_native_directory, publish):
    if type(precommit) is not RootPrecommit or precommit.request!=request:
        raise ValueError('no matching independently durable precommit')
    first=observe(request)
    again=observe(request)
    if first!=again or first.journal is not None:
        raise ValueError('native journal not durably absent')
    fsync_native_directory()
    settled=observe(request)
    if settled!=again:
        raise ValueError('native settled state changed during directory durability check')
    expected=check_root_terminal(controller_pending=request,before=precommit.observation,after=settled)
    # publish is narrowly fixed UID980 CAS and authorizer verifies terminal
    # receipt against exactly independently published phase.
    return publish(controller_pending=request,before=precommit.observation,after=settled),expected

def precommit_wire(value):
    if type(value) is not RootPrecommit:
        raise ValueError('root precommit required')
    return encode({'schema':1,'phase':json.loads(document(value.request)),
                   'snapshot':asdict(value.observation)})

def restore_precommit(raw, request):
    if type(raw) is not bytes or not 0<len(raw)<=16384:
        raise ValueError('invalid exact durable precommit')
    item=json.loads(raw)
    if type(item) is not dict or set(item)!={'schema','phase','snapshot'} or item['schema']!=1:
        raise ValueError('invalid exact durable precommit')
    snap=item['snapshot']
    proc=lambda value: Process(**(value|{'release':Release(**value['release'])}))
    try:
        snapshot=Snapshot(Release(**snap['current']),proc(snap['private']),proc(snap['graph']),
            tuple(tuple(p) for p in snap['listeners']),
            (request.admission.run,request.admission.nonce,request.predecessor,request.admission.candidate),
            snap['boot_id'],tuple(snap['database_identity']))
    except (KeyError,TypeError,ValueError) as exc:
        raise ValueError('wrong durable precommit observation') from exc
    precommit=RootPrecommit(request,snapshot)
    if precommit_wire(precommit)!=raw:
        raise ValueError('wrong durable root sequence or mismatched prior precommit')
    check_root_terminal(controller_pending=request,before=snapshot,after=replace(snapshot,journal=None))
    return precommit

# The activated host binding must put these callbacks on the inherited
# individual transaction, not a new entrypoint accepting a caller request.
def assemble_root_steps(request, *, observe, already_held, durable_precommit,
                        load_precommit, fsync_native_directory, publish):
    if not all(callable(x) for x in (observe,already_held,durable_precommit,
                                    load_precommit,fsync_native_directory,publish)):
        raise ValueError('existing root operator binding required')
    def before_unlink():
        if already_held() is not True:
            raise ValueError('native release/transition fence not held')
        before=observe_before_unlink(request,observe)
        body=precommit_wire(before)
        durable_precommit(body)
        if load_precommit()!=body:
            raise ValueError('durable precommit readback failed')
        return body
    def after_unlink():
        if already_held() is not True:
            raise ValueError('native release/transition fence not held')
        return observe_after_unlink(request,restore_precommit(load_precommit(),request),observe,
                                    fsync_native_directory,publish)
    return before_unlink,after_unlink
