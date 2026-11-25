"""Visual-only packed release control. Default opt-out; deployed path is still legacy."""
from dataclasses import asdict
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from standalone_bundle import artifact_name, open_original, MAX_TOTAL
import runtime_companion as runtime

PACK = 'visual-release.zip'
PROOF = 'visual-packed-test-proof.json'


def mode(stage, setting, earlier=None):
    if stage not in ('test','prod') or setting not in (None,'','false','true'):
        raise ValueError('Invalid protected Visual packed setting')
    selected=setting=='true'
    if stage=='prod' and earlier!=('true' if selected else 'false'):
        raise ValueError('Visual package selection must match protected TEST')
    return selected


def named(get,path,name):
    items=get(path+'?per_page=2&name='+name)
    if items.get('total_count')!=1 or len(items.get('artifacts',[]))!=1:
        raise ValueError('Missing or ambiguous exact Visual artifact')
    found=items['artifacts'][0]
    if found.get('name')!=name or not runtime.ID.fullmatch(str(found.get('id',''))):
        raise ValueError('Unexpected Visual artifact index')
    return str(found['id'])


def download(get,root,parent,aid,name,head,run,member,limit):
    doc=get(f'{root}/actions/artifacts/{aid}')
    origin=doc.get('workflow_run') or {}
    digest=doc.get('digest','')
    if (str(doc.get('id'))!=aid or doc.get('name')!=name or doc.get('expired') is not False
            or origin.get('head_sha')!=head or str(origin.get('id'))!=run
            or any(origin.get(k)!=1260097465 for k in ('repository_id','head_repository_id'))
            or not isinstance(digest,str) or not digest.startswith('sha256:')
            or not runtime.DIGEST.fullmatch(digest[7:]) or type(doc.get('size_in_bytes')) is not int
            or not 0<doc['size_in_bytes']<=limit+8192):
        raise ValueError('Untrusted Visual artifact provenance')
    with TemporaryDirectory(dir=parent,prefix='.visual-packed-provider-') as w:
        path=Path(w)/'download.zip'
        get(f'{root}/actions/artifacts/{aid}/zip',path)
        if path.stat().st_size>limit+8192:
            raise ValueError('Visual GitHub archive exceeds bound')
        raw=path.read_bytes()
    if sha256(raw).hexdigest()!=digest[7:]:
        raise ValueError('Visual GitHub artifact digest mismatch')
    with ZipFile(BytesIO(raw)) as zip:
        if zip.namelist()!=[member] or zip.getinfo(member).file_size>limit:
            raise ValueError('Unexpected Visual GitHub archive members')
        return zip.read(member),digest



def require_packed_test_step(get, root, ctrl):
    data=get(f'{root}/actions/runs/{ctrl.run}/attempts/1/jobs?per_page=100')
    jobs=data.get('jobs',[])
    matched=[j for j in jobs if j.get('name')=='configured / TEST']
    if data.get('total_count')!=len(jobs) or len(matched)!=1:
        raise ValueError('Missing exact TEST packed receipt job')
    job=matched[0]
    step=[x for x in job.get('steps',[]) if x.get('name')=='Preserve packed Visual TEST evidence']
    if (str(job.get('run_id'))!=ctrl.run or job.get('head_sha')!=ctrl.control_sha or job.get('conclusion')!='success'
            or len(step)!=1 or step[0].get('status')!='completed' or step[0].get('conclusion')!='success'):
        raise ValueError('Packed Visual TEST receipt upload not proven')

def runtime_witness(ctrl,original,content,aid,provider_sha,package_sha,companion_sha):
    return {'schema':'visual-packed-test-v1','lane':'test','run_id':ctrl.run,'control_sha':ctrl.control_sha,
            'artifact_id':aid,'artifact_digest':provider_sha,'combined_sha256':package_sha,
            'runtime_sha256':companion_sha,'static_sha256':content.sha256,
            'manifest_sha256':content.manifest_sha256,'original':asdict(original)}


def require_matching_witness(raw,witness):
    if raw!=runtime.wire(witness):
        raise ValueError('Exact packed Visual TEST witness mismatch')


def cli(env=os.environ,argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if len(argv)!=2 or argv[0] not in ('verify','deploy') or argv[1] not in ('test','prod'):
        raise ValueError('Unexpected Visual packed stage')
    phase,stage=argv
    # Visual-only executable is isolated from commits outside this fixed checkout.
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root/'assertion-memory-release'/'experimental'))
    from configured_release_run import load_registry, github_get, parameters, run as shared
    if env.get('RELEASE_BUILD_PROFILE')!='visual':
        raise ValueError('Packed release is Visual-only')
    selected=mode(stage,env.get('VISUAL_PACKAGED_RELEASE_REQUIRED'),
                  env.get('VISUAL_TEST_PACKAGED_RELEASE'))
    if not selected:
        return shared(env,argv)
    import configured_provider_checks as common
    import github_graph_preview as gh
    import configured_visual_profile as visual
    import visual_policy
    import release_core

    get=github_get(env['GH_TOKEN'])
    profiles,_=load_registry()
    event=json.loads(Path(env['GITHUB_EVENT_PATH']).read_bytes())
    _,ctrl,original=parameters(env,event,get,profiles,stage)
    folder=Path(env['RUNNER_TEMP'])/'configured-release'
    if phase=='verify':
        folder.mkdir(mode=0o700)
        name=artifact_name(original.release_id,original.revision)
        aid=named(get,f'{gh.ROOT}/actions/runs/{original.build_run_id}/artifacts',name)
        packed,provider=download(get,gh.ROOT,folder,aid,name,original.revision,original.build_run_id,PACK,MAX_TOTAL)
        (folder/PACK).write_bytes(packed)
        (folder/'visual-packed-origin.json').write_bytes(runtime.wire({
          'artifact_id':aid,'provider_digest':provider,'combined_digest':runtime.digest(packed)}))
        return

    saved=json.loads((folder/'visual-packed-origin.json').read_bytes())
    raw=(folder/PACK).read_bytes()
    binding,(static,companion)=open_original(raw,original,saved['combined_digest'])
    payload=folder/'payload'
    payload.mkdir(mode=0o700)
    static_sha=binding['static_sha']
    with TemporaryDirectory(dir=folder,prefix='.verify-packed-static-') as work:
        contents=release_core.unpack(BytesIO(static),Path(work),static_sha,visual_policy.archive_spec())
        manifest=visual_policy.validate(contents)
    content=common.Content(static_sha,manifest)
    witness=runtime_witness(ctrl,original,content,saved['artifact_id'],saved['provider_digest'],
                            saved['combined_digest'],runtime.digest(companion))
    if stage=='prod':
        common.candidate_promotion(ctrl,original,content,env['RELEASE_TEST_PROOF'],get,folder)
        require_packed_test_step(get,gh.ROOT,ctrl)
        name=f'visual-packed-test-{ctrl.run}-attempt-1'
        aid=named(get,f'{gh.ROOT}/actions/runs/{ctrl.run}/artifacts',name)
        raw,_=download(get,gh.ROOT,folder,aid,name,ctrl.control_sha,ctrl.run,PROOF,8192)
        require_matching_witness(raw,witness)

    (payload/'visual.tar').write_bytes(static)
    rpath=payload/'runtime.zip'
    rpath.write_bytes(companion)
    transport={'account':ctrl.target.receiver_account,'host':env['RELEASE_SSH_HOST'],
       'port':env['RELEASE_SSH_PORT'],'host_pin':env['RELEASE_SSH_HOST_PIN'],'ssh_key':env['RELEASE_SSH_KEY'],
       'signing_key':env['RELEASE_SIGNING_KEY'],'runner_temp':env['RUNNER_TEMP']}
    v=visual.Payload(payload,original.revision,static_sha,manifest)
    receipt=visual.activate(stage,v,transport,runtime=(binding,witness['runtime_sha256'],rpath))
    visual.verify_smoke(stage,v,{k:env['RELEASE_'+k.upper()] for k in ('gateway_host','gateway_token')})
    if stage=='test':
        proof=common.seal_test(ctrl,original,content,receipt)
        (folder/common.TEST_RECEIPT).write_bytes(runtime.wire(proof))
        (folder/PROOF).write_bytes(runtime.wire(witness))


if __name__=='__main__':
    try:
        cli()
    except (ValueError,OSError,KeyError,TypeError) as e:
        # Do not emit raw server/upstream exceptions.
        raise SystemExit('Visual packed release stopped: '+type(e).__name__) from None
