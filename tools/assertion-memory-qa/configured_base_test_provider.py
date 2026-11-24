"""Base configured operation hooks: scoped TEST only, no legacy fallback."""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

here=Path(__file__).resolve().parent
sys.path.insert(0,str(here/'prod-bridge'))
sys.path.insert(0,str(here/'configured-base'))
sys.path.insert(0,str(here.parent/'assertion-memory-release'))
from configured_base import BASE
from configured_base_test_gate import CALLER, WRAPPER, Intent, Policy, RECEIVER, audience, obj, plain
from prod_runner import mint
import ssh_transport

ACCOUNT='assertion-cfg-base-test-deploy'
SHAPE='assertion-test-deploy'   # Reuse local argv/key builder, never connect here.
RESULT='configured-base-test-receiver.json'


@contextmanager
def route(env):
    if BASE.test.receiver_account!=ACCOUNT or BASE.test.command!=RECEIVER: raise ValueError('Configured Base account contract changed')
    host=env['CONFIGURED_BASE_HOST']
    with ssh_transport.route(host=host,port=env['CONFIGURED_BASE_PORT'],account=SHAPE,
          host_pin=env['CONFIGURED_BASE_SSH_HOST_PIN'],key=env['CONFIGURED_BASE_SSH_KEY'],
          runtime_dir=env['RUNNER_TEMP']) as argv:
        if argv[0]!='/usr/bin/ssh' or argv[-2:]!=['--',SHAPE+'@'+host]:
            raise ValueError('Invalid local SSH arguments')
        yield [*argv[:-1],ACCOUNT+'@'+host]


def bind(ctrl,release,content):
    if (ctrl.profile!=BASE or ctrl.stage!='test' or ctrl.tag!=release.tag
            or content.manifest_sha256 is not None):
        raise ValueError('Only exact configured Base TEST authority is installed')
    return Intent(ctrl.run,ctrl.control_sha,ctrl.trigger,release.tag,release.release_id,
                  release.build_run_id,release.artifact_id,release.revision,content.sha256)


def expected(intent):
    return {'status':'ok','environment':'test','revision':intent.revision,'sha256':intent.archive_sha256}


def exchange(argv,envelope,payload):
    wire=json.dumps(envelope,separators=(',',':')).encode('ascii')+b'\n'
    code,body=ssh_transport.exchange(argv,RECEIVER,envelope=wire,payload=payload,limit=512)
    if code!=0 or not body.endswith(b'\n'):
        raise ValueError('Configured TEST receiver rejected transport')
    return obj(body)


def activate(ctrl,release,content,payload_dir,promotion,env):
    intent=bind(ctrl,release,content)
    if promotion is not None or env.get('CONFIGURED_BASE_TEST_READY')!='true':
        raise ValueError('Configured TEST operation is not provisioned')
    caller=env.get('GITHUB_WORKFLOW_REF')
    if caller not in (CALLER,WRAPPER):
        raise ValueError('Untrusted configured Base TEST caller')
    mode='pinned_wrapper' if caller==WRAPPER else 'pilot'
    policy=Policy(env['CONFIGURED_BASE_REUSABLE_SHA'],env['CONFIGURED_BASE_ENV_NODE_ID'],env['CONFIGURED_BASE_VM_ID'],mode=mode)
    actual=env.get('CONFIGURED_RELEASE_CODE_SHA') if mode=='pinned_wrapper' else intent.control_sha
    if policy.reusable_sha!=actual:
        raise ValueError('Configured local reusable SHA is not exact protected control')
    path=Path(payload_dir)/'release.tar.gz'
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as payload:
        meta=os.fstat(payload.fileno())
        if (not stat.S_ISREG(meta.st_mode) or not 0<meta.st_size<=110_000_000
                or hashlib.file_digest(payload,'sha256').hexdigest()!=content.sha256):
            raise ValueError('Configured TEST archive mismatch')
        payload.seek(0)
        with route(env) as argv:
            ssh_transport.preflight(argv)
            request={'op':'challenge','intent':asdict(intent)}
            nonce=exchange(argv,request,None)
            if (nonce.keys()!={'status','nonce'} or nonce['status']!='ok'
                    or not plain('[a-f0-9]{64}',nonce['nonce'])):
                raise ValueError('Configured TEST challenge refused')
            token=mint(audience(policy,intent,nonce['nonce']),env=env)
            receipt=exchange(argv,request|{'op':'deploy','nonce':nonce['nonce'],'token':token},payload)
    if receipt!=expected(intent): raise ValueError('Configured TEST activation receipt mismatch')
    with (Path(payload_dir)/RESULT).open('x',encoding='ascii') as target:
        json.dump(receipt,target,sort_keys=True)
    return receipt


def smoke(ctrl,release,content,payload_dir,env):
    intent=bind(ctrl,release,content)
    # Canonical receiver returns only after its transaction ran real process,
    # graph and MCP smoke. Common also seals this once into TEST evidence.
    with (Path(payload_dir)/RESULT).open('rb') as source:
        if obj(source.read(512))!=expected(intent):
            raise ValueError('Configured TEST smoke receipt missing or invalid')


def unpack(archive,directory,revision):
    # Exact Base artifact decoder, never a caller's/Visual's lifecycle import.
    spec=importlib.util.spec_from_file_location('_configured_base_release_ci',here/'release-ci.py')
    ci=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ci)
    return ci.unpack_zip(archive,directory,revision)
