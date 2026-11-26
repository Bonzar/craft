"""Verify immutable release metadata and a protected workflow admission witness.

No publisher credentials or deploy rights. A forward version without verified
proof is denied even if a GitHub release event has already fired.
"""
import json
from pathlib import Path
import re

from forward_release_train import Candidate, Reject

REPO = 'Bonzar/craft'
SIGNER_REF = f'https://github.com/{REPO}/.github/workflows/assertion-memory-forward-admission.yml@refs/heads/main'
PREDICATE = f'https://github.com/{REPO}/attestations/forward-release-stage/v1'
STAGES = {'build', 'test', 'prod'}
FIELDS = {'schema','repository_id','authority_revision','family','stage','tag','revision','release_id','workflow_run_id','payload_sha256','test_generation'}


def check(candidate, stage, release_id, workflow_run_id, authority_revision, receipt, *, verify_signed, payload_sha256=None, test_generation=None):
    major, minor, _ = candidate.version()
    if major == 0:
        if minor == 1:
            return 'legacy-controller'
        raise Reject('Unknown legacy release family')
    if (stage not in STAGES or type(release_id) is not int or release_id < 1
            or type(workflow_run_id) is not int or workflow_run_id < 1
            or not isinstance(authority_revision, str) or not re.fullmatch('[0-9a-f]{40}', authority_revision)):
        raise Reject('Forward authorization policy not configured')
    def digest(value):
        return type(value) is str and re.fullmatch('[0-9a-f]{64}',value)
    if ((stage == 'build' and (payload_sha256 is not None or test_generation is not None))
            or (stage == 'test' and (not digest(payload_sha256) or test_generation is not None))
            or (stage == 'prod' and (not digest(payload_sha256) or not digest(test_generation)))):
        raise Reject('Exact stage-specific payload and generation required')
    claim = verify_signed(receipt, authority_revision)
    expected = dict(schema='forward-release-stage/v1', repository_id=1260097465,
                    authority_revision=authority_revision, family=candidate.family, stage=stage,
                    tag=candidate.tag, revision=candidate.revision, release_id=release_id,
                    workflow_run_id=workflow_run_id,payload_sha256=payload_sha256,test_generation=test_generation)
    if (type(claim) is not dict or set(claim) != FIELDS or claim != expected
            or any(type(claim[k]) is not int for k in ('release_id','repository_id','workflow_run_id'))):
        raise Reject('Exact independently verified admission is required')
    return 'forward-admitted'


def verified_github_claim(path, authority_revision, *, execute, authenticated_metadata):
    """Run official verifier through trusted adapter. No alternate predicates/workflows."""
    if not isinstance(path, Path) or not path.is_file() or path.is_symlink() or not 0 < path.stat().st_size <= 16384:
        raise Reject('Missing protected bounded admission subject')
    args = ('gh','attestation','verify',str(path),'--repo',REPO,'--cert-identity',SIGNER_REF,
            '--cert-oidc-issuer','https://token.actions.githubusercontent.com','--signer-digest',authority_revision,
            '--source-digest',authority_revision,'--source-ref','refs/heads/main','--deny-self-hosted-runners',
            '--predicate-type',PREDICATE,'--format','json')
    if not authenticated_metadata(REPO,1260097465):
        raise Reject('Wrong GitHub repository identity')
    result = execute(args)
    if type(result) is not bytes or not 0 < len(result) <= 131072:
        raise Reject('Invalid GitHub cryptographic verifier output')
    native = json.loads(result)
    if not isinstance(native,list) or len(native)!=1:
        raise Reject('Ambiguous registered admission attestation')
    try:
        statement = native[0]['verificationResult']['statement']
        if statement['predicateType'] != PREDICATE:
            raise Reject('Unexpected signed predicate type')
        return statement['predicate']
    except (TypeError, KeyError, IndexError) as exc:
        raise Reject('Invalid verified GitHub attestation structure') from exc


def main(env=None):
    import os
    import subprocess
    import sys
    import github_graph_preview as gh

    env = os.environ if env is None else env
    try:
        family, stage = sys.argv[1:]
        if stage != 'build' or env.get('GITHUB_REPOSITORY') != REPO or env.get('GITHUB_EVENT_NAME') != 'release':
            raise Reject('This entry point is a release build check only')
        tag = env['GITHUB_REF_NAME']
        revision = env['GITHUB_SHA']
        c = Candidate(family, tag, revision)
        if c.version()[:2] == (0,1):
            return  # Existing legacy lifecycle remains in place.
        token = env['GH_TOKEN']
        get = lambda path: gh.api(path, token)
        release_id, product, peeled = gh.selection_for_tag(get, tag)
        if peeled != revision or product.workflow != f'assertion-memory-{family}.yml' or release_id != env['FORWARD_RELEASE_ID']:
            raise Reject('Provider immutable Release/event identity mismatch')
        trusted_path = Path(env['RUNNER_TEMP']).resolve(strict=True)/'forward-release-admission'/'approved-subject.json'

        def run(args):
            out = subprocess.run(args, capture_output=True, timeout=20, check=True).stdout
            return out

        def repository_identity(repo, repo_id):
            # selection_for_tag already scoped all provider metadata to canonical
            # repo; the signed statement also binds the exact numeric ID.
            return repo == REPO and env.get('GITHUB_REPOSITORY_ID') == str(repo_id)

        check(c,stage,int(release_id),int(env['GITHUB_RUN_ID']),env.get('FORWARD_AUTHORITY_SHA'),trusted_path,
              verify_signed=lambda path, rev: verified_github_claim(path,rev,execute=run,authenticated_metadata=repository_identity))
    except (KeyError, ValueError, OSError, subprocess.SubprocessError):
        raise SystemExit('Release build refused: exact protected forward admission is missing or invalid') from None


if __name__ == '__main__':
    main()
