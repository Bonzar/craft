"""Native guard helper: observations are consumed only inside a single fence."""
from dataclasses import asdict
from hashlib import sha256
from configured_terminal_state import encode
from terminal_lifecycle import TerminalRequest
from terminal_projection import _event,_identity
from terminal_verifier import terminal

def fallback_before_first(request, *, baseline, observed):
    """Root proof that the admitted attempt never changed the incumbent process."""
    if type(request) is not TerminalRequest or request.phase!='restore':
        raise ValueError('exact pre-first restore request required')
    # The existing terminal verifier compares exact journal, release identity,
    # baseline same boot+PID ticks and DB inode. No caller snapshot reaches here
    # in the reviewed root foreground binding.
    terminal(admission=request.admission,predecessor=request.predecessor,phase='restore',
             uid=986,before=observed,after=baseline)
    return {'run':request.admission.run,'nonce':request.admission.nonce,
            'phase':'restore','kind':'abort-before-first',
            'proof':sha256(encode({'original':asdict(baseline),'restoring':asdict(observed)})).hexdigest()}

def use_guard_projection(request, *, enter_existing_fence, read_fresh, verify_physical, effect):
    """Called only as the exact existing native transition fence owner.

    Never pass a prefetched response or acquire another transition fence from
    the authorizer. Root projections and settlement serialize with this same
    fence; the short freshness and effect window must live inside it.
    """
    if type(request) is not TerminalRequest or request.phase not in ('restart','restore'):
        raise ValueError('exact native guard request required')
    with enter_existing_fence():
        doc=read_fresh(request.admission.run,request.admission.nonce)
        if type(doc) is not dict or set(doc)!={'status','event'} or doc['status']!='ok':
            raise ValueError('no fresh actionable projection')
        ev=_event(doc['event'])
        if (_identity(ev)!=(request.admission.run,request.admission.nonce)
                or ev['phase']!=request.phase or ev['kind'] not in ('phase','abort-before-first')):
            raise ValueError('wrong exact active guard projection')
        if verify_physical(request) is not True:
            raise ValueError('native physical release changed')
        if ev['kind']=='abort-before-first':
            # Confirm original listener under normal read-only smoke, but do
            # not replay restart/stop if first authorized effect never ran.
            return 'already-physical-legacy'
        return effect()
