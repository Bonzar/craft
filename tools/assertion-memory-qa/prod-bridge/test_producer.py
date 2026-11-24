"""Only the pinned reusable job executes this test-producer identity check."""
import base64
import hashlib
from pathlib import Path
import re
from urllib.parse import quote

WORKFLOW = '.github/workflows/assertion-memory-base.yml'
SOURCE = 'tools/assertion-memory-qa'
EXACT = (
    f'{SOURCE}/lifecycle.py', f'{SOURCE}/release-ci.py', f'{SOURCE}/send-release.sh',
    f'{SOURCE}/send_test_release.py', 'tools/assertion-memory-release/ssh_transport.py',
    'tools/assertion-memory-release/receipt_proof.py',
    'tools/assertion-memory-release/native_test_policy.py',
)
PROD_KEY = b'\n  deploy-production:\n'


def contents(get, root, ref, path):
    response=get(f'{root}/contents/{quote(path,safe="/")}?ref={ref}')
    if response.get('encoding')!='base64' or response.get('path')!=path or response.get('type')!='file':
        raise ValueError('Invalid TEST producer provenance')
    return base64.b64decode(''.join(response['content'].split()),validate=True)


def exact_caller(reusable_sha):
    if not re.fullmatch('[0-9a-f]{40}',reusable_sha):
        raise ValueError('Missing immutable reusable workflow SHA')
    return f"""    name: Promote exact TEST archive to PROD
    if: github.event_name == 'workflow_dispatch' && inputs.action == 'production' && inputs.test_run_id != '' && github.repository == 'Bonzar/craft' && github.ref == 'refs/heads/main' && github.run_attempt == '1' && github.ref_protected && vars.ASSERTION_MEMORY_PROD_BRIDGE_CALLER_READY == 'true'
    permissions:
      contents: read
      actions: read
      id-token: write
    uses: Bonzar/craft/.github/workflows/assertion-memory-authorize-promotion.yml@{reusable_sha}
    with:
      release_id: ${{{{ inputs.release_id }}}}
      test_run_id: ${{{{ inputs.test_run_id }}}}
    secrets:
      ASSERTION_MEMORY_DEPLOY_KEY: ${{{{ secrets.ASSERTION_MEMORY_DEPLOY_KEY }}}}
      ASSERTION_MEMORY_SSH_HOST_PIN: ${{{{ secrets.ASSERTION_MEMORY_SSH_HOST_PIN }}}}
""".encode('utf-8')


def verify(get, root, main_sha, reusable_sha, checkout):
    if not re.fullmatch('[0-9a-f]{40}',main_sha):
        raise ValueError('Missing TEST control commit')
    pinned = (checkout/WORKFLOW).read_bytes()
    current = contents(get,root,main_sha,WORKFLOW)
    if pinned.count(PROD_KEY)!=1 or current.count(PROD_KEY)!=1:
        raise ValueError('Unrecognized TEST control workflow layout')
    prelude=pinned.split(PROD_KEY)[0]
    if current != prelude+PROD_KEY+exact_caller(reusable_sha):
        raise ValueError('TEST workflow differs from audited producer')
    for path in EXACT:
        carried=contents(get,root,main_sha,path)
        if hashlib.sha256(carried).digest()!=hashlib.sha256((checkout/path).read_bytes()).digest():
            raise ValueError('TEST helper differs from audited producer')
