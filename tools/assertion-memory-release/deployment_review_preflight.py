"""Read-only preflight for manual GitHub deployment review by Bonzar.

Supply the exact candidate IDs and payload SHA-256 from reviewed release evidence.
This tool issues only GET requests and prints no credential or approval command.
Pass the operator's existing credential through gh's usual authentication.
"""
import argparse
import hashlib
import io
import json
import subprocess
import zipfile

from github_graph_preview import DIGEST, ID, REPO, REPO_ID, ROOT, Reject, exact, product_of_tag

REVIEWER = 'Bonzar'
PROFILES = {
    'base': ('configured-base-release.yml', 'Base Release'),
    'visual': ('configured-visual-release.yml', 'Visual Release'),
}
TEST_STEPS = (
    'Verify exact artifact and stage approval', 'Activate and smoke exact archive',
    'Preserve TEST evidence', 'Publish same-run TEST proof',
)


def require(condition, diagnostic):
    if not condition:
        raise Reject(diagnostic)


def only(items, predicate, diagnostic):
    matches = [item for item in items if predicate(item)]
    require(len(matches) == 1, diagnostic)
    return matches[0]


def complete(result, key):
    items = result[key]
    require(len(items) == result['total_count'] <= 100, 'Incomplete GitHub listing')
    return items


def gh_get(path):
    try:
        result = subprocess.run(
            ['gh', 'api', '--hostname', 'github.com', '--method', 'GET', path],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        raise Reject('GitHub read failed; verify access to the run and TEST receipt') from None
    return result.stdout


def receipt_from_zip(blob, artifact):
    require(len(blob) == artifact['size_in_bytes'] <= 16384
            and 'sha256:' + hashlib.sha256(blob).hexdigest() == artifact['digest'],
            'Same-run TEST receipt archive digest or size mismatch')
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        name = 'configured-test-proof.json'
        require(archive.namelist() == [name] and archive.getinfo(name).file_size <= 8192,
                'Unexpected same-run TEST receipt archive')
        def unique(pairs):
            require(len(dict(pairs)) == len(pairs), 'Duplicate TEST receipt field')
            return dict(pairs)
        return json.loads(archive.read(name), object_pairs_hook=unique)


def preflight(args, get=gh_get):
    def api(path):
        return json.loads(get(path))

    for value in (args.run, args.test_job, args.environment_id, args.release_id):
        exact(ID, value)
    exact(DIGEST, args.payload_sha256)
    product = product_of_tag(args.tag)
    profile = 'visual' if product.workflow == 'assertion-memory-visual.yml' else 'base'
    workflow, title = PROFILES[profile]
    prod = f'configured-release-{profile}-prod'
    test = f'configured-release-{profile}-test'
    require(args.attempt == 1 and args.environment == prod, 'Unsupported attempt or PROD environment')
    user = api('/user')
    require(user['login'] == REVIEWER and user['type'] == 'User', 'Wrong authenticated GitHub user')
    require(api(ROOT + '/branches/main')['protected'] is True, 'Main branch is not protected')

    root = f'{ROOT}/actions/runs/{args.run}'
    run = api(root)
    path = '.github/workflows/' + workflow
    require(str(run['id']) == args.run and run['run_attempt'] == args.attempt
            and run['path'] == path and run['head_branch'] == 'main'
            and run['workflow_id'] == api(f'{ROOT}/actions/workflows/{workflow}')['id']
            and run['status'] == 'waiting' and run['event'] in ('workflow_run', 'workflow_dispatch')
            and run['display_title'] == f'{title} · {args.tag}'
            and all(run[key]['id'] == REPO_ID for key in ('repository', 'head_repository'))
            and all(run[key]['login'] == REVIEWER and run[key]['type'] == 'User'
                    for key in ('actor', 'triggering_actor')),
            'Wrong configured release run, candidate, state, or actor')
    release = api(f'{ROOT}/releases/{args.release_id}')
    tag = api(f'{ROOT}/git/ref/tags/{args.tag}')
    require(str(release['id']) == args.release_id and release['tag_name'] == args.tag
            and release['immutable'] is True and release['draft'] is False
            and tag['ref'] == 'refs/tags/' + args.tag and tag['object']['type'] == 'commit',
            'Wrong immutable release or tag')
    revision = tag['object']['sha']

    jobs = complete(api(f'{root}/attempts/{args.attempt}/jobs?per_page=100'), 'jobs')
    test_job = only(jobs, lambda job: job['name'] == 'configured / TEST', 'Expected one TEST job')
    prod_job = only(jobs, lambda job: job['name'] == 'configured / PROD', 'Expected one PROD job')
    require(all(str(job['run_id']) == args.run and job['run_attempt'] == args.attempt
                and job['head_sha'] == run['head_sha'] for job in (test_job, prod_job))
            and str(test_job['id']) == args.test_job and test_job['status'] == 'completed'
            and test_job['conclusion'] == 'success' and prod_job['status'] == 'waiting'
            and prod_job['conclusion'] is None and prod_job['steps'] == [], 'Wrong TEST or PROD job state')
    for name in TEST_STEPS:
        step = only(test_job['steps'], lambda step: step['name'] == name, 'Missing unique TEST proof step')
        require(step['status'] == 'completed' and step['conclusion'] == 'success', 'TEST proof step failed')

    name = f'configured-release-test-{args.run}-attempt-{args.attempt}'
    artifact = only(complete(api(root + '/artifacts?per_page=100'), 'artifacts'),
                    lambda item: item['name'] == name, 'Expected one same-run TEST receipt artifact')
    origin = artifact['workflow_run']
    require(artifact['expired'] is False and 0 < artifact['size_in_bytes'] <= 16384
            and str(origin['id']) == args.run and origin['head_sha'] == run['head_sha']
            and origin['repository_id'] == origin['head_repository_id'] == REPO_ID,
            'Wrong TEST receipt artifact provenance')
    aid = exact(ID, str(artifact['id']))
    receipt = receipt_from_zip(get(f'{ROOT}/actions/artifacts/{aid}/zip'), artifact)
    expected = {
        'schema': 1, 'profile': profile, 'workflow_ref': f'{REPO}/{path}@refs/heads/main',
        'control_sha': run['head_sha'], 'run_id': args.run, 'run_attempt': str(args.attempt),
        'trigger': run['event'], 'environment': test, 'tag': args.tag, 'revision': revision,
        'release_id': args.release_id, 'payload_sha256': args.payload_sha256,
    }
    require(all(receipt.get(key) == value for key, value in expected.items()),
            'TEST receipt does not bind the exact candidate and payload')

    env = api(f'{ROOT}/environments/{prod}')
    rule = only(env['protection_rules'], lambda rule: rule['type'] == 'required_reviewers',
                'Missing unique PROD reviewer rule')
    require(str(env['id']) == args.environment_id and env['name'] == prod
            and env['can_admins_bypass'] is False and rule['prevent_self_review'] is False,
            'Unexpected PROD review policy')
    pending = only(api(root + '/pending_deployments'),
                   lambda item: item['environment']['name'] == prod, 'PROD is not pending review')
    require(str(pending['environment']['id']) == args.environment_id
            and pending['current_user_can_approve'] is True
            and all([(entry['type'], entry['reviewer']['login']) for entry in obj['reviewers']]
                    == [('User', REVIEWER)] for obj in (rule, pending)),
            'Authenticated user is not the exact required reviewer')
    return f'{args.tag} · run {args.run} attempt {args.attempt} · {prod} ({args.environment_id})'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('tag', 'run', 'environment', 'environment-id', 'release-id', 'test-job', 'payload-sha256'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--attempt', type=int, required=True)
    try:
        result = preflight(parser.parse_args())
    except Reject as err:
        parser.exit(1, f'Preflight stopped: {err}\n')
    except (KeyError, TypeError, AttributeError, ValueError, OSError, RuntimeError, zipfile.BadZipFile):
        parser.exit(1, 'Preflight stopped: invalid GitHub metadata or TEST receipt\n')
    print(f'Preflight passed: {result}\nNo review submitted. Review write access was not tested.')


if __name__ == '__main__':
    main()
