"""Reproduce an exact production delayed listener on isolated synthetic data."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import stat
import subprocess as sp
import sys
import tarfile
import tempfile
import time

ARCHIVE = "1236eceb8f63d4fa9b096453c6f6debc21aecc88cc144cb97052dd9722c463d6"
NEW_HASH='f5ec913c93763dd4b7baa0fb3fe53efd0f74fb984cc051d1301740b6e9d35f90'
HOOK_HASH='477408b14cd441635dc7a3daafd9018b01068cfbd725e93e89cb2f69f3ec8f74'
HOOK=Path('/etc/assertion-release/production/prod_service_hook.py')
WHEELS = "/etc/assertion-release/production/wheels"
USER = "assertion-production-deploy"
DEVNULL = sp.DEVNULL

def require(value):
    if not value:
        raise ValueError("synthetic rehearsal invariant")

def command(args, cwd, timeout=120):
    sp.run(args, cwd=cwd, stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL,
           timeout=timeout, check=True)

def smoke():
    require(os.geteuid() == pwd.getpwnam(USER).pw_uid != 0)
    require({l.split(":")[0].strip() for l in Path("/proc/net/dev").read_text().splitlines()[2:]} == {"lo"})
    root=Path.cwd()
    stage='hook-import'
    def import_hook(name):
        spec=importlib.util.spec_from_file_location(name,root/(name+'.py'))
        hook=importlib.util.module_from_spec(spec)
        sys.modules[spec.name]=hook
        spec.loader.exec_module(hook)
        hook.ROOT=root
        return hook
    hook=import_hook('_exact_prod_hook')
    candidate=import_hook('_cold_prod_hook')
    release=root/'releases'/('f6e76c92752ece77318286d2c1d63ed5d4f95b84-'+ARCHIVE)
    py=str(root/'current/venv/bin/python')
    db=root/'synthetic.sqlite3'
    public_host=sys.argv[2]
    token,password=secrets.token_hex(32),secrets.token_hex(32)
    for name,value in (('token',token),('password',password)):
        (root/name).write_text(value+'\n')
    # Initial root-owned config template already written before dropping root.
    cfg=json.loads((root/'http.json').read_bytes())
    require(cfg['public_host']==public_host and cfg['token_file']==str(root/'token'))
    stage = "venv"
    procs = []
    try:
        command(["/usr/bin/python3","-m","venv","venv"],release,45)
        stage = "locked-install"
        command([py,"-m","pip","--isolated","install","--no-index","--find-links",WHEELS,
                 "--disable-pip-version-check","--no-cache-dir","--no-deps","--require-hashes",
                 "-r",WHEELS+"/locked-requirements.txt"],release)
        stage = "synthetic-fixture"
        command([py,"-B","synthetic_fixture.py",str(db)],release,30)
        command([py,"-B","smoke_local.py",str(db)],release,30)
        stage = "start-loopback"
        starts = ([py,"-B","-m","prototypes.assertion_memory.http_server","--config",str(root/"http.json")],
                  [py,"-B","-m","apps.graph_viewer.server","--host","127.0.0.1","--port","8901",
                   "--source",str(db),"--state-dir",str(root/"state/graph-state"),"--password-file",str(root/"password"),
                   "--static-dir",str(root/"current/apps/graph_viewer/web/dist")])
        delay=root/'injected-cold'
        delay.mkdir()
        (delay/'sitecustomize.py').write_text("import time\ntime.sleep(8.0)\n")
        started=time.monotonic()
        for i,args in enumerate(starts):
            env=dict(os.environ)
            if i==1: env['PYTHONPATH']=str(delay)
            procs.append(sp.Popen(args,cwd=root/'current',env=env,
                                 stdin=DEVNULL,stdout=DEVNULL,stderr=DEVNULL))
        meta=db.stat()
        policy=hook.Policy(database=str(db),db_dev=meta.st_dev,db_inode=meta.st_ino,
          mcp_config=str(root/'http.json'),mcp_token=str(root/'token'),graph_password=str(root/'password'),
          mcp_port=8903,graph_port=8901,services_ownership_attested=False,legacy_system_units=[])
        # Replay the installed manager metadata contract only. /proc, ss and HTTP
        # still come from the real fixture processes under the real scoped UID.
        for module in (hook,candidate):
            original=module.run_command
            def metadata(args,*a,_real=original,_module=module,**kw):
                if args[:3]==['/usr/bin/systemctl','--user','show']:
                    i=_module.UNITS.index(args[-1]); process=procs[i]
                    require(process.poll() is None)
                    return 'MainPID='+str(process.pid)+'\nActiveState=active\nFragmentPath=/etc/xdg/systemd/user/'+args[-1]+'\nDropInPaths=\n'
                return _real(args,*a,**kw)
            module.run_command=metadata
        # Match the old five listener polls and same real state. Never retry
        # non-listener predicates in the test or let a different error count.
        stage='old-five-listener-polls'
        for _ in range(5):
            try: hook.check_live(policy,release,os.geteuid())
            except ValueError as e:
                require(str(e)=='Wrong production listener owner or port')
                time.sleep(1)
            else: raise ValueError('old policy unexpectedly passed before delay')
        require(time.monotonic()-started<8.0 and all(p.poll() is None for p in procs))
        # Supply only a scratch timing deadline (production root permit path is
        # never opened); installed helper checks are verified independently.
        stage='new-exact-ready-only-wait'
        candidate.startup_deadline=lambda policy,now: now+candidate.APP_START_WAIT
        identity=candidate.wait_until_listening(policy,release,os.geteuid())
        stage='new-exact-auth-smoke'
        candidate.Smoke(policy).run(token,password)
        policy.check_database()
        require(candidate.check_live(policy,release,os.geteuid())==identity)
        elapsed=round(time.monotonic()-started,1)
        require(elapsed>=8)
        print('Old five-poll reproduced; new exact missing-listener wait and four auth checks passed, elapsed seconds='+str(elapsed),flush=True)
    except Exception as e:
        stages={
          'Production MCP read-only tool discovery failed':'mcp-tools',
          'Production MCP is not auth-gated':'mcp-auth-negative',
          'Production graph is not auth-gated':'graph-auth-negative',
          'Production graph credential check failed':'graph-auth-positive',
          'Unexpected production PID scope or release':'proc-cwd-uid',
          'Unexpected production interpreter':'proc-interpreter',
          'Wrong production process module':'proc-module',
          'Wrong production MCP state':'proc-mcp-state',
          'Wrong production graph state':'proc-graph-state',
          'Wrong production listener owner or port':'ss-pid-port',
          'Unexpected additional listener on scoped production port':'ss-duplicate',
          'Production unit not active':'systemd-state',
          'Production unit is not the exact root-managed definition':'systemd-fragment',
          'Untrusted production unit alias':'systemd-alias',
          'Unsafe production unit directory':'systemd-root-dir',
          'Production unit identity differs':'systemd-inode',
          'Unsafe file metadata':'checked-file-metadata',
          'Production pointer does not identify the intended release':'pointer'
        }
        print("synthetic runtime failed: "+stage+"/"+stages.get(str(e),type(e).__name__),flush=True)
        return 1
    finally:
        for p in procs:
            if p.poll() is None: p.terminate()
        for p in procs:
            try: p.wait(timeout=5)
            except sp.TimeoutExpired: p.kill();p.wait()
    return 0

def root_main():
    require(os.geteuid()==0 and len(sys.argv)==4)
    import re
    require(re.fullmatch(r'[A-Za-z0-9.-]{1,253}(?::[0-9]{1,5})?',sys.argv[2]))
    archive=Path(sys.argv[1])
    require(archive.is_absolute() and archive.resolve()==archive)
    acct=pwd.getpwnam(USER)
    with tempfile.TemporaryDirectory(prefix="prod-synthetic-",dir="/var/tmp") as temp:
        root=Path(temp)
        release=root/"releases"/("f6e76c92752ece77318286d2c1d63ed5d4f95b84-"+ARCHIVE)
        release.mkdir(parents=True)
        (root/"state/graph-state").mkdir(parents=True)
        with os.fdopen(os.open(archive,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),"rb") as f:
            m=os.fstat(f.fileno())
            require(stat.S_ISREG(m.st_mode) and m.st_uid==0 and not m.st_mode&0o022)
            require(hashlib.file_digest(f,"sha256").hexdigest()==ARCHIVE)
            f.seek(0)
            with tarfile.open(fileobj=f,mode="r:*") as t:
                for entry in t.getmembers():
                    require((entry.isfile() or entry.isdir()) and not entry.sparse and
                            not entry.name.startswith("/") and ".." not in Path(entry.name).parts)
                t.extractall(release,filter="data")
        require((release/"synthetic_fixture.py").is_file() and (release/"smoke_local.py").is_file()
                and (release/"apps/graph_viewer/web/dist").is_dir())
        for p in (root,*root.rglob('*')):
            require(not p.is_symlink())
            os.chown(p,acct.pw_uid,acct.pw_gid,follow_symlinks=False)
            p.chmod(0o700 if p.is_dir() else 0o600)
        (root/'current').symlink_to(release)
        with os.fdopen(os.open(HOOK,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as f:
            st=os.fstat(f.fileno())
            require(stat.S_ISREG(st.st_mode) and st.st_uid==0 and not st.st_mode&0o022)
            data=f.read(120001)
        require(len(data)<=120000 and hashlib.sha256(data).hexdigest()==HOOK_HASH)
        (root/'_exact_prod_hook.py').write_bytes(data)
        (root/'_exact_prod_hook.py').chmod(0o644)
        source=Path(sys.argv[3])
        require(source.is_absolute() and source.resolve()==source)
        with os.fdopen(os.open(source,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as f:
            st=os.fstat(f.fileno())
            require(stat.S_ISREG(st.st_mode) and st.st_uid==0 and not st.st_mode&0o022)
            new=f.read(120001)
        require(len(new)<=120000 and hashlib.sha256(new).hexdigest()==NEW_HASH)
        (root/'_cold_prod_hook.py').write_bytes(new)
        (root/'_cold_prod_hook.py').chmod(0o644)
        (root/'http.json').write_text(json.dumps({
          "service":{"db":str(root/'synthetic.sqlite3'),"provider":"none","embedding_mode":"lexical","processing_session_ids":[]},
          "token_file":str(root/'token'),"public_host":sys.argv[2],"port":8903}))
        os.chown(root/'http.json',0,acct.pw_gid)
        (root/'http.json').chmod(0o640)
        runner=root/"_isolated_rehearsal.py"
        require(not runner.exists())
        shutil.copyfile(Path(__file__).resolve(),runner)
        os.chown(runner,acct.pw_uid,acct.pw_gid)
        runner.chmod(0o600)
        launcher='set -eu; ip link set lo up; exec runuser -u "$1" -- env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME="$2" /usr/bin/python3 -I -B "$2/_isolated_rehearsal.py" --child "$3"'
        p=sp.run(["/usr/bin/unshare","--net","--pid","--fork","--kill-child","--mount-proc","/bin/sh","-c",launcher,
                  "sh",USER,str(root),sys.argv[2]],cwd=root,stdin=DEVNULL,stderr=DEVNULL,timeout=270)
        return p.returncode

if __name__=="__main__":
    os.umask(0o077)
    try:
        sys.exit(smoke() if len(sys.argv)==3 and sys.argv[1]=="--child" else root_main())
    except Exception as e:
        raise SystemExit("synthetic runtime preflight refused: "+type(e).__name__) from None
