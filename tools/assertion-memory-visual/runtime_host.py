"""Closed Visual host bindings; invoked by the configured unprivileged writer."""
from hashlib import sha256
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

import runtime_joint as joint
import runtime_receive as framing

HOST_SCOPE={
    'test':(Path('/etc/assertion-memory-visual/configured/test'),
            Path('/var/lib/assertion-memory-visual/test'),8829,'runtime-test-restart.py'),
    'prod':(Path('/etc/assertion-memory-visual/configured/prod'),
            Path('/var/lib/assertion-memory-visual/prod'),8830,'runtime-prod-restart.py'),
}

def enabled(policy):
    marker=policy/'runtime-enabled'
    if not os.path.lexists(marker):
        return False
    meta=marker.lstat()
    if (meta.st_uid!=0 or not stat.S_ISREG(meta.st_mode) or meta.st_mode&0o022
            or meta.st_nlink!=1 or meta.st_size!=3 or marker.read_bytes()!=b'on\n'):
        raise ValueError('Untrusted Visual TEST runtime enable marker')
    return True


def health_credential(policy):
    token=policy/'runtime-health-token'
    with os.fdopen(os.open(token,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as stream:
        meta=os.fstat(stream.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=0 or meta.st_mode&0o027 or meta.st_nlink!=1:
            raise ValueError('Untrusted Visual health authority')
        wire=stream.read(66)
    if not re.fullmatch(rb'[a-f0-9]{64}\n',wire):
        raise ValueError('Invalid Visual health authority')
    return wire[:-1].decode('ascii')


def restart(helper):
    subprocess.run(('/usr/bin/sudo','-n','--','/usr/bin/python3','-I','-B',str(helper)),
        env={'PATH':'/usr/bin:/bin','LANG':'C'},check=True,stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)


def smoke(stage, runtime, port, pair, key):
    entry=runtime/pair['runtime']/'asset-origin.py'
    with entry.open('rb') as stream:
        expected=sha256(stream.read(64_001)).hexdigest()
    want={'stage':stage,'runtime_entry_sha':expected,'static_digest':Path(pair['static']).name,
          'queue_ready':True}
    expires=time.monotonic()+10
    while time.monotonic()<expires:
        conn=HTTPConnection('127.0.0.1',port,timeout=4)
        try:
            conn.request('GET','/__visual-runtime-health',headers={'Authorization':'Bearer '+key})
            response=conn.getresponse()
            if response.status==200 and response.headers.get_content_type()=='application/json':
                if json.loads(response.read(1025))==want:
                    return
        except (OSError, ValueError):
            pass
        finally:
            conn.close()
        time.sleep(0.2)
    raise ValueError('Visual running generation or queue readiness not proven')


def receive(stage, command, stream):
    if stage not in HOST_SCOPE:
        raise ValueError('Wrong configured Visual stage')
    policy,visual,port,helper=HOST_SCOPE[stage]
    if not enabled(policy):
        raise ValueError('Scoped Visual runtime is not provisioned')
    runtime=visual/'runtime'
    key=health_credential(policy)
    def activate(expected, archive, runtime_sha, files):
        pair=joint.activate_verified(visual,runtime,expected,archive,runtime_sha,files,
                                     restart=lambda:restart(policy/helper),
                                     smoke=lambda state:smoke(stage,runtime,port,state,key))
        static=Path(pair['static']).name
        return {'environment':stage,'revision':expected['revision'],'archive_sha256':expected['static_sha'],
                'release':static,'runtime_sha256':runtime_sha}
    return framing.receive(stage,command,stream,signers=policy/'allowed-signers',
                           private_workspace=runtime,activate=activate)
