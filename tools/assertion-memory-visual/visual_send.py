"""Visual CI policy adapter using common pinned SSH transport, never shell execution."""
import json
import os
from pathlib import Path
import re
from ssh_transport import route, preflight, exchange
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


def deploy(d):
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
    envelope=sign(command,d['VISUAL_SIGNING_KEY'],directory=tmp)
    expected={'environment':mode,'revision':rev,'archive_sha256':sha,'release':manifest}
    with route(host=host,port=port,account=f'assertion-visual-{mode}-deploy',host_pin=pin,
                key=d['VISUAL_SSH_KEY'],runtime_dir=tmp) as to:
        preflight(to)
        with (Path(d['VISUAL_DOWNLOAD'])/'visual.tar').open('rb') as archive:
            status,body=exchange(to,command,envelope=envelope,payload=archive,timeout=60,limit=1024)
    if status!=0 or body!=json.dumps(expected,separators=(',',':')).encode()+b'\n':
        raise ValueError('visual_receiver_failed_or_receipt_mismatch')
    print('Authorized shared receiver returned exact visual release identity')


if __name__=='__main__':
    main()
