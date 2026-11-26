"""Existing root operator runs this bounded CLI for one already approved run."""
import json
import os
import time

from configured_terminal_state import read_exact
from operator_foreground_phase import ApprovedPair,_read_authorizer,_require_root,publish_one_observation
from operator_foreground_controller import settle_step,recover_settlement_only
from protected_native_activation import _approved,_approved_record
from projection_client import selected as projection_selected
from kernel_observer import observe
from ops_readonly_preflight import trusted_pid
from terminal_lifecycle import TerminalRequest
from transition_witness import Admission

def direct(run,*,deadline_seconds=180):
    _require_root()
    if not isinstance(run,str) or not run.isascii() or not run.isdecimal() or not 1<=len(run)<=20:
        raise ValueError('invalid exact foreground run')
    expiry=time.monotonic()+deadline_seconds
    pair=None
    baseline=None
    if projection_selected():
        # Read-only physical baseline before a pending transaction. Synthetic
        # label cannot grant authority through either socket or authorizer.
        candidate,prior=_approved_record(run)
        probe=TerminalRequest(Admission(run,'0'*64,candidate),prior,'restore')
        try:
            sample=observe(probe,trusted_pid=trusted_pid)
            if sample.journal is None:
                baseline=sample
        except (OSError,ValueError):
            pass # no pre-first skip allowed without the exact original witness
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
                try:publish_one_observation(pair,before_first=baseline)
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
