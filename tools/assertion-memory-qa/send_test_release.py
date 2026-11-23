"""Base TEST release proof and receipt only; no local SSH implementation."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'assertion-memory-release'))
import ssh_transport

BASE='Bonzar/craft/.github/workflows/assertion-memory-base.yml@refs/heads/main'


def send(env=os.environ):
    if env['GITHUB_REPOSITORY']!='Bonzar/craft' or env.get('RELEASE_ENABLED')!='true':
        raise ValueError('Incorrect repository or unprovisioned deployment')
    if env.get('GITHUB_WORKFLOW_REF')==BASE:
        if (env['GITHUB_REF'],env['GITHUB_EVENT_NAME'],env['GITHUB_RUN_ATTEMPT'])!=('refs/heads/main','workflow_dispatch','1'):
            raise ValueError('Incorrect TEST dispatch')
        revision=env['RELEASE_REVISION']
    else:
        if (env['GITHUB_REF'],env['GITHUB_EVENT_NAME'])!=('refs/heads/codex/assertion-memory-release','push'):
            raise ValueError('Incorrect legacy dispatch')
        revision=env['GITHUB_SHA']
    digest=env['RELEASE_ARCHIVE_SHA256']
    artifact_id=env['ARTIFACT_ID']
    if (env.get('RELEASE_USER')!='assertion-test-deploy'
            or not re.fullmatch('[a-f0-9]{40}',revision) or not re.fullmatch('[a-f0-9]{64}',digest)
            or not re.fullmatch('[1-9][0-9]{0,19}',artifact_id)):
        raise ValueError('Invalid TEST deployment identity')
    directory=Path(env['RELEASE_DOWNLOAD_DIR'])
    with (directory/'release.tar.gz').open('rb') as payload:
        if hashlib.file_digest(payload,'sha256').hexdigest()!=digest:
            raise ValueError('Incorrect archive digest')
        payload.seek(0)
        with ssh_transport.route(host=env['RELEASE_HOST'],port=env['RELEASE_PORT'],
                  account='assertion-test-deploy',host_pin=env['SSH_HOST_PIN'],
                  key=env['SSH_PRIVATE_KEY'],runtime_dir=env.get('RUNNER_TEMP')) as route:
            if env.get('GITHUB_WORKFLOW_REF')==BASE:
                ssh_transport.preflight(route)
            code,wire=ssh_transport.exchange(route,f'deploy-release test {revision} {digest}',payload=payload,limit=2048)
    expected=dict(status='ok',environment='test',revision=revision,sha256=digest)
    if code!=0 or json.loads(wire)!=expected:
        raise ValueError('Invalid TEST activation receipt')
    (directory/'receiver-receipt.json').write_text(json.dumps(expected,sort_keys=True)+'\n')
    print('Activated and verified: test',revision,digest)
    with Path(env['GITHUB_OUTPUT']).open('a',encoding='ascii') as result:
        result.write(f'revision={revision}\nsha256={digest}\nartifact_id={artifact_id}\n')


if __name__=='__main__':
    try: send()
    except Exception: raise SystemExit('Restricted TEST deployment failed closed') from None
