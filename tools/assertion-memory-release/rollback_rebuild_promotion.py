"""Offline contract for promoting one freshly tested rebuilt archive.

This does not authorize transfer: a protected controller still has to check
stage/environment approval, trusted code and service-specific signed audiences.
`get` and `download` are trusted authenticated provider adapters. The download
adapter must cap its response at the supplied byte cap while streaming.
"""
import hashlib
import io
import json
import zipfile
from dataclasses import asdict, dataclass

import github_graph_preview as gh
from rollback_rebuild_origin import RebuiltOrigin

WORKFLOW = '.github/workflows/assertion-memory-rollback-rebuild-release.yml'
TEST_JOB = 'Rollback / TEST'
STEPS = ('Verify rebuilt archive and stage approval', 'Activate and smoke exact rebuilt archive',
         'Preserve rebuild TEST evidence', 'Publish same-run TEST output')
MEMBER = 'rollback-rebuild-test-proof.json'
ZIP_LIMIT, JSON_LIMIT = 16_384, 8_192


@dataclass(frozen=True)
class DeployControl:
    """Pinned control/runner data, not dispatch input or arbitrary job output."""
    workflow_id: int
    control_sha: str
    run_id: str
    stage: str
    test_environment: str


@dataclass(frozen=True)
class RebuiltPromotion:
    origin: RebuiltOrigin
    run_id: str
    test_receipt_id: str
    control_sha: str
    kind: str = 'rollback_rebuild_promotion'


def _proof(control: DeployControl, origin: RebuiltOrigin):
    if (type(control) is not DeployControl or type(origin) is not RebuiltOrigin
            or control.stage not in ('test','prod') or type(control.workflow_id) is not int
            or control.workflow_id <= 0 or control.run_id == origin.build_run_id
            or (origin.profile, control.test_environment) not in (
                ('base', 'configured-release-base-test'), ('visual', 'configured-release-visual-test'))
            or origin.kind != 'rollback_rebuild'):
        raise gh.Reject('Invalid protected rebuild deployment control')
    gh.exact(gh.ID,control.run_id)
    gh.exact(gh.SHA,control.control_sha)
    return dict(schema='rollback-rebuild-test-v1', run_id=control.run_id, attempt=1,
                workflow=WORKFLOW, control_sha=control.control_sha,
                environment=control.test_environment, origin=asdict(origin))


def seal_test(control: DeployControl, origin: RebuiltOrigin, verified_test_receipt: dict) -> dict:
    """Call only after trusted receiver activation and independent service smoke."""
    proof=_proof(control,origin)
    expected=dict(stage='test', profile=origin.profile, release_id=origin.release_id,
                  tag=origin.tag, revision=origin.revision, archive_sha=origin.archive_sha,
                  payload_sha=origin.payload_sha, activation='verified', smoke='verified')
    if control.stage != 'test' or verified_test_receipt != expected:
        raise gh.Reject('Exact successful rebuilt TEST activation/smoke required')
    return proof


def _unique_object(text):
    def pairs(items):
        obj={}
        for key,value in items:
            if key in obj:
                raise gh.Reject('Duplicate keys in rebuild TEST proof')
            obj[key]=value
        return obj
    if not isinstance(text,(str,bytes)) or not len(text) <= JSON_LIMIT:
        raise gh.Reject('Oversize or wrong rebuild TEST proof')
    try:
        return json.loads(text,object_pairs_hook=pairs)
    except (UnicodeDecodeError,json.JSONDecodeError):
        raise gh.Reject('Malformed rebuild TEST proof') from None


def _canonical(obj):
    return json.dumps(obj,ensure_ascii=True,separators=(',',':'),sort_keys=True)


def promote(get, download, control: DeployControl, origin: RebuiltOrigin, job_output) -> RebuiltPromotion:
    """Require actual same-run GitHub TEST evidence, not the output alone."""
    expected=_proof(control,origin)
    if control.stage != 'prod':
        raise gh.Reject('Protected rebuilt PROD stage required')
    root=f'{gh.ROOT}/actions/runs/{control.run_id}'
    run=get(root)
    repo=lambda key: (run.get(key) or {}).get('id')
    if (str(run.get('id')) != control.run_id or run.get('workflow_id') != control.workflow_id
            or run.get('path') != WORKFLOW or run.get('head_sha') != control.control_sha
            or run.get('head_branch') != 'main' or run.get('event') != 'workflow_dispatch'
            or type(run.get('run_attempt')) is not int or run['run_attempt'] != 1
            or run.get('status') != 'in_progress' or repo('repository') != gh.REPO_ID
            or repo('head_repository') != gh.REPO_ID):
        raise gh.Reject('Wrong active protected rebuild deployment run')
    listing=get(root+'/attempts/1/jobs?per_page=100')
    jobs=listing.get('jobs',[])
    selected=[job for job in jobs if job.get('name')==TEST_JOB]
    if (listing.get('total_count') != len(jobs) or len(jobs)>100 or len(selected)!=1):
        raise gh.Reject('Missing or ambiguous rebuild TEST job')
    job=selected[0]
    if (str(job.get('run_id')) != control.run_id or job.get('head_sha') != control.control_sha
            or job.get('status') != 'completed' or job.get('conclusion') != 'success'):
        raise gh.Reject('Rebuilt TEST job not proven green')
    for name in STEPS:
        found=[step for step in job.get('steps',[]) if step.get('name')==name]
        if (len(found)!=1 or found[0].get('status')!='completed' or found[0].get('conclusion')!='success'):
            raise gh.Reject('Required rebuild TEST step not proven green')
    name=f'rollback-rebuild-test-{control.run_id}-attempt-1'
    artifact=gh.one_artifact(get(root+'/artifacts?per_page=2&name='+name))
    size=artifact.get('size_in_bytes')
    provider=artifact.get('workflow_run') or {}
    receipt_id=gh.exact(gh.ID,str(artifact.get('id','')))
    digest=gh.exact('sha256:'+gh.DIGEST,artifact.get('digest',''))[7:]
    if (artifact.get('expired') is not False or artifact.get('name')!=name
            or type(size) is not int or not 0<size<=ZIP_LIMIT
            or str(provider.get('id'))!=control.run_id or provider.get('head_sha')!=control.control_sha
            or provider.get('repository_id')!=gh.REPO_ID or provider.get('head_repository_id')!=gh.REPO_ID):
        raise gh.Reject('Wrong rebuilt TEST proof provenance')
    raw=download(receipt_id,ZIP_LIMIT)
    if type(raw) is not bytes or len(raw)!=size or hashlib.sha256(raw).hexdigest()!=digest:
        raise gh.Reject('Wrong rebuilt TEST proof ZIP bytes')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if z.namelist()!=[MEMBER] or z.getinfo(MEMBER).file_size>JSON_LIMIT:
                raise gh.Reject('Wrong rebuilt TEST proof ZIP layout')
            with z.open(MEMBER) as entry:
                receipt=_unique_object(entry.read(JSON_LIMIT+1))
    except (zipfile.BadZipFile,RuntimeError,NotImplementedError):
        raise gh.Reject('Invalid rebuilt TEST proof ZIP') from None
    if not _canonical(_unique_object(job_output))==_canonical(receipt)==_canonical(expected):
        raise gh.Reject('Rebuilt PROD does not match the exact same-run TEST proof')
    return RebuiltPromotion(origin,control.run_id,receipt_id,control.control_sha)
