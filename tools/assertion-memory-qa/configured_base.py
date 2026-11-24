"""Configured Base adapter: source/receipt authority only, no live transport."""
from dataclasses import asdict, dataclass
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
import re
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'assertion-memory-release'))
import github_graph_preview as canonical

REPO=canonical.REPO
MAIN='refs/heads/main'
WORKFLOW='configured-release.yml'
CALLER=REPO+'/.github/workflows/'+WORKFLOW+'@'+MAIN
REUSABLE_PATH=REPO+'/.github/workflows/configured-release-reusable.yml'
APPROVAL_NAMES={'test':'configured-release-base-test','prod':'configured-release-base-prod'}
EVIDENCE_NAME='configured-release-base-test-{run}-attempt-1'
RECEIPT_FILE='configured-base-test-proof.json'
TEST_REQUIRED_STEPS=('Verify exact configured Base artifact and approval',
                     'Activate and smoke configured Base TEST',
                     'Preserve configured Base TEST evidence')
IDENTITY_FIELDS=('tag','revision','release_id','build_run_id','artifact_id','archive_sha')
ID=re.compile(r'[1-9][0-9]{0,19}')
SHA=re.compile(r'[a-f0-9]{40}')
DIGEST=re.compile(r'[a-f0-9]{64}')


def expect(ok,error):
    if not ok: raise ValueError(error)


def field(pattern,value):
    return isinstance(value,str) and pattern.fullmatch(value) is not None


def json_object(data):
    expect(type(data) is str and 0<len(data)<=8192,'Invalid TEST proof text')
    def unique(pairs):
        out=dict(pairs)
        expect(len(out)==len(pairs),'Duplicate JSON member')
        return out
    result=json.loads(data,object_pairs_hook=unique)
    expect(type(result) is dict,'Expected JSON object')
    return result


@dataclass(frozen=True)
class Original:
    tag: str
    revision: str
    release_id: str
    build_run_id: str
    artifact_id: str
    archive_sha: str

    def __post_init__(self):
        expect(canonical.product_of_tag(self.tag).workflow=='assertion-memory-base.yml'
               and field(SHA,self.revision) and field(DIGEST,self.archive_sha)
               and all(field(ID,x) for x in (self.release_id,self.build_run_id,self.artifact_id)),
               'Expected exact original Base artifact')


@dataclass(frozen=True)
class Control:
    stage: str
    run: str
    control_sha: str
    tag: str

    def __post_init__(self):
        expect(self.stage in APPROVAL_NAMES and field(ID,self.run) and field(SHA,self.control_sha)
               and canonical.product_of_tag(self.tag).workflow=='assertion-memory-base.yml',
               'Invalid configured Base control context')

    @property
    def environment(self):
        return APPROVAL_NAMES[self.stage]


def control(env,stage,event):
    expect(stage in APPROVAL_NAMES and all(env.get(k)==v for k,v in {
        'GITHUB_REPOSITORY':REPO,'GITHUB_REF':MAIN,'GITHUB_REF_PROTECTED':'true',
        'GITHUB_WORKFLOW_REF':CALLER,'GITHUB_EVENT_NAME':'workflow_dispatch',
        'GITHUB_RUN_ATTEMPT':'1','GITHUB_JOB':stage,'PROFILE':'base',
    }.items()),'Untrusted configured Base stage')
    tag=env.get('GRAPH_TAG')
    expect(event.get('inputs')=={'profile':'base','tag':tag},'Wrong explicit Base release selection')
    return Control(stage,env['GITHUB_RUN_ID'],env['GITHUB_SHA'],tag)


def original(get, ctx, build):
    expect(set(build)==set(IDENTITY_FIELDS),'Unexpected configured build output')
    candidate=Original(**build)
    expect(candidate.tag==ctx.tag and candidate.build_run_id!=ctx.run,'Invalid original build on this run')
    # Re-resolve published release, original run and ZIP metadata. A prior job
    # output is never the authority for the ZIP path or its content identity.
    resolved=Original(**canonical.from_tag(get,ctx.tag))
    expect(resolved==candidate,'Configured Base build evidence disagrees with GitHub')
    return resolved


def unpack_verified(zip_path,output,release):
    # Copy verified bytes into a private adjacent payload; legacy unpack is
    # reused, but never reads a caller path again after its digest is checked.
    from lifecycle import ci
    with tempfile.TemporaryDirectory(prefix='.configured-base-',dir=output) as work:
        sealed=Path(work)/'verified.zip'
        with os.fdopen(os.open(zip_path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as source, sealed.open('xb') as sink:
            info=os.fstat(source.fileno())
            expect(stat.S_ISREG(info.st_mode) and 0<info.st_size<=canonical.MAX_ARTIFACT,
                   'Unexpected Base ZIP shape')
            digest=hashlib.sha256()
            remaining=info.st_size
            while remaining:
                block=source.read(min(remaining,1<<20))
                expect(bool(block),'Base ZIP changed while staging')
                digest.update(block)
                sink.write(block)
                remaining-=len(block)
            expect(source.read(1)==b'' and digest.hexdigest()==release.archive_sha,
                   'Configured Base original ZIP hash mismatch')
        inner=ci.unpack_zip(sealed,output,release.revision)
    expect(field(DIGEST,inner),'Unexpected immutable Base TAR digest')
    with (output/'release.tar.gz').open('rb') as source:
        expect(hashlib.file_digest(source,'sha256').hexdigest()==inner,'Configured Base TAR hash changed')
    return inner


def expected_test(ctx, release, tar_sha):
    expect(field(DIGEST,tar_sha) and ctx.tag==release.tag and ctx.run!=release.build_run_id,
           'Invalid TEST identity')
    return {'schema':1,'profile':'base','workflow_ref':CALLER,'control_sha':ctx.control_sha,
            'run_id':ctx.run,'run_attempt':'1','environment':APPROVAL_NAMES['test'],
            'tar_sha256':tar_sha,**asdict(release)}


def seal_test(ctx,release,tar_sha,receiver_receipt):
    # The shared caller may write/upload this only after exact provider smoke.
    expect(ctx.stage=='test' and receiver_receipt=={
        'status':'ok','environment':'test','revision':release.revision,'sha256':tar_sha,
    },'Exact Base TEST activation not attested')
    return expected_test(ctx,release,tar_sha)


@dataclass(frozen=True)
class Promotion:
    release: Original
    control_sha: str
    run_id: str
    test_receipt_id: str
    tar_sha256: str

    def __post_init__(self):
        expect(field(SHA,self.control_sha) and field(ID,self.run_id)
               and field(ID,self.test_receipt_id) and self.run_id!=self.release.build_run_id
               and field(DIGEST,self.tar_sha256),'Invalid configured Base promotion')


def candidate_promotion(ctx,release,tar_sha,job_output,verify_test_receipt):
    expect(ctx.stage=='prod','Configured Base PROD stage required')
    expected=expected_test(ctx,release,tar_sha)
    # Shared authenticator must independently validate the first-attempt same-run
    # completed TEST job, its steps, artifact origin, digest, and bounded JSON.
    # The outer run must still be in progress here; legacy verifier is not used.
    receipt_id,receipt=verify_test_receipt(
        workflow=WORKFLOW,run_id=ctx.run,control_sha=ctx.control_sha,
        job_name='configured / TEST',required_steps=TEST_REQUIRED_STEPS,
        artifact_name=EVIDENCE_NAME.format(run=ctx.run),filename=RECEIPT_FILE)
    expect(json_object(job_output)==receipt==expected,'Configured Base same-run TEST proof mismatch')
    return Promotion(release,ctx.control_sha,ctx.run,receipt_id,tar_sha)


def signed_audience(promotion,*,vm_id,environment_node_id,reusable_sha,nonce):
    expect(field(SHA,reusable_sha) and field(DIGEST,nonce)
           and field(re.compile('[A-Za-z0-9.-]{1,128}'),vm_id)
           and field(re.compile('[A-Za-z0-9_=-]{4,180}'),environment_node_id),
           'Invalid configured Base host binding')
    wire=json.dumps({'intent':asdict(promotion),'profile':'base','attempt':1,
                     'environment':APPROVAL_NAMES['prod'],'environment_node_id':environment_node_id,
                     'vm':vm_id,'reusable':REUSABLE_PATH+'@'+reusable_sha,'nonce':nonce},
                    sort_keys=True,separators=(',',':')).encode('ascii')
    return 'urn:assertion-memory:configured:base:promote:v1:'+hashlib.sha256(wire).hexdigest()
