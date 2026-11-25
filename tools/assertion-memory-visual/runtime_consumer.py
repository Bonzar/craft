"""Protected Visual TEST companion selection; no host command or privilege."""
from pathlib import Path
import tempfile
from urllib.parse import quote
from zipfile import ZipFile

if __package__:
    from . import runtime_companion as companion
else:
    import runtime_companion as companion

REPO = '/repos/Bonzar/craft'
REPO_ID = 1260097465


def stage_original(get, original, static_sha, destination):
    expected=companion.identity(original.tag, original.revision, original.release_id,
                                original.build_run_id, static_sha)
    name=companion.artifact_name(expected)
    found=get(f"{REPO}/actions/runs/{original.build_run_id}/artifacts?per_page=2&name={quote(name,safe='')}")
    artifacts=found.get('artifacts') if type(found) is dict else None
    if not isinstance(artifacts,list) or found.get('total_count')!=1 or len(artifacts)!=1:
        raise ValueError('Expected exactly one original Visual runtime companion')
    selected=artifacts[0]
    if type(selected) is not dict:
        raise ValueError('Invalid Visual runtime companion index')
    ident=str(selected.get('id',''))
    if not companion.ID.fullmatch(ident) or selected.get('name')!=name:
        raise ValueError('Wrong Visual runtime companion selection')
    meta=get(f'{REPO}/actions/artifacts/{ident}')
    run=meta.get('workflow_run') if type(meta) is dict else None
    if (type(run) is not dict or not all(run.get(k)==REPO_ID for k in ('repository_id','head_repository_id'))
            or type(meta.get('size_in_bytes')) is not int or not 0<meta['size_in_bytes']<=2*companion.MAX_ZIP
            or meta.get('name')!=name or str(run.get('id'))!=original.build_run_id
            or run.get('head_sha')!=original.revision or meta.get('expired') is not False):
        raise ValueError('Unexpected Visual runtime artifact origin')
    destination=Path(destination)
    with tempfile.TemporaryDirectory(dir=destination.parent,prefix='.visual-runtime-verified-') as d:
        outer=Path(d)/'provider.zip'
        get(f'{REPO}/actions/artifacts/{ident}/zip',outer)
        raw=outer.read_bytes()
        _,sha=companion.verify_provider(meta,raw,expected,ident)
        with ZipFile(outer) as archive:
            exact=archive.read('runtime.zip')
        out=Path(d)/'stage'
        out.mkdir(mode=0o700)
        (out/'runtime.zip').write_bytes(exact)
        (out/'runtime-control.json').write_bytes(companion.wire({
            'identity':expected,'companion_sha256':sha,'provider_artifact_id':ident,
            'provider_artifact_digest':meta['digest']}))
        # The checkout-private destination must not overwrite another stage.
        destination.mkdir(mode=0o700)
        for path in out.iterdir():
            path.rename(destination/path.name)
    return expected,sha


def stage_test_if_required(stage, setting, *, get, original, static_sha, destination):
    if stage!='test' or setting in (None,'','false'):
        return None
    if setting!='true':
        raise ValueError('Unsupported protected Visual runtime setting')
    return stage_original(get,original,static_sha,destination)
