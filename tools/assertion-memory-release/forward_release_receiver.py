"""Receiver-side admission for the existing TEST/PROD deploy adapters.

Transport authentication and actual service smoke callbacks remain owned by
the existing receiver. Call this after its transport authentication, before
any switch. No environment input or receipt controls whether to use forward mode.
"""
from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import stat
from contextlib import contextmanager

from forward_release_train import Artifact, Reject, require


@dataclass(frozen=True)
class Request:
    artifact: Artifact
    stage: str
    run_id: int
    receipt: Path


class FamilyLocks:
    """One host-local lock per product, shared after authorized receiver adoption."""
    def __init__(self, root, owner):
        self.root = root
        self.owner = owner

    @contextmanager
    def __call__(self, family):
        require(family in ('base','visual'), 'Unknown receiver family')
        dfd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            ds = os.fstat(dfd)
            require(ds.st_uid == self.owner and not ds.st_mode & 0o022, 'Untrusted lock root')
            fd = os.open(f'{family}.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=dfd)
            try:
                fs = os.fstat(fd)
                require(stat.S_ISREG(fs.st_mode) and fs.st_uid == self.owner and fs.st_nlink == 1
                        and not fs.st_mode & 0o077, 'Untrusted family lock')
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield
            finally:
                os.close(fd)
        finally:
            os.close(dfd)


def activate_forward(checkpoints, req, *, receiver_stage, verify_dispatch, verify_release,
                     active_test, verify_promote_approval, activate_and_probe,
                     live_prod=None, verify_fix=None):
    if (type(req) is not Request or req.stage not in ('test','prod')
            or req.stage != receiver_stage or type(req.artifact) is not Artifact):
        raise Reject('Receiver stage or artifact mismatch')
    require(type(req.run_id) is int and req.run_id > 0, 'Invalid protected deployment run')
    a = req.artifact
    a.checked()
    require(a.candidate.version()[0] > 0, 'Use separate supervised legacy receiver path')

    def authority(generation=None):
        # Existing provider/receiver authority must check exact stage, run,
        # artifact and, before PROD, the actual TEST generation. No new signer.
        return verify_dispatch(req, generation) is True

    def authorize_test(pub):
        # Route through checkpoint lock before proof verification and effects.
        require(authority(), 'Protected TEST workflow proof unavailable')
        return verify_release(pub)

    def read_exact_test(artifact):
        w = active_test(artifact)
        from forward_release_train import Witness
        require(type(w) is Witness and w.artifact == artifact and w.stage == 'test',
                'Active TEST artifact mismatch')
        require(authority(w.generation), 'Protected PROD workflow proof unavailable')
        return w

    if req.stage == 'test':
        return checkpoints.test(a, verified_release=authorize_test, activate_and_probe=activate_and_probe,
                                live_prod=live_prod, verify_fix=verify_fix)
    return checkpoints.production(a, verified_release=verify_release, actual_test=read_exact_test,
                                  approve=verify_promote_approval, activate_and_probe=activate_and_probe)


def protected_activation(checkpoints, req, *, receiver_stage, verify_dispatch,
                         verify_release, identify_prepared, probe, active_test=None,
                         verify_promote_approval=None, live_prod=None, verify_fix=None):
    """Join authenticated receive to release_core/runtime_joint pre-switch hook.

    identify_prepared and probe must be independently configured by the pinned
    installed adapter. Prepared bytes are already verified by existing release
    validation; no metadata in them grants authority. probe reads actual served
    lane identity, not a local enabled flag.
    """
    def authorize(prepared, switch):
        if type(req) is not Request or identify_prepared(prepared) != req.artifact:
            raise Reject('Prepared release differs from authenticated request')

        def activate_and_probe(artifact):
            switch()  # preserve native prepare/restart/smoke/recovery code
            return probe(receiver_stage, artifact)

        return activate_forward(checkpoints,req,receiver_stage=receiver_stage,
                 verify_dispatch=verify_dispatch,verify_release=verify_release,
                 active_test=active_test,verify_promote_approval=verify_promote_approval,
                 activate_and_probe=activate_and_probe,live_prod=live_prod,verify_fix=verify_fix)
    return authorize
