"""Protected Actions composition; the provider commands do not validate GitHub."""
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.request import Request, build_opener
import configured_provider_checks as common
import github_graph_preview as github

ROOT = Path(__file__).resolve().parents[2]
@dataclass(frozen=True)
class Operations:
    payload_name: str
    unpack: object
    activate: object
    smoke: object
    concurrency: tuple[str,str]


def github_get(token):
    def get(path, destination=None):
        if destination is None:
            return github.api(path, token)
        if not re.fullmatch(re.escape(github.ROOT)+r'/actions/artifacts/[1-9][0-9]{0,19}/zip', path):
            raise ValueError('Unexpected archival endpoint')
        req = Request('https://api.github.com'+path, headers={
            'Authorization':'Bearer '+token,
            'Accept':'application/vnd.github+json',
            'X-GitHub-Api-Version':'2022-11-28',
        })
        destination = Path(destination)
        count=0
        try:
            with build_opener(github.ArtifactRedirect()).open(req,timeout=25) as src, destination.open('xb') as out:
                while chunk := src.read(1024*1024):
                    count += len(chunk)
                    common.expect(count<=github.MAX_ARTIFACT,'Archive exceeds bound')
                    out.write(chunk)
            common.expect(count>0, 'Empty archived build')
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
    return get


def context(profile, env, stage, event):
    return common.control(profile, {
        **env, 'PROFILE': profile.name, 'GRAPH_TAG': env['RELEASE_BUILD_TAG'],
    }, stage, event)


def write_output(env, values):
    with Path(env['GITHUB_OUTPUT']).open('a', encoding='ascii') as stream:
        for key,value in values.items():
            if not re.fullmatch('[A-Za-z0-9_-]+',key) or any(x in str(value) for x in '\r\n'):
                raise ValueError('Invalid Actions output')
            stream.write(f'{key}={value}\n')


def load_registry():
    from configured_release_registry import registry
    return registry(ROOT, Operations)


def build(env,event,get,profiles,ops):
    profile, original = common.build_selection(get,profiles,env,event)
    d=asdict(original)
    d.update(profile=profile.name,test_environment=profile.test.environment,
             prod_environment=profile.prod.environment)
    d['test_concurrency'],d['prod_concurrency']=ops[profile.name].concurrency
    write_output(env,d)


def parameters(env,event,get,profiles,stage):
    common.expect(env.get('RELEASE_ENABLE')=='true', 'Configured deployment lane is not enabled')
    profile=profiles[env['RELEASE_BUILD_PROFILE']]
    control=context(profile,env,stage,event)
    common.verify_approval(get,control,env['RELEASE_ENVIRONMENT_ID'],env['RELEASE_REVIEWER_ID'])
    data={k:env['RELEASE_BUILD_'+k.upper()] for k in common.IDENTITY_FIELDS}
    artifact=common.original(get,control,data)
    return profile,control,artifact


def run(env=os.environ, argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    temp=Path(env['RUNNER_TEMP'])/'configured-release'
    # Proof output performs no network/secret access; it only carries the TEST receipt.
    if argv==['proof']:
        common.expect(env.get('GITHUB_JOB')=='test','Proof output is TEST-only')
        raw=(temp/common.TEST_RECEIPT).read_text(encoding='ascii')
        common.json_object(raw)
        write_output(env,{'proof':raw})
        return
    profiles,ops=load_registry()
    get=github_get(env['GH_TOKEN'])
    event=json.loads(Path(env['GITHUB_EVENT_PATH']).read_bytes())
    if argv==['build']:
        build(env,event,get,profiles,ops)
        return
    common.expect(len(argv)==2 and argv[0] in ('verify','deploy') and argv[1] in ('test','prod'),
                  'Unsupported release control step')
    phase,stage=argv
    profile,ctrl,release=parameters(env,event,get,profiles,stage)
    path=temp/'source.zip'
    payload=temp/'payload'
    operation=ops[profile.name]
    if phase=='verify':
        temp.mkdir(mode=0o700)
        get(f'{github.ROOT}/actions/artifacts/{release.artifact_id}/zip',path)
        with path.open('rb') as stream:
            common.expect(hashlib.file_digest(stream,'sha256').hexdigest()==release.archive_sha,
                          'Archive hash does not match immutable original')
        return
    # Transport helpers are intentionally absent for a profile until its own
    # strict on-host receiver/authenticator is independently provisionable.
    common.expect(operation.activate is not None, 'Configured receiver is not registered')
    payload.mkdir(mode=0o700)
    content=common.unpack_verified(path,payload,release,operation.unpack,payload_name=operation.payload_name)
    if stage=='prod':
        promotion=common.candidate_promotion(ctrl,release,content,env['RELEASE_TEST_PROOF'],get,temp)
    else:
        promotion=None
    receipt=operation.activate(ctrl,release,content,payload,promotion,env)
    operation.smoke(ctrl,release,content,payload,env)
    if stage=='test':
        proof=common.seal_test(ctrl,release,content,receipt)
        (temp/common.TEST_RECEIPT).write_text(json.dumps(proof, separators=(',',':'),sort_keys=True),encoding='ascii')


if __name__ == '__main__':
    try:
        run()
    except (KeyError, OSError, ValueError, ImportError) as err:
        # Credentials and unknown upstream text must not appear in job logs.
        raise SystemExit('Configured release stopped: '+type(err).__name__) from None
