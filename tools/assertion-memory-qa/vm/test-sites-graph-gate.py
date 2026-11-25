#!/usr/bin/python3
"""Pinned release gate for the existing TEST Sites-facing Graph service.

This file is installed by the host operator under /etc, not executed with
privilege from a release tree. Default host provisioning leaves it disabled.
"""
from __future__ import annotations
from hashlib import sha256
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = Path('/srv/assertion-release/test')
CONFIG = Path('/etc/assertion-release/test/sites-graph-gate.json')
UNIT = 'memory-site-test-graph.service'
ACCOUNT = 'assertion-test-deploy'
HEX = re.compile(r'[0-9a-f]{64}\Z')
ARCHIVE = re.compile(r'([0-9a-f]{40})-([0-9a-f]{64})\Z')
TAG = re.compile(r'assertion-memory-v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]{1,30})?\Z')
GATEWAY = re.compile(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.){2}apigw\.yandexcloud\.net\Z')
PATH = '/memory-graph-test-read'
QUEUE = '/api/v1/capabilities?queue_status=1&limit=1&offset=0'
MAX_BODY = 64 * 1024
ACTIONS = frozenset(('restart','stop','smoke'))


def checked_read(path, limit, *, uid=None, private=False):
    fd = os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        st=os.fstat(f.fileno())
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_mode&0o022
                or (uid is not None and st.st_uid!=uid) or (private and st.st_mode&0o077)):
            raise ValueError('untrusted_graph_gate_file')
        body=f.read(limit+1)
    if len(body)>limit:
        raise ValueError('oversize_graph_gate_file')
    return body


def expected_identity(release, *, root=ROOT):
    if (root.resolve(strict=True)!=root or (root/'releases').is_symlink()
            or release.resolve(strict=True)!=release or release.parent!=root/'releases'
            or (root/'current').resolve(strict=True)!=release):
        raise ValueError('wrong_active_graph_release')
    match=ARCHIVE.fullmatch(release.name)
    if match is None:
        raise ValueError('wrong_active_graph_release')
    revision,archive=match.groups()
    tag_bytes=checked_read(release/'BASE_RELEASE_TAG',128)
    tag=tag_bytes.decode('ascii').strip()
    revision_bytes=checked_read(release/'REVISION',128)
    if not TAG.fullmatch(tag) or revision_bytes.decode('ascii').strip()!=revision:
        raise ValueError('unverified_active_graph_release')
    manifest=checked_read(release/'SHA256SUMS',5*1024*1024).decode('ascii').splitlines()
    for name,payload in (('BASE_RELEASE_TAG',tag_bytes),('REVISION',revision_bytes)):
        if manifest.count(sha256(payload).hexdigest()+'  ./'+name)!=1:
            raise ValueError('unverified_active_graph_release')
    data=dict(revision=revision,archive=archive,tag=tag,lane='test')
    release_sha=sha256(json.dumps(data,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode('ascii')).hexdigest()
    return tag,release_sha


def proof(value, expected):
    tag,generation=expected
    if (type(value) is not dict or value.get('stage')!='test' or value.get('release_version')!=tag
            or value.get('release_generation')!=generation or value.get('global_order') is not False
            or type(value.get('capabilities')) is not dict or type(value['capabilities'].get('live')) is not bool
            or type(value.get('readiness')) is not list or type(value.get('queues')) is not list
            or len(value['queues'])!=3 or any(type(q) is not dict for q in value['queues'])
            or [q.get('id') for q in value['queues']]!=['source','maintenance','interactive']):
        raise ValueError('wrong_canonical_sites_graph_identity')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*_args,**_kwargs):
        return None


def gateway_check(host,token, get=None):
    if type(host) is not str or not GATEWAY.fullmatch(host) or not HEX.fullmatch(token):
        raise ValueError('invalid_canonical_sites_graph_ingress')
    request=urllib.request.Request('https://'+host+PATH,method='POST',
        headers={'Content-Type':'application/json','Accept':'application/json','X-Memory-Graph-Auth':'Bearer '+token},
        data=json.dumps({'path':QUEUE},separators=(',',':')).encode('ascii'))
    if get is None:
        get=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open
    with get(request,timeout=5) as response:
        if response.status!=200 or response.headers.get_content_type()!='application/json':
            raise ValueError('canonical_sites_graph_unavailable')
        data=response.read(MAX_BODY+1)
    if len(data)>MAX_BODY:
        raise ValueError('oversize_canonical_sites_graph_response')
    return json.loads(data)


WRAPPER = Path('/opt/assertion-memory-site-test-graph/start.py')
PROJECTION = '/run/assertion-memory-dashboard/test/scheduler.json'
LOOPBACK = 'http://127.0.0.1:8819'
LOCAL_ENV = {
    'ASSERTION_MEMORY_QUEUE_DASHBOARD_STAGE': 'test',
    'ASSERTION_MEMORY_QUEUE_DASHBOARD_PROJECTION': PROJECTION,
}


def exact_listener(tcp, fd_links):
    records=(line.split() for line in tcp.splitlines()[1:])
    # 127.0.0.1:8819, TCP LISTEN. Require the exact serving PID's FD.
    matches=[r[9] for r in records if len(r)>9 and r[1]==b'0100007F:2273' and r[3]==b'0A']
    return len(matches)==1 and b'socket:['+matches[0]+b']' in fd_links


def serving_identity(release):
    r=subprocess.run(['/usr/bin/systemctl','show','--property=MainPID,ActiveState,User','--',UNIT],
                     env={'PATH':'/usr/bin:/bin'},check=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL,timeout=3)
    v=dict(line.split('=',1) for line in r.stdout.decode('ascii').splitlines())
    if (set(v)!={'MainPID','ActiveState','User'} or v['ActiveState']!='active' or v['User']!=ACCOUNT
            or not re.fullmatch(r'[1-9][0-9]{0,9}',v['MainPID'])):
        raise ValueError('wrong_sites_graph_unit')
    pid=v['MainPID']
    proc=Path('/proc')/pid
    argv=(proc/'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
    interpreter=release/'venv'/'bin'/'python'
    if (proc.stat().st_uid!=os.getuid() or Path(os.readlink(proc/'exe'))!=interpreter.resolve(strict=True)
            or argv!=[os.fsencode(interpreter),b'-s',b'-B',os.fsencode(WRAPPER)]):
        raise ValueError('wrong_sites_graph_process')
    env=(proc/'environ').read_bytes().split(b'\0')
    for name,raw in {**LOCAL_ENV,'PYTHONPATH':str(release)}.items():
        key=name.encode('ascii')+b'='
        if [x for x in env if x.startswith(key)]!=[key+os.fsencode(raw)]:
            raise ValueError('wrong_sites_graph_process_scope')
    links={os.fsencode(os.readlink(p)) for p in (proc/'fd').iterdir()}
    if not exact_listener((proc/'net'/'tcp').read_bytes(),links):
        raise ValueError('wrong_sites_graph_loopback_server')
    stat_fields=(proc/'stat').read_bytes().rsplit(b')',1)[1].split()
    return pid,stat_fields[19]


def local_wrapper_denial(get=None):
    req=urllib.request.Request(LOOPBACK+PATH,method='POST',
        headers={'Content-Type':'application/json','Accept':'application/json'},
        data=json.dumps({'path':QUEUE},separators=(',',':')).encode('ascii'))
    if get is None:
        get=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open
    try:
        with get(req,timeout=3):
            pass
    except urllib.error.HTTPError as exc:
        status=exc.code
        exc.close()
        if status in (401,403):
            return
    raise ValueError('sites_graph_wrapper_missing_exact_auth_denial')


LOCAL_WORKER = """
from pathlib import Path
import json,os,sys
p=Path(sys.argv[1])
sys.path.insert(0,str(p))
from apps.graph_viewer.server import queue_dashboard as q
assert q.LOADED_RELEASE == p and Path(q.__file__).resolve().is_relative_to(p)
assert q.SCOPES['test'][0]=='assertion-test-deploy'
obj=q.configured_for_legacy_reader(q.SCOPES['test'][1])
assert obj is not None and obj.stage=='test' and obj.publisher_uid==os.getuid()
result=obj.snapshot(limit=1,offset=0)
print(json.dumps({k:result[k] for k in ('stage','release_version','release_generation','global_order')} |
    {'queue_ids':[row['id'] for row in result['queues']]}))
"""


def local_projection(release):
    r=subprocess.run([str(release/'venv/bin/python'),'-I','-B','-c',LOCAL_WORKER,str(release)],
                     cwd=release,env={'PATH':'/usr/bin:/bin',**LOCAL_ENV},check=True,stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL,timeout=6)
    if len(r.stdout)>2048:
        raise ValueError('oversize_local_graph_projection')
    return json.loads(r.stdout)


def local_qualification(release, expected, *, identity=serving_identity,
                        denial=local_wrapper_denial, project=local_projection):
    before=identity(release)
    denial()
    doc=project(release)
    tag,generation=expected
    if (type(doc) is not dict or doc!={'stage':'test','release_version':tag,
            'release_generation':generation,'global_order':False,
            'queue_ids':['source','maintenance','interactive']} or identity(release)!=before):
        raise ValueError('wrong_local_sites_graph_generation')


def service_command(action):
    if action not in ('restart','stop'):
        raise ValueError('invalid_canonical_graph_unit_action')
    return ['/usr/bin/sudo','-n','--','/usr/bin/systemctl',action,UNIT]


def run(action,release):
    if pwd.getpwuid(os.getuid()).pw_name!=ACCOUNT or os.geteuid()!=os.getuid() or action not in ACTIONS:
        raise ValueError('wrong_test_graph_gate_identity')
    if action=='stop':
        # First activation rollback has already restored an absent current.
        if (release.parent!=ROOT/'releases' or not ARCHIVE.fullmatch(release.name)
                or os.path.lexists(ROOT/'current')):
            raise ValueError('unexpected_first_activation_rollback')
    else:
        expected=expected_identity(release)
    if action in ('restart','stop'):
        subprocess.run(service_command(action),env={'PATH':'/usr/bin:/bin'},check=True,
                       stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
        return
    subprocess.run(['/usr/bin/systemctl','is-active','--quiet',UNIT],
                   env={'PATH':'/usr/bin:/bin'},check=True,stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL,timeout=5)
    config=json.loads(checked_read(CONFIG,4096,uid=0))
    if config=={'version':1,'mode':'local_candidate_owner_pending'} and type(config['version']) is int:
        local_qualification(release,expected)
        if expected_identity(release)!=expected:
            raise ValueError('canonical_sites_graph_release_changed')
        return 'local qualification verified; owner Sites acceptance still pending'
    if type(config) is not dict or set(config)!={'gateway_host','token_file'}:
        raise ValueError('invalid_canonical_graph_gate_config')
    token_path=Path(config['token_file'])
    if (not token_path.is_absolute() or token_path.resolve(strict=True)!=token_path
            or token_path.parent!=ROOT/'state'):
        raise ValueError('invalid_canonical_graph_token_path')
    # Operator points to the existing TEST Graph read secret, not a general
    # token, not another lane's token, never passed as argv or printed.
    token=checked_read(token_path,130,uid=os.getuid(),private=True).decode('ascii').strip()
    proof(gateway_check(config['gateway_host'],token),expected)
    if expected_identity(release)!=expected:
        raise ValueError('canonical_sites_graph_release_changed')


def main():
    try:
        if len(sys.argv)!=3:
            raise ValueError('invalid_graph_gate_invocation')
        state=run(sys.argv[1],Path(sys.argv[2]))
    except (ValueError,OSError,TypeError,subprocess.SubprocessError,urllib.error.URLError):
        print('Canonical Sites TEST Graph proof failed.',file=sys.stderr)
        return 1
    print('Canonical Sites TEST Graph '+(state or sys.argv[1]+' verified')+'.')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
