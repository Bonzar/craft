"""Common configured release checks. Product adapters supply data and commands only."""
from dataclasses import asdict, dataclass
import hashlib
import json
import os
import stat
import tempfile
import zipfile
from pathlib import Path
import re
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import github_graph_preview as canonical

REPO=canonical.REPO
MAIN='refs/heads/main'

@dataclass(frozen=True)
class Target:
    environment: str
    receiver_scope: str
    receiver_account: str
    command: str

@dataclass(frozen=True)
class ReceiptLayout:
    archive_key: str
    manifest_key: str | None = None
    status_key: str | None = None

@dataclass(frozen=True)
class Profile:
    name: str
    original_workflow: str
    control_workflow: str
    reusable_workflow: str
    test: Target
    prod: Target
    receipt: ReceiptLayout

    @property
    def caller(self):
        return REPO+'/.github/workflows/'+self.control_workflow+'@'+MAIN

    @property
    def reusable_path(self):
        return REPO+'/.github/workflows/'+self.reusable_workflow

TEST_JOB='configured / TEST'
TEST_REQUIRED_STEPS=('Verify exact artifact and stage approval',
                     'Activate and smoke exact archive',
                     'Preserve TEST evidence',
                     'Publish same-run TEST proof')
TEST_ARTIFACT_PREFIX='configured-release-test-'
TEST_RECEIPT='configured-test-proof.json'
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
        expect(bool(canonical.product_of_tag(self.tag))
               and field(SHA,self.revision) and field(DIGEST,self.archive_sha)
               and all(field(ID,x) for x in (self.release_id,self.build_run_id,self.artifact_id)),
               'Expected exact original Base artifact')


@dataclass(frozen=True)
class Control:
    stage: str
    run: str
    control_sha: str
    tag: str

    profile: Profile

    def __post_init__(self):
        expect(self.stage in ('test','prod') and field(ID,self.run) and field(SHA,self.control_sha)
               and canonical.product_of_tag(self.tag).workflow==self.profile.original_workflow,
               'Invalid configured release control context')

    @property
    def target(self):
        return self.profile.test if self.stage=='test' else self.profile.prod


def control(profile,env,stage,event):
    expect(stage in ('test','prod') and all(env.get(k)==v for k,v in {
        'GITHUB_REPOSITORY':REPO,'GITHUB_REF':MAIN,'GITHUB_REF_PROTECTED':'true',
        'GITHUB_WORKFLOW_REF':profile.caller,'GITHUB_EVENT_NAME':'workflow_dispatch',
        'GITHUB_RUN_ATTEMPT':'1','GITHUB_JOB':stage,'PROFILE':profile.name,
    }.items()),'Untrusted configured release stage')
    tag=env.get('GRAPH_TAG')
    expect(event.get('inputs')=={'profile':profile.name,'tag':tag},'Wrong explicit release selection')
    return Control(stage,env['GITHUB_RUN_ID'],env['GITHUB_SHA'],tag,profile)


def original(get,ctx,build):
    expect(set(build)==set(IDENTITY_FIELDS),'Unexpected configured build output')
    candidate=Original(**build)
    expect(candidate.tag==ctx.tag and candidate.build_run_id!=ctx.run,
           'Invalid original build on this run')
    # Re-resolve published release, original run and ZIP metadata independently.
    resolved=Original(**canonical.from_tag(get,ctx.tag))
    expect(resolved==candidate,'Configured build evidence disagrees with GitHub')
    return resolved


def unpack_verified(zip_path,output,release,unpack,*,payload_name="release.tar.gz"):
    # Copy verified bytes into a private adjacent payload; legacy unpack is
    # reused, but never reads a caller path again after its digest is checked.
    with tempfile.TemporaryDirectory(prefix='.configured-base-',dir=output) as work:
        sealed=Path(work)/'verified.zip'
        with os.fdopen(os.open(zip_path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as source, sealed.open('xb') as sink:
            info=os.fstat(source.fileno())
            expect(stat.S_ISREG(info.st_mode) and 0<info.st_size<=canonical.MAX_ARTIFACT,
                   'Unexpected release ZIP shape')
            digest=hashlib.sha256()
            remaining=info.st_size
            while remaining:
                block=source.read(min(remaining,1<<20))
                expect(bool(block),'Release ZIP changed while staging')
                digest.update(block)
                sink.write(block)
                remaining-=len(block)
            expect(source.read(1)==b'' and digest.hexdigest()==release.archive_sha,
                   'Configured original ZIP hash mismatch')
        inner=unpack(sealed,output,release.revision)
    payload=Content(inner) if isinstance(inner,str) else inner
    expect(isinstance(payload,Content) and bool(re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,100}',payload_name)),
           'Unexpected immutable payload or layout')
    raw=output/payload_name
    with os.fdopen(os.open(raw,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as source:
        shape=os.fstat(source.fileno())
        expect(stat.S_ISREG(shape.st_mode) and 0<shape.st_size<=canonical.MAX_ARTIFACT
               and hashlib.file_digest(source,'sha256').hexdigest()==payload.sha256,'Configured payload hash changed')
    return payload


@dataclass(frozen=True)
class Content:
    sha256: str
    manifest_sha256: str | None = None

    def __post_init__(self):
        expect(field(DIGEST,self.sha256) and
               (self.manifest_sha256 is None or field(DIGEST,self.manifest_sha256)),
               'Invalid normalized release content')


def expected_test(ctx,release,content):
    expect((content.manifest_sha256 is not None)==(ctx.profile.receipt.manifest_key is not None)
           and ctx.tag==release.tag and ctx.run!=release.build_run_id,'Invalid TEST content scope')
    return {'schema':1,'profile':ctx.profile.name,'workflow_ref':ctx.profile.caller,'control_sha':ctx.control_sha,
            'run_id':ctx.run,'run_attempt':'1','environment':ctx.profile.test.environment,
            'payload_sha256':content.sha256,'manifest_sha256':content.manifest_sha256,**asdict(release)}


def seal_test(ctx,release,content,receiver_receipt):
    layout=ctx.profile.receipt
    expected={'environment':ctx.profile.test.receiver_scope,'revision':release.revision,
              layout.archive_key:content.sha256}
    if layout.manifest_key:
        expected[layout.manifest_key]=content.manifest_sha256
    if layout.status_key:
        expected[layout.status_key]='ok'
    expect(ctx.stage=='test' and receiver_receipt==expected,'Exact TEST activation not attested')
    return expected_test(ctx,release,content)


def verify_approval(get,ctx,env_id,reviewer_id):
    expect(field(ID,env_id) and field(ID,reviewer_id),'Invalid approval authority')
    name=ctx.target.environment
    root=canonical.ROOT
    path=f'{root}/environments/{name}'
    live=get(path)
    expect(live.get('name')==name and str(live.get('id'))==env_id
           and live.get('can_admins_bypass') is False
           and live.get('deployment_branch_policy')=={'protected_branches':False,'custom_branch_policies':True}
           and get(root+'/branches/main').get('protected') is True,'Wrong protected release environment')
    rules=[r for r in live.get('protection_rules',[]) if r.get('type')=='required_reviewers']
    expect(len(rules)==1 and [(i.get('type'),str((i.get('reviewer') or {}).get('id')))
            for i in rules[0].get('reviewers',[])]==[('User',reviewer_id)],'Wrong release reviewer')
    policy=get(path+'/deployment-branch-policies?per_page=2')
    expect(policy.get('total_count')==1 and
           [(r.get('type'),r.get('name')) for r in policy.get('branch_policies',[])]==[('branch','main')],
           'Deployment environment must be main-only')
    reviews=get(f'{root}/actions/runs/{ctx.run}/approvals')
    expect(type(reviews) is list and len(reviews)<=100,'Incomplete approval history')
    matches=[r for r in reviews if any(t.get('name')==name and str(t.get('id'))==env_id for t in r.get('environments',[]))]
    expect(len(matches)==1 and matches[0].get('state')=='approved'
           and (matches[0].get('user') or {}).get('type')=='User'
           and str((matches[0].get('user') or {}).get('id'))==reviewer_id,'Missing exact stage approval')
    return True


def verify_same_run_test(get,ctx,output):
    expect(ctx.stage=='prod','Same-run TEST proof is required by PROD')
    root=canonical.ROOT
    path=f'{root}/actions/runs/{ctx.run}'
    run=get(path)
    wf=get(root+'/actions/workflows/'+ctx.profile.control_workflow)
    expect(str(run.get('id'))==ctx.run and wf.get('id') and run.get('workflow_id')==wf['id']
           and run.get('path')=='.github/workflows/'+ctx.profile.control_workflow
           and run.get('event')=='workflow_dispatch' and run.get('head_branch')=='main'
           and run.get('head_sha')==ctx.control_sha and run.get('run_attempt')==1
           and run.get('status')=='in_progress'
           and all((run.get(k) or {}).get('id')==canonical.REPO_ID for k in ('repository','head_repository')),
           'Wrong live configured release run')
    result=get(path+'/attempts/1/jobs?per_page=100')
    jobs=result.get('jobs',[])
    expect(result.get('total_count')==len(jobs) and len(jobs)<=100,'Incomplete release jobs')
    matches=[j for j in jobs if j.get('name')==TEST_JOB]
    expect(len(matches)==1,'Expected exactly one configured TEST job')
    job=matches[0]
    expect(str(job.get('run_id'))==ctx.run and job.get('head_sha')==ctx.control_sha
           and job.get('status')=='completed' and job.get('conclusion')=='success',
           'Configured TEST not proven green')
    for name in TEST_REQUIRED_STEPS:
        steps=[r for r in job.get('steps',[]) if r.get('name')==name]
        expect(len(steps)==1 and steps[0].get('status')=='completed' and steps[0].get('conclusion')=='success',
               'Required TEST activation, smoke, or proof step not green')
    artifacts=get(path+'/artifacts?per_page=100')
    items=artifacts.get('artifacts',[])
    expect(artifacts.get('total_count')==len(items) and len(items)<=100,'Incomplete release artifacts')
    name=f'{TEST_ARTIFACT_PREFIX}{ctx.run}-attempt-1'
    matches=[x for x in items if x.get('name')==name]
    expect(len(matches)==1,'Expected unique same-run TEST evidence')
    item=matches[0]; origin=item.get('workflow_run') or {}
    aid=str(item.get('id','')); digest=item.get('digest')
    expect(field(ID,aid) and field(re.compile('sha256:[a-f0-9]{64}'),digest)
           and item.get('expired') is False and type(item.get('size_in_bytes')) is int
           and 0<item['size_in_bytes']<=16384 and str(origin.get('id'))==ctx.run
           and origin.get('head_sha')==ctx.control_sha,'Wrong TEST receipt artifact provenance')
    with tempfile.TemporaryDirectory(prefix='.verified-test-',dir=output) as work:
        archive=Path(work)/'receipt.zip'
        get(f'{root}/actions/artifacts/{aid}/zip',archive)
        with archive.open('rb') as source:
            expect(archive.stat().st_size<=16384 and
                   hashlib.file_digest(source,'sha256').hexdigest()==digest[7:],'TEST receipt ZIP hash mismatch')
        with zipfile.ZipFile(archive) as zip_file:
            expect(zip_file.namelist()==[TEST_RECEIPT] and zip_file.getinfo(TEST_RECEIPT).file_size<=8192,
                   'Unexpected TEST receipt ZIP content')
            receipt=json_object(zip_file.read(TEST_RECEIPT).decode('utf-8'))
    return aid,receipt


@dataclass(frozen=True)
class Promotion:
    profile: Profile
    release: Original
    control_sha: str
    run_id: str
    test_receipt_id: str
    content: Content

    def __post_init__(self):
        expect(field(SHA,self.control_sha) and field(ID,self.run_id)
               and field(ID,self.test_receipt_id) and self.run_id!=self.release.build_run_id
               and isinstance(self.content,Content),'Invalid configured promotion')


def candidate_promotion(ctx,release,content,job_output,get,output):
    expect(ctx.stage=='prod','Configured PROD stage required')
    expected=expected_test(ctx,release,content)
    receipt_id,receipt=verify_same_run_test(get,ctx,output)
    expect(json_object(job_output)==receipt==expected,'Configured same-run TEST proof mismatch')
    return Promotion(ctx.profile,release,ctx.control_sha,ctx.run,receipt_id,content)


def signed_audience(promotion,*,vm_id,environment_node_id,reusable_sha,nonce):
    expect(field(SHA,reusable_sha) and field(DIGEST,nonce)
           and field(re.compile('[A-Za-z0-9.-]{1,128}'),vm_id)
           and field(re.compile('[A-Za-z0-9_=-]{4,180}'),environment_node_id),
           'Invalid configured Base host binding')
    message={k:v for k,v in asdict(promotion).items() if k!='profile'}
    wire=json.dumps({'intent':message,'profile':promotion.profile.name,'attempt':1,
                     'environment':promotion.profile.prod.environment,'environment_node_id':environment_node_id,
                     'vm':vm_id,'reusable':promotion.profile.reusable_path+'@'+reusable_sha,'nonce':nonce},
                    sort_keys=True,separators=(',',':')).encode('ascii')
    return 'urn:assertion-memory:configured:'+promotion.profile.name+':promote:v1:'+hashlib.sha256(wire).hexdigest()
