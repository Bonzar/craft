"""Read-only CI shadow: use the real release API but never environment or receiver."""
import argparse
import json
import os
from pathlib import Path

import configured_release as preflight
import github_graph_preview as original


def run(args, env=os.environ):
    if (env.get('GITHUB_EVENT_NAME') != 'pull_request' or env.get('GITHUB_REPOSITORY') != original.REPO
            or env.get('GITHUB_BASE_REF') != 'main' or env.get('GITHUB_RUN_ATTEMPT') != '1'):
        raise original.Reject('Unexpected read-only shadow runner')
    run_id = original.exact(original.ID, env['GITHUB_RUN_ID'])
    sha = original.exact(original.SHA, env['GITHUB_SHA'])
    expected = preflight.selection(args.profile, args.tag)
    token = env['GH_TOKEN']
    data = original.from_tag(lambda path: original.api(path, token), args.tag)
    if original.product_of_tag(data['tag']) != expected:
        raise original.Reject('Resolved project differs')
    if original.artifact_digest(data['artifact_id'], token) != data['archive_sha']:
        raise original.Reject('Archive hash differs')
    evidence = json.dumps(dict(data, profile=args.profile, run_id=run_id, control_sha=sha, attempt=1),
                          sort_keys=True, separators=(',', ':'))
    if args.stage != 'build' and env.get('BUILD_PROOF') != evidence:
        raise original.Reject('Current artifact differs from exact Build')
    if args.stage == 'prod' and env.get('TEST_PROOF') != evidence:
        raise original.Reject('Current artifact differs from exact TEST')
    with Path(env['GITHUB_OUTPUT']).open('a', encoding='ascii') as output:
        output.write(f'proof={evidence}\n')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('stage', choices=['build', 'test', 'prod'])
    p.add_argument('profile', choices=['base', 'visual'])
    p.add_argument('tag')
    try:
        run(p.parse_args())
    except (KeyError, ValueError, OSError) as exc:
        raise SystemExit(str(exc) if isinstance(exc, original.Reject) else type(exc).__name__) from None
