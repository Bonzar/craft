"""Existing root operator runs this bounded CLI for one already approved run."""
import json
import os
import time

from configured_terminal_state import read_exact
from operator_foreground_phase import ApprovedPair,_read_authorizer,_require_root,publish_one_observation
from operator_foreground_controller import settle_step,recover_settlement_only
from protected_native_activation import _approved
from transition_witness import Admission

def direct(run,*,deadline_seconds=180):
    _require_root()
    if not isinstance(run,str) or not run.isascii() or not run.isdecimal() or not 1<=len(run)<=20:
        raise ValueError('invalid exact foreground run')
    expiry=time.monotonic()+deadline_seconds
    pair=None
    while time.monotonic()<expiry:
        try:
            known,admission,receipt=read_exact(_read_authorizer())
            if pair is None:
                if admission is None or admission.run!=run:
                    raise ValueError('awaiting exact signed run')
                if _approved(admission)!=known:
                    raise ValueError('independent approved prior differs')
                pair=ApprovedPair(admission,known)
            if admission is not None:
                try:publish_one_observation(pair)
                except (ValueError,OSError):pass # Kernel/state check below remains exact.
            try:
                if settle_step(pair)=='settled':
                    return {'status':'settled','run':run,'authority':'existing-root-foreground'}
            except BlockingIOError:
                pass
            except (OSError,ValueError):
                try:
                    if recover_settlement_only(pair)=='settled':
                        return {'status':'settled','run':run,'authority':'existing-root-foreground-recovery'}
                except (OSError,ValueError):pass
        except (OSError,ValueError):
            pass
        time.sleep(.25)
    raise TimeoutError('protected foreground settlement absent')

if __name__=='__main__':
    import sys
    if len(sys.argv)!=2:raise SystemExit(2)
    try:print(json.dumps(direct(sys.argv[1]),sort_keys=True))
    except (OSError,ValueError,TimeoutError):
        print('{"status":"not-settled"}')
        raise SystemExit(1)
