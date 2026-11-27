"""Fail-closed source-only origin gate for a future protected rollback rebuild.

The normal release controller cannot consume this identity. Deployment, receipt
selection, and provider signature verification are intentionally not implemented
here. `get` must be the authenticated GitHub API adapter; `verify_envelope` must
verify signatures, issuer, subject, and signer constraints independently against
the supplied protected policy, not merely parse a manifest or return a boolean.
Callers must obtain the policy and compatibility record from protected current
code, not dispatch inputs, the candidate tree, or the attestation payload.
"""
from dataclasses import dataclass
from urllib.parse import quote

import github_graph_preview as gh

WORKFLOW = '.github/workflows/assertion-memory-rollback-builder.yml'
SIGNER = '.github/workflows/assertion-memory-rollback-seal.yml'


@dataclass(frozen=True)
class Policy:
    workflow_id: int
    control_sha: str
    tag: str
    revision: str
    release_id: str
    profile: str
    layout: str


@dataclass(frozen=True)
class RebuiltOrigin:
    profile: str
    layout: str
    tag: str
    revision: str
    release_id: str
    build_run_id: str
    control_sha: str
    artifact_id: str
    archive_sha: str
    payload_sha: str
    kind: str = 'rollback_rebuild'


def rebuild_origin(get, verify_envelope, policy: Policy, tag: str, build_run_id: str) -> RebuiltOrigin:
    """Return distinct evidence for a rebuild, never original release identity."""
    if type(policy.workflow_id) is not int or policy.workflow_id <= 0:
        raise gh.Reject('Invalid protected rollback workflow identity')
    build_run_id = gh.exact(gh.ID, build_run_id)
    control_sha = gh.exact(gh.SHA, policy.control_sha)
    release_id = gh.exact(gh.ID, policy.release_id)
    revision = gh.exact(gh.SHA, policy.revision)
    profile = policy.profile
    if tag != policy.tag or (profile, policy.layout) not in (
            ('base', 'base-v1'), ('visual', 'visual-packed-v1')):
        raise gh.Reject('Exact historical version not authorized for this rebuild')

    # Re-resolve the selected published immutable tag and main ancestry now.
    actual_id, product, actual_revision = gh.selection_for_tag(get, tag)
    if (actual_id, product.workflow, actual_revision) != (release_id, f'assertion-memory-{profile}.yml', revision):
        raise gh.Reject('Historical release identity changed or wrong profile')

    run = get(f'{gh.ROOT}/actions/runs/{build_run_id}')
    if (str(run.get('id')) != build_run_id or run.get('workflow_id') != policy.workflow_id
            or run.get('path') != WORKFLOW or run.get('head_branch') != 'main'
            or run.get('head_sha') != control_sha or run.get('event') != 'workflow_dispatch'
            or type(run.get('run_attempt')) is not int or run['run_attempt'] != 1
            or run.get('status') != 'completed' or run.get('conclusion') != 'success'
            or (run.get('repository') or {}).get('id') != gh.REPO_ID
            or (run.get('head_repository') or {}).get('id') != gh.REPO_ID):
        raise gh.Reject('Not the approved current rollback rebuild run')

    name = f'rollback-rebuild-{profile}-{release_id}-{revision}'
    path = f'{gh.ROOT}/actions/runs/{build_run_id}/artifacts?per_page=2&name={quote(name,safe="")}'
    artifact = gh.one_artifact(get(path))
    origin = artifact.get('workflow_run') or {}
    archive_sha = gh.exact('sha256:'+gh.DIGEST, artifact.get('digest',''))[7:]
    artifact_id = gh.exact(gh.ID, str(artifact.get('id','')))
    size = artifact.get('size_in_bytes')
    if (artifact.get('name') != name or artifact.get('expired') is not False
            or type(size) is not int or not 0 < size <= gh.MAX_ARTIFACT
            or str(origin.get('id')) != build_run_id or origin.get('head_sha') != control_sha
            or origin.get('repository_id') != gh.REPO_ID or origin.get('head_repository_id') != gh.REPO_ID):
        raise gh.Reject('Wrong rebuild provider artifact identity')

    # The sealing workflow runs after upload, on a fresh runner. Hence it can
    # attest the actual provider ZIP hash/ID independently of candidate code.
    claim = verify_envelope(artifact_id, archive_sha, gh.REPO_ID, SIGNER, control_sha)
    if type(claim) is not dict:
        raise gh.Reject('Missing verified rebuild attestation')
    payload_sha = gh.exact(gh.DIGEST, claim.get('payload_sha',''))
    expected = dict(schema='rollback-rebuild-v1', profile=profile, layout=policy.layout,
                    tag=tag, revision=revision, release_id=release_id, control_sha=control_sha,
                    build_run_id=build_run_id, build_attempt=1, artifact_id=artifact_id,
                    archive_sha=archive_sha, payload_sha=payload_sha)
    if claim != expected or type(claim.get('build_attempt')) is not int:
        raise gh.Reject('Verified rebuild attestation did not bind exact selected origin')
    return RebuiltOrigin(profile, policy.layout, tag, revision, release_id,
                         build_run_id, control_sha, artifact_id, archive_sha, payload_sha)
