"""Exact-host restricted SSH client; policy supplies only account and byte framing."""
from contextlib import contextmanager
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile

BASE_ENV={'PATH':'/usr/bin:/bin','LC_ALL':'C'}
ACCOUNT=re.compile(r'assertion-(?:test|production|visual-test|visual-prod|configured-visual-test|configured-visual-prod)-deploy\Z')
DENIED=b'{"status":"rejected","error":"command_denied"}\n'


@contextmanager
def route(*, host,port,account,host_pin,key,runtime_dir):
    if (not ACCOUNT.fullmatch(account)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}',host) or '..' in host
            or not re.fullmatch(r'[1-9][0-9]{0,4}',port) or int(port)>65535):
        raise ValueError('invalid_host_route')
    hostentry=host if port=='22' else f'[{host}]:{port}'
    pin=host_pin.strip()
    if pin.startswith(hostentry+' '):
        pin=pin[len(hostentry)+1:]
    if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]{60,120}',pin):
        raise ValueError('invalid_host_pin')
    with tempfile.TemporaryDirectory(prefix='scoped-release-',dir=runtime_dir) as directory:
        directory=Path(directory)
        identity=directory/'key'
        with os.fdopen(os.open(identity,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w',encoding='ascii') as handle:
            handle.write(key.strip()+'\n')
        hosts=directory/'known_hosts'
        hosts.write_text(hostentry+' '+pin+'\n',encoding='ascii')
        yield ['/usr/bin/ssh','-F','/dev/null','-T','-p',port,'-i',str(identity),
               '-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes',
               '-o','HostKeyAlgorithms=ssh-ed25519','-o','ControlMaster=no','-o','ClearAllForwardings=yes',
               '-o','PasswordAuthentication=no','-o','KbdInteractiveAuthentication=no',
               '-o','UserKnownHostsFile='+str(hosts),'-o','GlobalKnownHostsFile=/dev/null',
               '-o','ConnectTimeout=10','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=4',
               '--',account+'@'+host]


def exchange(route,command,*,envelope=b'',payload=None,timeout=None,limit=4096):
    if (not re.fullmatch(r'[a-z0-9-]+(?: [a-z0-9-]+){0,5}',command)
            or len(command)>160 or len(envelope)>20_001):
        raise ValueError('invalid_release_command')
    with tempfile.TemporaryFile() as source,tempfile.TemporaryFile() as output:
        source.write(envelope)
        if payload is not None:
            shutil.copyfileobj(payload,source,length=1<<20)
        source.seek(0)
        result=subprocess.run([*route,command],stdin=source,stdout=output,stderr=subprocess.DEVNULL,
           timeout=timeout or (570 if payload is not None else 25),check=False,env=BASE_ENV)
        output.seek(0)
        response=output.read(limit+1)
        if len(response)>limit:
            raise ValueError('release_response_too_large')
    return result.returncode,response


def preflight(route):
    for command in ('receiver-probe','id'):
        if exchange(route,command,limit=512)!=(1,DENIED):
            raise ValueError('scoped_receiver_preflight_failed')

