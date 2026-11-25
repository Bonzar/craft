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
import runtime_bootstrap

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


def health_credential(policy, name='runtime-health-token'):
    token=policy/name
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



def prod_legacy_health(visual, key):
    """Private rollback-only proof; initial package must still pass modern smoke."""
    unit='assertion-memory-visual-prod.service'
    old='/etc/assertion-memory-visual/prod/asset-origin.py'
    installed=str(visual/'runtime/current/asset-origin.py')
    def running(preferred):
        wire=subprocess.check_output(('/usr/bin/systemctl','show',unit,
                       '--property=ActiveState,MainPID,ExecMainStartTimestampMonotonic'),
                      env={'PATH':'/usr/bin:/bin','LANG':'C'},timeout=3).decode('ascii')
        fields=dict(x.split('=',1) for x in wire.splitlines())
        pid=fields.get('MainPID','')
        since=fields.get('ExecMainStartTimestampMonotonic','')
        boot=Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip()
        if (fields.get('ActiveState')!='active' or not re.fullmatch(r'[1-9][0-9]{0,12}',pid)
                or not re.fullmatch(r'[1-9][0-9]{0,19}',since) or not re.fullmatch(r'[0-9a-f-]{36}',boot)):
            raise ValueError('Legacy Visual process generation not available')
        with (Path('/proc')/pid/'cmdline').open('rb') as cmd:
            args=cmd.read(4097).split(b'\x00')
        permitted=(installed,) if preferred else (old,installed)
        if not any(args==[b'/usr/bin/python3',b'-I',b'-B',script.encode(),b'prod',b'8830',b'']
                   for script in permitted):
            raise ValueError('Legacy Visual service start identity does not match')
        return boot+':'+pid+':'+since
    def read_only(op,mime):
        conn=HTTPConnection('127.0.0.1',8830,timeout=3)
        try:
            raw=json.dumps(op,separators=(',',':')).encode('ascii')
            conn.request('POST','/memory-visual-prod-read',body=raw,
                         headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
            response=conn.getresponse()
            data=response.read(2_400_001)
            if response.status!=200 or response.headers.get_content_type()!=mime or len(data)>2_400_000:
                raise ValueError('Legacy Visual authenticated health did not return exact data')
            return data
        finally:
            conn.close()
    def probe_once(pair, previous):
        generation=running(previous is not None)
        if previous is not None and generation==previous:
            raise ValueError('Previous Visual process did not restart')
        digest=Path(pair['static']).name
        if json.loads(read_only({'op':'current'},'application/json'))!={'digest':digest}:
            raise ValueError('Legacy Visual active static digest not restored')
        manifest=visual/pair['static']/'manifest.json'
        with manifest.open('rb') as src:
            pins=json.loads(src.read(1025))
        if sha256(read_only({'op':'asset','digest':digest,'name':'index.html'},'text/html')).hexdigest()!=pins['index.html']:
            raise ValueError('Legacy Visual served bytes did not match approved assets')
        if running(previous is not None)!=generation:
            raise ValueError('Legacy Visual process changed during health read')
        return generation
    def probe(pair,previous):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            try:
                return probe_once(pair,previous)
            except (OSError,ValueError,subprocess.SubprocessError):
                time.sleep(0.2)
        raise ValueError('Authenticated legacy PROD service recovery not proven')
    return probe

def receive(stage, command, stream):
    if stage not in HOST_SCOPE:
        raise ValueError('Wrong configured Visual stage')
    policy,visual,port,helper=HOST_SCOPE[stage]
    if not enabled(policy):
        raise ValueError('Scoped Visual runtime is not provisioned')
    runtime=visual/'runtime'
    key=health_credential(policy)
    legacy=policy/'legacy-runtime-bootstrap.json'
    bootstrap=None
    if stage=='prod' and os.path.lexists(legacy):
        legacy_key=health_credential(policy,'legacy-runtime-asset-token')
        if legacy_key==key:
            raise ValueError('No independent legacy PROD source health authority')
        bootstrap=runtime_bootstrap.read_policy(legacy,prod_legacy_health(visual,legacy_key),
                 source=Path('/etc/assertion-memory-visual/prod/asset-origin.py'))
    def activate(expected, archive, runtime_sha, files):
        pair=joint.activate_verified(visual,runtime,expected,archive,runtime_sha,files,
                                     restart=lambda:restart(policy/helper),
                                     smoke=lambda state:smoke(stage,runtime,port,state,key),bootstrap=bootstrap)
        static=Path(pair['static']).name
        return {'environment':stage,'revision':expected['revision'],'archive_sha256':expected['static_sha'],
                'release':static,'runtime_sha256':runtime_sha}
    return framing.receive(stage,command,stream,signers=policy/'allowed-signers',
                           private_workspace=runtime,activate=activate)
