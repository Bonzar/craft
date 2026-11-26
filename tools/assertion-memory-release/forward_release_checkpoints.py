"""Receiver-side protected release orchestration; no GitHub credential handling."""
from dataclasses import dataclass
from typing import Callable

from forward_release_train import Artifact, Candidate, FixApproval, Ledger, Reject, Witness, require


@dataclass(frozen=True)
class Published:
    candidate: Candidate
    release_id: int
    # Provider verifier must validate tag peel/main ancestry plus immutable public release.
    immutable: bool


@dataclass(frozen=True)
class SupervisedLegacy:
    # Independently verified narrowly scoped capability, never a workflow input.
    operation: str
    published: Published
    authority_id: str


class Checkpoints:
    """Use one protected, family-scoped mutex shared across ordinary and privileged paths.

    Each callback is from existing protected receiver/control plane, never dispatch
    payload. Disconnection/uncertain outcome requires actual state reconciliation;
    no effect is repeated here.
    """
    def __init__(self, ledger: Ledger, lock: Callable):
        self.ledger = ledger
        self.lock = lock

    def publication(self, candidate: Candidate, *, live_prod, verify_fix, publish):
        with self.lock(candidate.family):
            if candidate.version()[1:] == (0,0):
                fresh = live_prod(candidate.family)
                fix = None
            else:
                fresh = None
                fix = verify_fix(candidate)
                require(type(fix) is FixApproval, 'Protected defect-only review unavailable')
            started = self.ledger.reserve(candidate, fresh, fix=fix)
            require(started, 'Publication may already exist; reconcile exact provider state without repeating')
            p = self._published(publish(candidate), candidate)
            self.ledger.bind_release_id(candidate, p.release_id)
            return p

    @staticmethod
    def _published(p, c):
        require(type(p) is Published and p.candidate == c and p.immutable is True and
                type(p.release_id) is int and p.release_id > 0, 'Exact immutable published release required')
        return p

    def build_start(self, published: Published, *, lookup, prepare):
        c = published.candidate
        with self.lock(c.family):
            p = self._published(lookup(published.release_id), c)
            require(p == published, 'Release provider identity changed')
            self.ledger.require_reserved(c, release_id=p.release_id)
            artifact = prepare(p)
            require(type(artifact) is Artifact and artifact.candidate == c and
                    artifact.release_id == p.release_id, 'Build output does not match reservation')
            self.ledger.bind_artifact(artifact)
            return artifact

    def test(self, artifact: Artifact, *, verified_release, activate_and_probe):
        c = artifact.candidate
        with self.lock(c.family):
            p = self._published(verified_release(artifact.release_id), c)
            require(p.release_id == artifact.release_id, 'Wrong immutable TEST release')
            self.ledger.require_bound(artifact)
            w = activate_and_probe(artifact)
            require(type(w) is Witness and w.artifact == artifact and w.stage == 'test',
                    'TEST service did not prove exact activation')
            self.ledger.witness(w)
            return w

    def production(self, artifact: Artifact, *, verified_release, actual_test, approve, activate_and_probe):
        c = artifact.candidate
        with self.lock(c.family):
            p = self._published(verified_release(artifact.release_id), c)
            require(p.release_id == artifact.release_id, 'Wrong immutable PROD release')
            require(approve(artifact) is True, 'Existing PROD approval not independently verified')
            test = actual_test(artifact)
            require(type(test) is Witness and test.artifact == artifact and test.stage == 'test',
                    'Actual TEST is not the candidate selected for PROD')
            self.ledger.begin_promotion(test)
            w = activate_and_probe(artifact)
            require(type(w) is Witness and w.artifact == artifact and w.stage == 'prod',
                    'PROD service did not prove exact activation')
            self.ledger.witness(w)
            return w

    def supervised_legacy(self, permit: SupervisedLegacy, *, verify, perform_and_probe):
        require(type(permit) is SupervisedLegacy and permit.operation in
                ('finish_exact_legacy', 'urgent_gateway_restore', 'historical_rollback'),
                'Unknown or non-supervised exception')
        c = permit.published.candidate
        with self.lock(c.family):
            p = self._published(permit.published, c)
            require(c.version()[:2] == (0,1) and verify(permit) is True,
                    'Only exact supervised old releases can bypass forward controls')
            # No forward reservation or witness even when PROD smoke succeeds.
            return perform_and_probe(permit)
