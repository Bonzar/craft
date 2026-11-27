"""Integration seam for the protected Base-only terminal controller.

This module does not wire systemd, handle credentials or grant access.
Outer owned controller must bring actual independently verified observers,
same release/fence lease and authorizer-backed commit capability. Nothing
here accepts commands, database paths, ports or receipt bodies from clients.
"""
from dataclasses import dataclass, replace
from typing import Callable

from terminal_verifier import Snapshot, terminal
from transition_witness import Admission, Release

@dataclass(frozen=True)
class TerminalRequest:
    admission: Admission
    predecessor: Release
    phase: str

@dataclass(frozen=True)
class VerifiedPrecommit:
    request: TerminalRequest
    before: Snapshot

class ProtectedTerminalLifecycle:
    def __init__(self, *, service_uid, release_lease: Callable,
                 read_pending: Callable, snapshot: Callable,
                 finish_native_release: Callable, settle_exact_authorizer: Callable,
                 read_precommit: Callable, record_precommit: Callable):
        if (type(service_uid) is not int or service_uid<=0 or
            not all(callable(x) for x in (release_lease,read_pending,snapshot,
                finish_native_release,settle_exact_authorizer,read_precommit,record_precommit))):
            raise ValueError('protected Base terminal authority required')
        self.uid=service_uid
        self._hold=release_lease
        self._pending=read_pending
        self._observe=snapshot
        self._finish=finish_native_release
        self._read_precommit=read_precommit
        self._record=record_precommit
        self._settle=settle_exact_authorizer

    def verify_terminal(self):
        """No arguments: exact transaction/phase derive from trusted state.

        Only a protected durable precommit record can recover a crash after
        the native journal was removed. No repeat of the native terminal
        effect, and no trusting its absence alone.
        """
        with self._hold():
            request=self._pending()
            if type(request) is not TerminalRequest or request.phase not in ('restart','restore'):
                raise ValueError('no verified terminal transaction')
            before=self._observe()
            if before.journal is None:
                pre=self._read_precommit(request)
                if type(pre) is not VerifiedPrecommit or pre.request!=request:
                    raise ValueError('missing exact independent precommit witness')
                return self._commit(request,pre.before,before)
            checked=self._observe()
            if checked.journal!=before.journal:
                raise ValueError('base terminal journal changed during observation')
            terminal(admission=request.admission,predecessor=request.predecessor,
                phase=request.phase,uid=self.uid,before=before,
                after=replace(checked,journal=None))
            self._record(VerifiedPrecommit(request,before))
            self._finish(request)
            return self._commit(request,before,self._observe())

    def _commit(self,request,before,after):
        confirmed=terminal(admission=request.admission,predecessor=request.predecessor,phase=request.phase,
                uid=self.uid,before=before,after=after)
        self._settle(request,confirmed)
        return confirmed
