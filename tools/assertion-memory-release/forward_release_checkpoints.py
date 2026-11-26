"""Receiver-side protected release orchestration; no GitHub credential handling."""
from dataclasses import dataclass
from typing import Callable

from forward_release_train import Artifact, Candidate, Ledger, Reject, Witness, require


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
    """Use one protected, family-scoped deployment mutex across ordinary and privileged paths.

    Each callback is from existing protected receiver/control plane, never dispatch
    payload. Disconnection/uncertain outcome requires actual state reconciliation;
    no effect is repeated here.
    """
    def __init__(self, ledger: Ledger, lock: Callable):
        self.ledger = ledger
        self.lock = lock

    @staticmethod
    def _published(p, c):
        require(type(p) is Published and p.candidate == c and p.immutable is True and
                type(p.release_id) is int and p.release_id > 0, 'Exact immutable published release required')
        return p

    def test(self, artifact: Artifact, *, verified_release, activate_and_probe, live_prod, verify_fix):
        c = artifact.candidate
        with self.lock(c.family):
            p = self._published(verified_release(artifact.release_id), c)
            require(p.release_id == artifact.release_id, 'Wrong immutable TEST release')
            if not self.ledger.is_reserved(c):
                _, minor, _ = c.version()
                live = live_prod(c.family) if minor == 0 else None
                fix = verify_fix(artifact) if minor != 0 else None
                self.ledger.reserve(c, live, fix=fix)
            self.ledger.bind_release_id(c, p.release_id)
            self.ledger.bind_artifact(artifact)
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
