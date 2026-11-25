"""Visual CI policy adapter using common pinned SSH transport, never shell execution."""
import json
import os
from pathlib import Path
import re
from ssh_transport import route, preflight, exchange
if __package__:
    from .visual_auth import COMMAND, sign
else:
    from visual_auth import COMMAND, sign


def main():
    d=os.environ
    mode=d['VISUAL_ENV']
    if (mode not in ('test','prod') or d['GITHUB_REPOSITORY']!='Bonzar/craft'
        or d['GITHUB_WORKFLOW_REF']!='Bonzar/craft/.github/workflows/assertion-memory-visual.yml@refs/heads/main'
        or d['GITHUB_REF']!='refs/heads/main' or d['GITHUB_RUN_ATTEMPT']!='1'
        or d['GITHUB_EVENT_NAME']!='workflow_dispatch' or d.get('VISUAL_DEPLOY_ENABLED')!='true'):
        raise ValueError('untrusted_visual_workflow')
    deploy(d)


def deploy(d, *, lane="legacy", runtime=None):
    if lane not in {"legacy", "configured"}:
        raise ValueError("invalid_visual_transport_lane")
    mode=d['VISUAL_ENV']
    rev,sha,manifest=d['VISUAL_REVISION'],d['VISUAL_SHA'],d['VISUAL_DIGEST']
    command=f'deploy-visual {mode} {rev} {sha}'
    if not COMMAND.fullmatch(command) or not re.fullmatch('[0-9a-f]{64}',manifest):
        raise ValueError('invalid_visual_release_descriptor')
    host,port=d['VISUAL_HOST'],d['VISUAL_PORT']
    host_field=host if port=='22' else f'[{host}]:{port}'
    scope,sep,pin=d['VISUAL_HOST_PIN'].partition(' ')
    if scope!=host_field or not sep:
        raise ValueError('invalid_visual_host_pin')
    tmp=d['RUNNER_TEMP']
    envelope=sign(command,d['VISUAL_SIGNING_KEY'],directory=tmp) if runtime is None else b''
    expected={'environment':mode,'revision':rev,'archive_sha256':sha,'release':manifest}
    runtime_claim=None
    if runtime is not None:
        if mode not in ('test','prod') or lane!='configured':
            raise ValueError('Only configured Visual accepts joint runtime release')
        if __package__:
            from . import runtime_companion, runtime_receive
        else:
            import runtime_companion, runtime_receive
        descriptor,runtime_digest,path=runtime
        if descriptor['revision']!=rev or descriptor['static_sha']!=sha:
            raise ValueError('Wrong staged Visual paired identity')
        raw=path.read_bytes()
        runtime_companion.verify(raw,descriptor,runtime_digest,stage=mode)
        signature=runtime_companion.sign(descriptor,runtime_digest,d['VISUAL_SIGNING_KEY'],stage=mode)
        command,joint=runtime_receive.assemble(descriptor,raw,signature,(Path(d['VISUAL_DOWNLOAD'])/'visual.tar').read_bytes(),stage=mode)
        runtime_claim={**expected,'runtime_sha256':runtime_digest}
    if lane == 'configured':
        if __package__:
            from .configured_visual_route import route as scoped_route
        else:
            from configured_visual_route import route as scoped_route
        account_prefix = 'assertion-cfg-vis'
    else:
        scoped_route = route
        account_prefix = 'assertion-visual'
    with scoped_route(host=host,port=port,account=f'{account_prefix}-{mode}-deploy',host_pin=pin,
                key=d['VISUAL_SSH_KEY'],runtime_dir=tmp) as to:
        preflight(to)
        if runtime_claim is None:
            with (Path(d['VISUAL_DOWNLOAD'])/'visual.tar').open('rb') as archive:
                status,body=exchange(to,command,envelope=envelope,payload=archive,timeout=60,limit=1024)
        else:
            status,body=exchange(to,command,payload=joint,timeout=70,limit=1024)
    if status!=0 or body!=json.dumps(runtime_claim or expected,separators=(',',':')).encode()+b'\n':
        raise ValueError('visual_receiver_failed_or_receipt_mismatch')
    print('Authorized shared receiver returned exact visual release identity')
    return expected


if __name__=='__main__':
    main()
