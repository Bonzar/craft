"""Native checkpoint waiting for exact root phase, without taking its fence."""
from pathlib import Path
import os
import time

from native_boundary import PROD_ROOT,observe_native_boundary
from root_phase_binding import _read_root_phase,document
from terminal_lifecycle import TerminalRequest
from transition_witness import Admission,Release

def _before_effect(expected, *, root, phase, old, candidate, root_receipt, native_observer,
                   clock, sleep, timeout):
    request=TerminalRequest(expected.admission,expected.predecessor,phase)
    if (root!=PROD_ROOT or type(expected.admission) is not Admission
            or type(expected.predecessor) is not Release
            or old!=root/'releases'/(request.predecessor.revision+'-'+request.predecessor.archive)
            or candidate!=root/'releases'/(request.admission.candidate.revision+'-'+request.admission.candidate.archive)):
        raise ValueError('foreign native phase checkpoint')
    permitted=document(request)
    forward=document(TerminalRequest(expected.admission,expected.predecessor,'restart'))
    deadline=clock()+timeout
    while clock()<deadline:
        try: receipt=root_receipt()
        except FileNotFoundError: receipt=None
        if receipt==permitted:
            selected,journal=native_observer(request)
            chosen=expected.admission.candidate if phase=='restart' else expected.predecessor
            if (selected!=chosen or journal!=(expected.admission.run,expected.admission.nonce,
                    expected.predecessor,expected.admission.candidate) or root_receipt()!=permitted):
                raise ValueError('native physical phase changed')
            return
        if receipt is not None and (phase!='restore' or receipt!=forward):
            raise ValueError('wrong root phase receipt')
        sleep(.25)
    raise TimeoutError('protected native phase not yet authorized')

def checkpoint_for(expected):
    if type(expected) is not TerminalRequest or os.getuid()!=986 or os.geteuid()!=986:
        raise ValueError('canonical native receiver binding required')
    return lambda root,phase,old,candidate:_before_effect(expected,root=root,phase=phase,old=old,
        candidate=candidate,root_receipt=_read_root_phase,native_observer=observe_native_boundary,
        clock=time.monotonic,sleep=time.sleep,timeout=15)
