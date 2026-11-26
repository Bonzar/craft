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
from forward_release_workflow import check


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


def activate_forward(checkpoints, req, *, receiver_stage, verify_seal, verify_release,
                     active_test, verify_promote_approval, activate_and_probe, authority_revision):
    if (type(req) is not Request or req.stage not in ('test','prod')
            or req.stage != receiver_stage or type(req.artifact) is not Artifact):
        raise Reject('Receiver stage or artifact mismatch')
    a = req.artifact
    a.checked()
    require(a.candidate.version()[0] > 0, 'Use separate supervised legacy receiver path')

    def authority(generation=None):
        return check(a.candidate,req.stage,a.release_id,req.run_id,authority_revision,req.receipt,
                     verify_signed=verify_seal,payload_sha256=a.digest,test_generation=generation) == 'forward-admitted'

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
        return checkpoints.test(a, verified_release=authorize_test, activate_and_probe=activate_and_probe)
    return checkpoints.production(a, verified_release=verify_release, actual_test=read_exact_test,
                                  approve=verify_promote_approval, activate_and_probe=activate_and_probe)
