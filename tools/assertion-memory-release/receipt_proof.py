"""Shared release TEST provenance: workflow, run, steps and authentic artifact."""
import hashlib
import json
import re
import zipfile

ID=re.compile(r'[1-9][0-9]{0,19}\Z')
SHA=re.compile(r'[0-9a-f]{40}\Z')
DIGEST=re.compile(r'sha256:[0-9a-f]{64}\Z')


def verify(get, *, repo, workflow, run_id, control_sha, job_name, required_steps,
           artifact_name, filename, output, max_zip, max_json):
    if not ID.fullmatch(run_id) or not SHA.fullmatch(control_sha):
        raise ValueError('Invalid TEST receipt identity')
    root='/repos/'+repo
    run=get(f'{root}/actions/runs/{run_id}')
    wf=get(f'{root}/actions/workflows/{workflow}')
    if (str(run.get('id'))!=run_id or run.get('workflow_id')!=wf['id']
            or run.get('path')!='.github/workflows/'+workflow
            or run.get('event')!='workflow_dispatch' or run.get('head_branch')!='main'
            or run.get('head_sha')!=control_sha or run.get('run_attempt')!=1
            or run.get('status')!='completed' or run.get('conclusion')!='success'
            or (run.get('repository') or {}).get('full_name')!=repo
            or (run.get('head_repository') or {}).get('full_name')!=repo):
        raise ValueError('Source TEST run is not the exact successful workflow on main')
    result=get(f'{root}/actions/runs/{run_id}/attempts/1/jobs?per_page=100')
    jobs=result.get('jobs',[])
    if result.get('total_count')!=len(jobs) or len(jobs)>100:
        raise ValueError('Incomplete TEST jobs response')
    matching=[job for job in jobs if job.get('name')==job_name]
    if len(matching)!=1:
        raise ValueError('Expected exactly one TEST job')
    job=matching[0]
    if (str(job.get('run_id'))!=run_id or job.get('head_sha')!=control_sha
            or job.get('status')!='completed' or job.get('conclusion')!='success'):
        raise ValueError('TEST job did not complete successfully')
    for step in required_steps:
        matching=[sample for sample in job.get('steps',[]) if sample.get('name')==step]
        if len(matching)!=1 or matching[0].get('conclusion')!='success' or matching[0].get('status')!='completed':
            raise ValueError('TEST activation, smoke, or receipt upload was not successful')
    result=get(f'{root}/actions/runs/{run_id}/artifacts?per_page=100')
    if result.get('total_count')!=1 or len(result.get('artifacts',[]))!=1:
        raise ValueError('Expected exactly one durable TEST receipt artifact')
    artifact=result['artifacts'][0]
    origin=artifact.get('workflow_run') or {}
    if (artifact.get('name')!=artifact_name or artifact.get('expired') is not False
            or str(origin.get('id'))!=run_id or origin.get('head_sha')!=control_sha
            or not 0<artifact.get('size_in_bytes',0)<=max_zip):
        raise ValueError('TEST receipt artifact provenance mismatch')
    aid=str(artifact.get('id',''))
    digest=artifact.get('digest','')
    if not ID.fullmatch(aid) or not DIGEST.fullmatch(digest):
        raise ValueError('Invalid TEST receipt artifact IDs')
    archive=output/(filename.removesuffix('.json')+'.zip')
    get(f'{root}/actions/artifacts/{aid}/zip',archive)
    with archive.open('rb') as stream:
        if archive.stat().st_size>max_zip or hashlib.file_digest(stream,'sha256').hexdigest()!=digest[7:]:
            raise ValueError('TEST receipt ZIP digest mismatch')
    with zipfile.ZipFile(archive) as z:
        if z.namelist()!=[filename] or z.getinfo(filename).file_size>max_json:
            raise ValueError('Unexpected TEST receipt ZIP contents')
        receipt=json.loads(z.read(filename))
    return aid, receipt
