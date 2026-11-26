"""Qualify an unsigned historical Base candidate in the trusted sealing job.

The read_blob/list_paths adapter must read Git objects for exactly the review-
selected commit, not files from an untrusted working tree. This deliberately
does not execute candidate code or fabricate an ordinary release event. It
does not compile assets, sign, publish or activate anything.
"""
from pathlib import PurePosixPath
import re

import base_payload
import github_graph_preview as gh
from assembly import inspect
from rollback_rebuild_origin import Policy

TREE_ROOTS=('prototypes/assertion_memory','apps/graph_viewer/server')
WEB=PurePosixPath('apps/graph_viewer/web/dist')
REQUIRED_BLOBS=(
    'prototypes/assertion_memory/requirements-mcp.txt',
    'prototypes/assertion_memory/requirements-runtime.txt',
    'tools/assertion-memory-hosted-mcp/policy.py',
    'tools/assertion-memory-hosted-mcp/vm_relay.py',
    'tools/assertion-memory-hosted-mcp/private_host.py',
    'tools/assertion-memory-hosted-mcp/compile_functional_release.py',
    'tools/assertion-memory-hosted-mcp/monium-trace-collector/assertion-monium-traces@.service',
    'tools/assertion-memory-hosted-mcp/monium-trace-collector/common.yaml',
    'tools/assertion-memory-hosted-mcp/monium-trace-collector/test.yaml',
    'tools/assertion-memory-hosted-mcp/monium-trace-collector/prod.yaml',
)
HELPERS={'synthetic_fixture.py':'tools/assertion-memory-qa/synthetic_fixture.py',
         'smoke_local.py':'tools/assertion-memory-qa/smoke_local.py'}
GENERATED={'REVISION','BASE_RELEASE_TAG','QA_PROFILE','mcp-tool-schema.json','requirements-graph-lexical.txt'}


def _source_member(path):
    if not isinstance(path,str) or not re.fullmatch(r'[A-Za-z0-9_./-]+',path):
        raise gh.Reject('Unexpected historical Git path')
    p=PurePosixPath(path)
    if str(p)!=path or p.is_absolute() or '..' in p.parts or not any(
            p.is_relative_to(root) for root in TREE_ROOTS):
        raise gh.Reject('Unexpected historical Git path scope')
    excluded=any(part in ('tests','experiments') for part in p.parts)
    return (not excluded and p.suffix=='.py' and not p.name.startswith('test_')
            and not p.name.endswith('_test.py') and p.name!='offline_test_process.py')


def qualify_base_candidate(path, policy, payload_sha, list_paths, read_blob):
    """Check candidate members against exact Git source, without filesystem writes beyond scratch."""
    if (type(policy) is not Policy or (policy.profile,policy.layout)!=('base','base-v1')
            or gh.product_of_tag(policy.tag).workflow!='assertion-memory-base.yml'):
        raise gh.Reject('Base historical candidate policy required')
    revision=gh.exact(gh.SHA,policy.revision)
    payload_sha=gh.exact(gh.DIGEST,payload_sha)
    paths=list_paths(revision,TREE_ROOTS)
    selected=[name for name in paths if _source_member(name)]
    blobs={name:name for name in (*selected,*REQUIRED_BLOBS)}
    if len(blobs)!=len(selected)+len(REQUIRED_BLOBS):
        raise gh.Reject('Duplicate historical Git member')
    blobs.update(HELPERS)

    def validate(payload):
        key=base_payload.validate(payload,revision,payload_sha)
        manifest={str(name) for name in base_payload.read_manifest(payload)}
        web={name for name in manifest if WEB in PurePosixPath(name).parents and not name.endswith('.map')}
        if manifest!=set(blobs)|GENERATED|web or not web:
            raise gh.Reject('Historical Base archive includes unapproved or missing members')
        if (payload/'BASE_RELEASE_TAG').read_bytes()!=policy.tag.encode('ascii')+b'\n':
            raise gh.Reject('Historical Base archive tag does not match policy')
        for output,source in blobs.items():
            if (payload/output).read_bytes()!=read_blob(revision,source):
                raise gh.Reject('Historical Base bytes do not match the selected Git commit')
        requirements=read_blob(revision,'apps/graph_viewer/requirements.txt')
        expected=b''.join(line for line in requirements.splitlines(keepends=True)
                          if not line.startswith(b'fastembed=='))
        if (payload/'requirements-graph-lexical.txt').read_bytes()!=expected:
            raise gh.Reject('Historical Base derived requirements do not match source')
        return key
    return inspect(path,payload_sha,spec=base_payload.SPEC,validate=validate)
