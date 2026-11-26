"""Plan a fixed TEST graph-only systemd overlay from the exact installed ExecStart.

The operator supplies the original raw systemd assignment locally, never logs
its values, and must pin the actual effective configuration and reviewed bytes.
"""
from hashlib import sha256
from ipaddress import ip_address
from pathlib import PurePosixPath
import re
import shlex
from urllib.parse import urlsplit

UNIT='assertion-test-deploy-graph.service'
ROOT='/srv/assertion-release/test'
SOURCE=ROOT+'/state/fictional.sqlite3'
DROPIN='98-test-direct-event-rehearsal.conf'
PUBLIC='https://d5dc14bhh6hba54q0i3p.hsvi2zuh.apigw.yandexcloud.net'
DIRECT='https://153.51.163.106'

def render(line,current_release,public_origin,direct_origin):
    if public_origin!=PUBLIC or direct_origin!=DIRECT:
        raise ValueError('origin differs from reviewed TEST deployment')
    release=PurePosixPath(current_release)
    if release.parent!=PurePosixPath(ROOT+'/releases') or not re.fullmatch(r'[a-f0-9]{40}-[a-f0-9]{64}',release.name):
        raise ValueError('not the exact immutable TEST artifact identity')
    if not line.startswith('ExecStart=') or len(line)>8192 or any(x in line for x in '\r\n\\$;`'):
        raise ValueError('expected one direct Graph ExecStart assignment')
    args=shlex.split(line[len('ExecStart='):])
    if len(args)<4 or args[0] not in (ROOT+'/current/venv/bin/python','%h/current/venv/bin/python'):
        raise ValueError('not the fixed TEST artifact interpreter')
    prefix=['-B','-m','apps.graph_viewer.server'] if args[1]=='-B' else ['-m','apps.graph_viewer.server']
    if args[1:1+len(prefix)]!=prefix:
        raise ValueError('not the existing Graph entry point')
    if args.count('--source')!=1 or args[args.index('--source')+1] not in (SOURCE,'%h/state/fictional.sqlite3'):
        raise ValueError('not the TEST synthetic database')
    if args.count('--port')!=1 or args[args.index('--port')+1]!='8891':
        raise ValueError('not the TEST Graph listener')
    if args.count('--synthetic-fixture')!=1 or any(x.startswith('--public-origin') for x in args):
        raise ValueError('expected existing synthetic TEST startup without an explicit public origin')
    if any(x.startswith(('--test-direct-events-origin','--database-clear-')) for x in args):
        raise ValueError('refusing an already configured or out-of-scope flag')
    target=urlsplit(direct_origin)
    ip=ip_address(target.hostname)
    if ip.version!=4 or not ip.is_global or direct_origin!='https://'+str(ip):
        raise ValueError('not the fixed ordinary public TLS origin')
    return '[Service]\nExecStart=\n'+line+' --public-origin '+public_origin+' --test-direct-events-origin '+direct_origin+'\n'

def pin(data):
    return sha256(data.encode()).hexdigest()
