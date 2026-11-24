"""Reuse canonical GitHub release validators. Never deploy or read secrets."""
import os
from pathlib import Path
import sys
import github_graph_preview as original

CONTROL = 'Bonzar/craft/.github/workflows/configured-release.yml@refs/heads/main'
WORKFLOWS = {'base': 'assertion-memory-base.yml', 'visual': 'assertion-memory-visual.yml'}


def selection(profile, tag):
    product = original.product_of_tag(tag)
    if product.workflow != WORKFLOWS[profile]:
        raise original.Reject('Release tag does not match selected project')
    return product


def run(env=os.environ):
    if (env.get('GITHUB_REPOSITORY') != original.REPO
        or env.get('GITHUB_REF') != original.MAIN
        or env.get('CONTROL_REF_PROTECTED') != 'true'
        or env.get('GITHUB_WORKFLOW_REF') != CONTROL
        or env.get('GITHUB_EVENT_NAME') != 'workflow_dispatch'
        or env.get('GITHUB_RUN_ATTEMPT') != '1'):
        raise original.Reject('Controller not pinned to protected main')
    profile, tag = env['PROFILE'], env['SELECTED_TAG']
    expected = selection(profile, tag)
    token = env['GH_TOKEN']
    evidence = original.from_tag(lambda path: original.api(path, token), tag)
    if original.product_of_tag(evidence['tag']) != expected:
        raise original.Reject('Resolved project changed')
    if original.artifact_digest(evidence['artifact_id'], token) != evidence['archive_sha']:
        raise original.Reject('Original artifact digest changed')
    with Path(env['GITHUB_OUTPUT']).open('a', encoding='ascii') as output:
        for key, value in evidence.items():
            output.write(f'{key}={value}\n')


if __name__ == '__main__':
    try:
        run()
    except (KeyError, ValueError, OSError) as exc:
        raise SystemExit(str(exc) if isinstance(exc, original.Reject) else type(exc).__name__) from None
