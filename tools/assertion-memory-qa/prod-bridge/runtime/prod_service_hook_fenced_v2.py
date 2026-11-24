"""Production-only service hook. The existing release journal owns pointer rollback."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from urllib.error import HTTPError

ROOT = Path('/srv/assertion-release/production')
POLICY = Path('/etc/assertion-release/production/application-policy.json')
UNITS = ('assertion-production-deploy-mcp.service', 'assertion-production-deploy-graph.service')
USER = 'assertion-production-deploy'
RELEASE = re.compile(r'[a-f0-9]{40}-[a-f0-9]{64}')
FIRST_RELEASE = (
    'f6e76c92752ece77318286d2c1d63ed5d4f95b84-'
    '1236eceb8f63d4fa9b096453c6f6debc21aecc88cc144cb97052dd9722c463d6'
)
FIRST_GUARD = Path('/usr/local/sbin/assertion-base-prod-transition')
FIRST_COMPLETED = Path('/var/lib/assertion-base-prod-status/settled.json')
HTTP_PERMIT = re.compile(r'[A-Za-z0-9._~+/-]{24,512}={0,2}')


def need(condition, message):
    if not condition:
        raise ValueError(message)


def checked_file(path, *, owner, limit, private=False):
    path = Path(path)
    need(path.is_absolute() and path.resolve() == path, 'Unsafe file path')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        meta = os.fstat(f.fileno())
        need(stat.S_ISREG(meta.st_mode) and meta.st_uid == owner and not (meta.st_mode & 0o022) and (not private or not (meta.st_mode & 0o007)),
             'Unsafe file metadata')
        buf = f.read(limit+1)
    need(len(buf) <= limit, 'File exceeds expected bound')
    return buf


@dataclass(frozen=True)
class Policy:
    database: str
    db_dev: int
    db_inode: int
    mcp_config: str
    mcp_token: str
    graph_password: str
    mcp_port: int
    graph_port: int
    services_ownership_attested: bool
    legacy_system_units: list[str]

    def check(self):
        need(self.services_ownership_attested is True, 'Independent production owner handoff unconfirmed')
        need(len(self.legacy_system_units)==2 and len(set(self.legacy_system_units))==2 and
             all(re.fullmatch(r'[A-Za-z0-9_.@-]{1,100}\.service', n) for n in self.legacy_system_units) and
             not (set(self.legacy_system_units)&set(UNITS)), 'Legacy service ownership must be explicit')
        need(type(self.db_dev) is int and self.db_dev >= 0 and
             type(self.db_inode) is int and self.db_inode > 0, 'Database identity missing')
        for p in (self.database, self.mcp_config, self.mcp_token, self.graph_password):
            pp=Path(p)
            need(pp.is_absolute() and pp.resolve() == pp and
                 not any(k in pp.parts for k in ('test', 'qa')) and not
                 any('synthetic' in k or 'fictional' in k for k in pp.parts), 'Unsafe production path')
        need(all(Path(p).is_relative_to(ROOT/'state') for p in
                 (self.mcp_config,self.mcp_token,self.graph_password)), 'Config is not release-scoped')
        need(len({self.database,self.mcp_config,self.mcp_token,self.graph_password}) == 4,
             'Unexpected aliased production files')
        need(all(type(n) is int and 1024 <= n <= 65535 for n in
                 (self.mcp_port,self.graph_port)) and (self.mcp_port,self.graph_port)==(8903,8901), 'Unsafe production ports')
        return self

    def check_database(self):
        e=os.stat(self.database,follow_symlinks=False)
        need(stat.S_ISREG(e.st_mode) and (e.st_dev,e.st_ino)==(self.db_dev,self.db_inode)
             and not (e.st_mode&0o002),'Live production DB does not match approved identity')


def selection(cmd, flag):
    found=[i for i,x in enumerate(cmd) if x==flag]
    need(len(found)==1 and found[0]+1<len(cmd),'Missing or ambiguous endpoint configuration')
    return cmd[found[0]+1]


def check_process(policy, role, argv, cwd, release, uid, expect_uid, config=None):
    need(uid==expect_uid and cwd==release, 'Unexpected production PID scope or release')
    need(argv and Path(argv[0]) in (release/'venv/bin/python',ROOT/'current/venv/bin/python'),
         'Unexpected production interpreter')
    need('--synthetic-fixture' not in argv,'Synthetic application configured for production')
    module='prototypes.assertion_memory.http_server' if role=='mcp' else 'apps.graph_viewer.server'
    need(selection(argv,'-m')==module,'Wrong production process module')
    if role=='mcp':
        need(selection(argv,'--config')==policy.mcp_config and isinstance(config,dict) and
             isinstance(config.get('public_host'),str) and bool(config['public_host']) and
             config.get('port')==policy.mcp_port and config.get('token_file')==policy.mcp_token and
             config.get('service',{}).get('db')==policy.database,'Wrong production MCP state')
    else:
        need(selection(argv,'--source')==policy.database and
             selection(argv,'--password-file')==policy.graph_password and
             selection(argv,'--port')==str(policy.graph_port) and
             selection(argv,'--state-dir')==str(ROOT/'state/graph-state') and
             Path(selection(argv,'--static-dir')) in (release/'apps/graph_viewer/web/dist',
                 ROOT/'current/apps/graph_viewer/web/dist'),
             'Wrong production graph state')


def check_listener(ss, port, pid):
    lines=[x for x in ss.splitlines() if re.search(r'(?<![0-9])127\.0\.0\.1:'+str(port)+r'\s',x)]
    need(len(lines)==1 and bool(re.search(r'\bpid='+str(pid)+r'\b',lines[0])),
         'Wrong production listener owner or port')
    other=[x for x in ss.splitlines() if re.search(r':'+str(port)+r'\s',x) and x not in lines]
    need(not other,'Unexpected additional listener on scoped production port')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ValueError('Unexpected redirect during production smoke')


class Smoke:
    def __init__(self, policy):
        self.policy=policy
        self.client=build_opener(ProxyHandler({}),NoRedirect())

    def request(self,port,path,*,body=None,headers=None):
        # Fixed loopback, fixed request paths; never follow redirects or log responses.
        req=Request('http://127.0.0.1:'+str(port)+path,data=body,headers=headers or {})
        try:
            with self.client.open(req,timeout=4) as res:
                data=res.read(65537)
                need(len(data)<=65536,'Production smoke response too large')
                return res.status,data
        except HTTPError as e:
            e.close()
            return e.code,b''

    def run(self, token, graph_password):
        need(bool(HTTP_PERMIT.fullmatch(token)) and len(graph_password)>=24 and token != graph_password,
             'Invalid or shared production credentials')
        body=b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
        hdr={'Accept':'application/json, text/event-stream','Content-Type':'application/json',
             'MCP-Protocol-Version':'2025-11-25'}
        need(self.request(self.policy.mcp_port,'/memory/mcp',body=body,headers=hdr)[0]==401,
             'Production MCP is not auth-gated')
        status,data=self.request(self.policy.mcp_port,'/memory/mcp',body=body,headers={**hdr,'Authorization':'Bearer '+token})
        need(status==200 and bool(json.loads(data).get('result',{}).get('tools')),
             'Production MCP read-only tool discovery failed')
        need(self.request(self.policy.graph_port,'/api/v1/graph')[0]==401,
             'Production graph is not auth-gated')
        # Login establishes a short-lived session; no graph snapshot or personal record is read.
        status,_=self.request(self.policy.graph_port,'/api/v1/session',body=json.dumps({'password':graph_password}).encode(),
                              headers={'Content-Type':'application/json'})
        need(status==200,'Production graph credential check failed')

def run_command(cmd, cwd=None, timeout=30):
    # No inherited HOME override, shell or unconstrained PATH from the deployment payload.
    return subprocess.run(cmd,cwd=cwd,stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=timeout,
        check=True).stdout.decode(errors='strict')


def valid_release(release):
    release=Path(release)
    need(release.parent==ROOT/'releases' and RELEASE.fullmatch(release.name) and
         release.resolve()==release and release.is_dir(),'Invalid production release path')
    return release


def systemd(action, *units):
    return run_command(['/usr/bin/systemctl','--user',action,*units])


def require_legacy_quiesced(policy, runner=run_command):
    for unit in policy.legacy_system_units:
        info=runner(['/usr/bin/systemctl','show','--property=MainPID,ActiveState,LoadState',unit],timeout=5)
        values=dict(line.split('=',1) for line in info.splitlines())
        need(values.get('LoadState')=='loaded' and values.get('MainPID')=='0'
             and values.get('ActiveState') in ('inactive','failed'), 'Existing production process is not quiesced')


def first_maintenance_complete(policy):
    try:
        observed=FIRST_COMPLETED.lstat()
    except FileNotFoundError:
        return False
    need(observed.st_uid==0 and observed.st_gid==0 and observed.st_nlink==1
         and stat.S_ISREG(observed.st_mode)
         and stat.S_IMODE(observed.st_mode)==0o644,
         'Unsafe permanent production completion marker')
    marker=json.loads(checked_file(FIRST_COMPLETED,owner=0,limit=1024))
    need(isinstance(marker,dict) and set(marker)=={'version','state','inode'}
         and type(marker['version']) is int and isinstance(marker['inode'],list)
         and all(type(n) is int for n in marker['inode'])
         and marker=={'version':1,'state':'settled','inode':[policy.db_dev,policy.db_inode]},
         'Invalid permanent production completion marker')
    return True

def start_services(action,release,policy):
    need(action in ('restart','restore'),'Unexpected activation phase')
    if first_maintenance_complete(policy):
        method='receiver-restart-after-settlement'
    else:
        # Neither a current pointer nor a journal is a permit.
        need(action=='restart' and release.name==FIRST_RELEASE,
             'First activation not approved for this release or action')
        method='receiver-first-start'
    # The guard validates either marker and holds the shared kernel lock.
    for directory in (FIRST_GUARD.parent,FIRST_GUARD.parent.parent):
        entry=directory.lstat()
        need(directory.resolve()==directory and stat.S_ISDIR(entry.st_mode)
             and entry.st_uid==0 and not entry.st_mode&0o022,'Unsafe first-activation guard parent')
    entry=FIRST_GUARD.lstat()
    need(entry.st_uid==0 and entry.st_gid==0 and entry.st_nlink==1
         and stat.S_ISREG(entry.st_mode) and stat.S_IMODE(entry.st_mode) in (0o555,0o755),
         'Unsafe first-activation guard')
    run_command([str(FIRST_GUARD),method],timeout=45)


def trusted_unit_fragment(unit, values):
    require_path=Path('/etc/systemd/user')/unit
    alias=Path('/etc/xdg/systemd/user')/unit
    fragment=values.get('FragmentPath')
    need(fragment in (str(require_path),str(alias)) and values.get('DropInPaths') in ('',None),
         'Production unit is not the exact root-managed definition')
    actual=Path(fragment)
    expected=require_path.resolve(strict=True)
    need(expected==require_path and actual.resolve(strict=True)==expected,
         'Untrusted production unit alias')
    for directory in (require_path.parent,alias.parent if actual==alias else require_path.parent,expected.parent):
        mode=directory.stat()
        need(mode.st_uid==0 and stat.S_ISDIR(mode.st_mode) and not mode.st_mode&0o022,
             'Unsafe production unit directory')
    checked_file(expected,owner=0,limit=16384)
    a=actual.stat()
    b=expected.stat()
    need((a.st_dev,a.st_ino)==(b.st_dev,b.st_ino) and a.st_uid==0 and b.st_uid==0
         and stat.S_ISREG(a.st_mode) and not a.st_mode&0o022,
         'Production unit identity differs')


def check_live(policy, release, uid):
    need((ROOT/'current').is_symlink() and (ROOT/'current').resolve()==release,
         'Production pointer does not identify the intended release')
    pids=[]
    config=json.loads(checked_file(policy.mcp_config,owner=0,limit=8192,private=True))
    ss=run_command(['/usr/bin/ss','-H','-ltnp'],timeout=5)
    for i,role in enumerate(('mcp','graph')):
        meta=run_command(['/usr/bin/systemctl','--user','show','--property=MainPID,ActiveState,FragmentPath,DropInPaths',UNITS[i]],timeout=5)
        values=dict(line.split('=',1) for line in meta.splitlines())
        trusted_unit_fragment(UNITS[i],values)
        need(values.get('ActiveState')=='active' and
             bool(re.fullmatch(r'[1-9][0-9]{0,9}',values.get('MainPID',''))),'Production unit not active')
        pid=int(values['MainPID']); proc=Path('/proc')/str(pid)
        argv=proc.joinpath('cmdline').read_bytes().decode().rstrip(chr(0)).split(chr(0))
        check_process(policy,role,argv,proc.joinpath('cwd').resolve(),release,proc.stat().st_uid,uid,config)
        check_listener(ss,(policy.mcp_port,policy.graph_port)[i],pid)
        pids.append(pid)
    need(pids[0]!=pids[1],'Production unit process identities are not distinct')
    return tuple(pids)


def run(action, release):
    import pwd
    import tempfile
    import time
    uid=os.geteuid()
    need(uid!=0 and uid==os.getuid() and pwd.getpwuid(uid).pw_name==USER,
         'Incorrect production deployment principal')
    release=valid_release(release)
    if action=='stop':
        systemd('stop',*UNITS)
        return
    policy=Policy(**json.loads(checked_file(POLICY,owner=0,limit=4096,private=True))).check()
    policy.check_database()
    if action=='prepare':
        # Only trusted locally provisioned wheel artifacts; no internet or package mutation.
        wheels=Path('/etc/assertion-release/production/wheels')
        pin=wheels/'locked-requirements.txt'
        requirements=checked_file(pin,owner=0,limit=128000).decode().splitlines()
        need(bool(requirements) and all(re.fullmatch(r'[A-Za-z0-9_.-]+==[A-Za-z0-9_.+-]+(?: --hash=sha256:[a-f0-9]{64})+', x)
             for x in requirements), 'Offline wheel lock must contain only complete pinned dependencies')
        need(wheels.resolve()==wheels and wheels.stat().st_uid==0 and
             not (wheels.stat().st_mode&0o022),'Unsafe production wheelhouse')
        run_command(['/usr/bin/python3','-m','venv',str(release/'venv')],cwd=release)
        py=str(release/'venv/bin/python')
        run_command([py,'-m','pip','--isolated','install','--no-index','--find-links',str(wheels),
                     '--disable-pip-version-check','--no-cache-dir','--no-deps','--require-hashes','-r',str(pin)],cwd=release,timeout=110)
        with tempfile.TemporaryDirectory(prefix='.preflight-',dir=ROOT) as d:
            fixture=str(Path(d)/'synthetic-smoke.sqlite3')
            run_command([py,'-B','synthetic_fixture.py',fixture],cwd=release)
            run_command([py,'-B','smoke_local.py',fixture],cwd=release)
    elif action in ('restart','restore'):
        need((ROOT/'current').is_symlink() and (ROOT/'current').resolve()==release,
             'Production restart points at the wrong release')
        require_legacy_quiesced(policy)
        start_services(action,release,policy)
    elif action=='smoke':
        for _ in range(5):
            try:
                require_legacy_quiesced(policy)
                before=check_live(policy,release,uid)
                token=checked_file(policy.mcp_token,owner=uid,limit=512,private=True).decode().strip()
                password=checked_file(policy.graph_password,owner=uid,limit=512,private=True).decode().strip()
                Smoke(policy).run(token,password)
                policy.check_database()
                need(check_live(policy,release,uid)==before,'Production processes changed during smoke')
                return
            except (ValueError,OSError,subprocess.CalledProcessError):
                time.sleep(1)
        raise ValueError('Bounded production smoke failed')
    else:
        raise ValueError('Unknown production hook action')


if __name__=='__main__':
    import sys
    try:
        action,release=sys.argv[1:]
        run(action,release)
    except Exception:
        raise SystemExit('Scoped base production hook failed closed') from None
