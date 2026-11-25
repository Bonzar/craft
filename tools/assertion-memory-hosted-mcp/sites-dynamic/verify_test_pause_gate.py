"""Bind owner-verified native TEST smoke evidence to the existing release jobs."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import quote
from zipfile import ZipFile

import configured_provider_checks as common
from configured_release_run import github_get
import github_graph_preview as gh
from prototypes.assertion_memory.dashboard_projection import release_generation
from verify_native_receipt import read_bundle

ENV='assertion-memory-sites-pause-test'
WORKFLOW='assertion-memory-sites-pause-evidence.yml'
ROOT=gh.ROOT
expect=common.expect
checks=common.TEST_REQUIRED_STEPS


def load_artifact(get, run, name, files, max_bytes):
    run_id=str(run['id'])
    raw=get(f'{ROOT}/actions/runs/{run_id}/artifacts?per_page=2&name='+quote(name,safe=''))
    a=gh.one_artifact(raw)
    origin=a.get('workflow_run') or {}
    expect(a.get('name')==name and a.get('expired') is False and
        type(a.get('size_in_bytes')) is int and 0<a['size_in_bytes']<=max_bytes and
        str(origin.get('id'))==run_id and origin.get('head_sha')==run['head_sha'] and
        origin.get('repository_id')==origin.get('head_repository_id')==gh.REPO_ID,'Artifact origin drift')
    artifact_id=gh.exact(gh.ID,str(a['id']))
    expected=gh.exact('sha256:'+gh.DIGEST,a.get('digest',''))[7:]
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/'artifact.zip'
        get(f'{ROOT}/actions/artifacts/{artifact_id}/zip',path)
        expect(0<path.stat().st_size<=max_bytes and sha256(path.read_bytes()).hexdigest()==expected,
            'Artifact archive hash drift')
        with ZipFile(path) as z:
            expect(len(z.namelist())==len(files) and set(z.namelist())==set(files)
                and all(0<z.getinfo(k).file_size<=bound for k,bound in files.items()),'Artifact shape drift')
            return {key:z.read(key) for key in files}


def verify_test(get, product, run_id, release):
    run_id=gh.exact(gh.ID,run_id)
    workflow=f'configured-{product}-release.yml'
    prefix=f'{ROOT}/actions/runs/{run_id}'
    run=get(prefix)
    wf=get(f'{ROOT}/actions/workflows/{workflow}')
    expect(str(run.get('id'))==run_id and run.get('workflow_id')==wf['id'] and
        run.get('path')=='.github/workflows/'+workflow and run.get('head_branch')=='main' and
        run.get('event') in ('workflow_dispatch','workflow_run') and run.get('run_attempt')==1 and
        run.get('repository',{}).get('id')==run.get('head_repository',{}).get('id')==gh.REPO_ID,
        'Wrong configured TEST workflow')
    listing=get(prefix+'/jobs?per_page=100')
    jobs=listing.get('jobs',[])
    test=[j for j in jobs if j.get('name')==common.TEST_JOB]
    expect(listing.get('total_count')==len(jobs) and len(test)==1 and
        test[0].get('conclusion')=='success' and not set(checks)-{
            s['name'] for s in test[0].get('steps',[]) if s.get('conclusion')=='success'},'TEST was not activated and smoked')
    f=common.TEST_RECEIPT
    raw=load_artifact(get,run,common.TEST_ARTIFACT_PREFIX+run_id+'-attempt-1',{f:8192},16384)
    r=common.json_object(raw[f].decode('utf-8'))
    for key,value in {**release,'profile':product,'schema':1,'run_id':run_id,'run_attempt':'1',
          'control_sha':run['head_sha'],'trigger':run['event'],
          'workflow_ref':gh.REPO+'/.github/workflows/'+workflow+'@'+gh.MAIN}.items():
        expect(r.get(key)==value,'TEST proof does not match exact release')
    gh.exact(gh.DIGEST,r.get('payload_sha256'))
    if product=='visual':
        gh.exact(gh.DIGEST,r.get('manifest_sha256'))
    return r


def validate_external(receipt, profile, base, visual, bundle_sha, *, now=None):
    keys={'source_sha','deployment_sha256','saved_version_sha256','project_id_sha256',
          'acl_before_sha256','acl_after_sha256','plugin_before_sha256','plugin_after_sha256',
          'archive_sha256','base_generation','visual_digest','stage','observed_at','smoke'}
    expect(set(receipt)==keys and receipt['stage']=='test','TEST evidence shape/lane mismatch')
    gh.exact(gh.SHA,receipt['source_sha'])
    for key in keys-{'source_sha','stage','observed_at','smoke'}:
        gh.exact(gh.DIGEST,receipt[key])
    expect(receipt['project_id_sha256']==profile['project_id_sha256'] and
        receipt['acl_before_sha256']==receipt['acl_after_sha256']==profile['access_identity_sha256'] and
        receipt['plugin_before_sha256']==receipt['plugin_after_sha256'] and
        receipt['archive_sha256']==bundle_sha and receipt['visual_digest']==visual['manifest_sha256'] and
        receipt['base_generation']==release_generation(base['revision'],base['payload_sha256'],base['tag'],'test'),
        'TEST provider evidence crosses release or owner scope')
    stamp=datetime.strptime(receipt['observed_at'],'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    current=now or datetime.now(timezone.utc)
    expect(0<=(current-stamp).total_seconds()<=12*3600,'Native TEST smoke is not fresh')
    smoke=receipt['smoke']
    expect(type(smoke) is dict and set(smoke)=={'initial','changed','restored','denied'},'Missing native pause smoke')
    def state(value):
        expect(type(value) is dict and set(value)=={'prepare','put','readback','paused','revision'} and
            type(value['paused']) is bool,'Incomplete native UI transitions')
        gh.exact('[0-9a-f]{32}',value['revision'])
        return value
    initial,changed,restored=[state(smoke[k]) for k in ('initial','changed','restored')]
    expect((initial['prepare'],initial['put'],initial['readback'])==(200,None,200) and
        all((x['prepare'],x['put'],x['readback'])==(200,200,200) for x in (changed,restored)) and
        initial['paused']==restored['paused']!=changed['paused'] and
        len({x['revision'] for x in (initial,changed,restored)})==3 and
        smoke['denied']=={'no_owner':403,'bad_csrf':403},'Native pause/restore or deny not proven')
    return {**receipt,'base_tag':base['tag'],'visual_tag':visual['tag'],
            'base_test_run_id':base['run_id'],'visual_test_run_id':visual['run_id'],
            'authority':'owner-provider-readback-and-protected-review'}


def approved(get, env):
    run=gh.exact(gh.ID,env['GITHUB_RUN_ID'])
    env_id=gh.exact(gh.ID,env['PAUSE_EVIDENCE_ENV_ID'])
    reviewer_id=gh.exact(gh.ID,env['PAUSE_EVIDENCE_REVIEWER_ID'])
    expect(env['GITHUB_REPOSITORY']==gh.REPO and env['GITHUB_REF']==gh.MAIN and
        env['GITHUB_EVENT_NAME']=='workflow_dispatch' and env['GITHUB_RUN_ATTEMPT']=='1' and
        env['GITHUB_WORKFLOW_REF']==gh.REPO+'/.github/workflows/'+WORKFLOW+'@'+gh.MAIN and
        env['GITHUB_REF_PROTECTED']=='true','Wrong evidence workflow scope')
    path=f'{ROOT}/environments/{ENV}'
    protection=get(path)
    rules=[r for r in protection.get('protection_rules',[]) if r.get('type')=='required_reviewers']
    expect(protection.get('name')==ENV and str(protection.get('id'))==env_id and
        protection.get('can_admins_bypass') is False and protection.get('deployment_branch_policy')==
        {'protected_branches':False,'custom_branch_policies':True} and len(rules)==1 and
        [(r.get('type'),str((r.get('reviewer') or {}).get('id'))) for r in rules[0].get('reviewers',[])]
        ==[('User',reviewer_id)],'Evidence environment is not protected')
    policy=get(path+'/deployment-branch-policies?per_page=2')
    expect(policy.get('total_count')==1 and [(r.get('type'),r.get('name')) for r in policy.get('branch_policies',[])]
           ==[('branch','main')],'Evidence environment is not main-only')
    reviews=get(f'{ROOT}/actions/runs/{run}/approvals')
    matches=[r for r in reviews if any(t.get('name')==ENV and str(t.get('id'))==env_id
                                      for t in r.get('environments',[]))]
    expect(len(matches)==1 and matches[0].get('state')=='approved'
          and str(matches[0].get('user',{}).get('id'))==reviewer_id,'Missing protected evidence review')


def main(env=os.environ):
    get=github_get(env['GH_TOKEN'])
    approved(get,env)
    base=gh.from_tag(get,env['BASE_TAG'])
    visual=gh.from_tag(get,env['VISUAL_TAG'])
    expect(not base['tag'].startswith('assertion-memory-visual-') and
        visual['tag'].startswith('assertion-memory-visual-'),'Expected Base and Visual releases')
    expect(base['revision']==visual['revision'],'Pause Base and Visual must use the same source commit')
    bp=verify_test(get,'base',env['BASE_TEST_RUN_ID'],base)
    vp=verify_test(get,'visual',env['VISUAL_TEST_RUN_ID'],visual)
    run=gh.run_ok(get,base['build_run_id'],gh.product_of_tag(base['tag']),base['revision'],base['tag'])
    package=load_artifact(get,run,f"sites-mcp-bridge-{base['release_id']}-{base['revision']}",
                          {'source.tar.gz':150000,'build-evidence.json':8192},180000)
    bundle=package['source.tar.gz']
    sha=sha256(bundle).hexdigest()
    manifest,files=read_bundle(bundle,sha)
    expect(manifest['version']==2 and manifest['tag']==base['tag'] and manifest['source_sha']==base['revision'] and
        common.json_object(package['build-evidence.json'].decode())=={'archive_sha256':sha,'manifest':manifest},
        'Pause bundle is not the original release companion')
    proof=validate_external(common.json_object(env['NATIVE_RECEIPT']),json.loads(files['profiles/test.json']),bp,vp,sha)
    out=Path(env['RUNNER_TEMP'])/'sites-pause-test-evidence.json'
    with out.open('x',encoding='ascii') as f:
        json.dump(proof,f,ensure_ascii=True,sort_keys=True,separators=(',',':'))
    print('Native TEST pause release evidence verified; no PROD authority.')


if __name__=='__main__':
    try:
        main()
    except (KeyError, OSError, TypeError, ValueError):
        raise SystemExit('Native Sites TEST pause evidence did not pass; no green result recorded.') from None
