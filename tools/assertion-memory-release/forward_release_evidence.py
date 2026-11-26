"""Typed bridge from existing receiver's independently verified provider evidence."""
from dataclasses import dataclass

from forward_release_checkpoints import Published
from forward_release_receiver import Request
from forward_release_train import Artifact, FixApproval, Witness, require


@dataclass(frozen=True)
class ProtectedDispatch:
    # This type is only constructed *after* the established root-owned
    # authorizer validates provider run, app/actor, stage and exact artifact.
    request: Request
    published: Published
    release_workflow_id: int
    stage_approved: bool
    test_generation: str | None


def verify_dispatch(protected, incoming, generation, *, workflow_ids):
    require(type(protected) is ProtectedDispatch and type(incoming) is Request
            and protected.request == incoming and type(incoming.artifact) is Artifact,
            'No independently verified deployment authority')
    a = incoming.artifact
    a.checked()
    p = protected.published
    require(type(p) is Published and p.immutable is True and p.candidate == a.candidate
            and p.release_id == a.release_id, 'Provider release is not exact admitted artifact')
    require(type(protected.release_workflow_id) is int and protected.release_workflow_id > 0
            and workflow_ids.get((a.candidate.family,incoming.stage)) == protected.release_workflow_id,
            'Wrong protected workflow')
    if incoming.stage == 'test':
        require(protected.test_generation is None and generation is None,
                'TEST dispatch cannot have a PROD generation binding')
    else:
        require(incoming.stage == 'prod' and protected.stage_approved is True and
                type(generation) is str and protected.test_generation == generation,
                'Existing PROD approval or exact actual TEST generation missing')
    return True


@dataclass(frozen=True)
class ReviewedFailure:
    original: Artifact
    replacement: Artifact
    failing_check_id: int
    review_id: int
    scope_manifest_sha256: str
    independently_verified_failure: bool
    independently_reviewed_defect_only: bool


def fix_approval(reviewed, replacement):
    require(type(reviewed) is ReviewedFailure and reviewed.replacement == replacement
            and reviewed.independently_verified_failure is True
            and reviewed.independently_reviewed_defect_only is True,
            'No exact protected fix-only decision')
    reviewed.original.checked()
    replacement.checked()
    return FixApproval(reviewed.original,replacement.candidate,reviewed.failing_check_id,
                       reviewed.review_id,reviewed.scope_manifest_sha256,'open_major_defect_only')


def fresh_probe(evidence, stage, expected=None):
    require(type(evidence) is Witness and evidence.stage == stage and evidence.origin == 'forward'
            and (expected is None or evidence.artifact == expected),
            'Missing actual service witness for exact forward artifact')
    return evidence
