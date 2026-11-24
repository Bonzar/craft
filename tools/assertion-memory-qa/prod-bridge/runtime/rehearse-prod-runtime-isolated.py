"""Exact base release synthetic-only runtime rehearsal; no live state."""
import hashlib
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
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ARCHIVE = "1236eceb8f63d4fa9b096453c6f6debc21aecc88cc144cb97052dd9722c463d6"
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
    root = Path.cwd()
    py = str(root/"venv/bin/python")
    db = root/"synthetic.sqlite3"
    token, password = secrets.token_hex(32), secrets.token_hex(32)
    for name, value in (("token",token),("password",password)):
        (root/name).write_text(value+"\n")
    (root/"http.json").write_text(json.dumps({
        "service":{"db":str(db),"provider":"none","embedding_mode":"lexical","processing_session_ids":[]},
        "token_file":str(root/"token"),"public_host":"synthetic.invalid","port":8903
    }))
    stage = "venv"
    procs = []
    try:
        command(["/usr/bin/python3","-m","venv","venv"],root,45)
        stage = "locked-install"
        command([py,"-m","pip","--isolated","install","--no-index","--find-links",WHEELS,
                 "--disable-pip-version-check","--no-cache-dir","--no-deps","--require-hashes",
                 "-r",WHEELS+"/locked-requirements.txt"],root)
        stage = "synthetic-fixture"
        command([py,"-B","synthetic_fixture.py",str(db)],root,30)
        command([py,"-B","smoke_local.py",str(db)],root,30)
        stage = "start-loopback"
        starts = ([py,"-B","-m","prototypes.assertion_memory.http_server","--config",str(root/"http.json")],
                  [py,"-B","-m","apps.graph_viewer.server","--host","127.0.0.1","--port","8901",
                   "--source",str(db),"--state-dir",str(root/"graph-state"),"--password-file",str(root/"password"),
                   "--static-dir",str(root/"apps/graph_viewer/web/dist")])
        for args in starts:
            procs.append(sp.Popen(args,cwd=root,stdin=DEVNULL,stdout=DEVNULL,stderr=DEVNULL))
        stage = "mcp-and-graph-auth"
        def request(port,path,body=None,headers=None):
            try:
                with urlopen(Request(f"http://127.0.0.1:{port}"+path,body,headers or {}),timeout=2) as r:
                    return r.status,r.read(65537)
            except HTTPError as e:
                e.close()
                return e.code,b""
        body=b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
        hdr={"Accept":"application/json, text/event-stream","Content-Type":"application/json","MCP-Protocol-Version":"2025-11-25"}
        for i in range(25):
            try:
                require(all(p.poll() is None for p in procs))
                require(request(8903,"/memory/mcp",body,hdr)[0]==401)
                status, data=request(8903,"/memory/mcp",body,{**hdr,"Authorization":"Bearer "+token})
                require(status==200 and len(data)<=65536 and json.loads(data).get("result",{}).get("tools"))
                require(request(8901,"/api/v1/graph")[0]==401)
                require(request(8901,"/api/v1/session",json.dumps({"password":password}).encode(),
                                {"Content-Type":"application/json"})[0]==200)
                break
            except (OSError,ValueError):
                if i==24: raise
                time.sleep(.5)
        print("synthetic runtime: MCP+graph auth and loopback passed",flush=True)
    except Exception as e:
        print("synthetic runtime failed: "+stage+"/"+type(e).__name__,flush=True)
        return 1
    finally:
        for p in procs:
            if p.poll() is None: p.terminate()
        for p in procs:
            try: p.wait(timeout=5)
            except sp.TimeoutExpired: p.kill();p.wait()
    return 0

def root_main():
    require(os.geteuid()==0 and len(sys.argv)==2)
    archive=Path(sys.argv[1])
    require(archive.is_absolute() and archive.resolve()==archive)
    acct=pwd.getpwnam(USER)
    with tempfile.TemporaryDirectory(prefix="prod-synthetic-",dir="/var/tmp") as temp:
        root=Path(temp)
        with os.fdopen(os.open(archive,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),"rb") as f:
            m=os.fstat(f.fileno())
            require(stat.S_ISREG(m.st_mode) and m.st_uid==0 and not m.st_mode&0o022)
            require(hashlib.file_digest(f,"sha256").hexdigest()==ARCHIVE)
            f.seek(0)
            with tarfile.open(fileobj=f,mode="r:*") as t:
                for entry in t.getmembers():
                    require((entry.isfile() or entry.isdir()) and not entry.sparse and
                            not entry.name.startswith("/") and ".." not in Path(entry.name).parts)
                t.extractall(root,filter="data")
        require((root/"synthetic_fixture.py").is_file() and (root/"smoke_local.py").is_file()
                and (root/"apps/graph_viewer/web/dist").is_dir())
        runner=root/"_isolated_rehearsal.py"
        require(not runner.exists())
        shutil.copyfile(Path(__file__).resolve(),runner)
        for p in (root,*root.rglob("*")):
            require(not p.is_symlink())
            os.chown(p,acct.pw_uid,acct.pw_gid,follow_symlinks=False)
            p.chmod(0o700 if p.is_dir() else 0o600)
        launcher='set -eu; ip link set lo up; exec runuser -u "$1" -- env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME="$2" /usr/bin/python3 -I -B "$2/_isolated_rehearsal.py" --child'
        p=sp.run(["/usr/bin/unshare","--net","--pid","--fork","--kill-child","/bin/sh","-c",launcher,
                  "sh",USER,str(root)],cwd=root,stdin=DEVNULL,stderr=DEVNULL,timeout=270)
        return p.returncode

if __name__=="__main__":
    os.umask(0o077)
    try:
        sys.exit(smoke() if sys.argv[1:]==["--child"] else root_main())
    except Exception as e:
        raise SystemExit("synthetic runtime preflight refused: "+type(e).__name__) from None
