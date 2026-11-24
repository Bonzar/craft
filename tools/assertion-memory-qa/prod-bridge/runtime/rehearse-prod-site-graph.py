"""Offline synthetic rehearsal for the staged production Sites graph reader."""
import ast, hashlib, io, json, os, pathlib, pwd, re, secrets, stat
import subprocess as sp
import sys, tarfile, tempfile, time, tokenize
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

P=pathlib.Path
NEW='assertion-memory-prod-site-graph'
USER='assertion-production-deploy'
APP=P('/opt')/NEW/'start.py'
DATA=P('/var/lib')/NEW
DB=P('/var/lib/assertion-memory/memory.sqlite3')
SOURCE='/opt/assertion-memory-qa/source'
PYTHON='/opt/assertion-memory-qa/.venv/bin/python'
ARCHIVE='1236eceb8f63d4fa9b096453c6f6debc21aecc88cc144cb97052dd9722c463d6'
DEVNULL=sp.DEVNULL

def need(ok):
    if not ok: raise ValueError('synthetic adapter invariant')

def rootfile(path):
    need(path.is_absolute() and path.resolve()==path)
    f=os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb')
    m=os.fstat(f.fileno())
    try: need(stat.S_ISREG(m.st_mode) and m.st_uid==0 and not m.st_mode&0o022)
    except Exception: f.close(); raise
    return f

def retarget(body,directory):
    source=body.decode('utf-8')
    ast.parse(source)
    counts={str(DATA):0,str(DB):0}
    replacements={str(DATA):str(directory/'adapter'),str(DB):str(directory/'synthetic.sqlite3')}
    tokens=[]
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        new=tok.string
        if tok.type==tokenize.STRING:
            try: val=ast.literal_eval(new)
            except (ValueError,SyntaxError): val=None
            if isinstance(val,str) and val in replacements:
                counts[val]+=1
                new=repr(replacements[val])
        tokens.append((tok.type,new))
    need(set(counts.values())=={1})
    result=tokenize.untokenize(tokens)
    ast.parse(result)
    return result

def child(argv):
    need(os.geteuid()==pwd.getpwnam(USER).pw_uid!=0)
    need({l.split(':')[0].strip() for l in P('/proc/net/dev').read_text().splitlines()[2:]}=={'lo'})
    root=P.cwd()
    stage='synthetic-fixture'; process=None
    try:
        env=dict(os.environ,PYTHONPATH=SOURCE,PYTHONDONTWRITEBYTECODE='1')
        sp.run([PYTHON,'-B','synthetic_fixture.py',str(root/'synthetic.sqlite3')],
               cwd=root/'release',env=env,stdin=DEVNULL,stdout=DEVNULL,stderr=DEVNULL,timeout=40,check=True)
        stage='adapter-bootstrap'
        process=sp.Popen([PYTHON,'-B',str(root/'staged.py')],cwd=root,env=env,
                         stdin=DEVNULL,stdout=DEVNULL,stderr=DEVNULL)
        stage='adapter-capabilities'
        probe(root,process)
        print('Synthetic isolated Sites adapter capabilities passed',flush=True)
        return 0
    except Exception as e:
        print('Synthetic Sites adapter failed: '+stage+'/'+type(e).__name__,flush=True)
        return 1
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=5)
            except sp.TimeoutExpired: process.kill(); process.wait()

def main():
    need(os.geteuid()==0 and len(sys.argv)==3 and re.fullmatch('[a-f0-9]{64}',sys.argv[2]))
    with rootfile(APP) as f: body=f.read(256001)
    need(len(body)<=256000 and hashlib.sha256(body).hexdigest()==sys.argv[2])
    acct=pwd.getpwnam(USER)
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='prod-sites-synthetic-',dir='/var/tmp') as tmp:
        root=P(tmp)
        (root/'adapter').mkdir(mode=0o700); (root/'adapter/viewer').mkdir(mode=0o700)
        (root/'runner.py').write_bytes(P(__file__).read_bytes())
        (root/'staged.py').write_text(retarget(body,root))
        with rootfile(P(sys.argv[1])) as f:
            need(hashlib.file_digest(f,'sha256').hexdigest()==ARCHIVE)
            f.seek(0)
            with tarfile.open(fileobj=f,mode='r:*') as t:
                for member in t.getmembers():
                    need((member.isfile() or member.isdir()) and not member.sparse
                         and not member.name.startswith('/') and '..' not in P(member.name).parts)
                t.extractall(root/'release',filter='data')
        need((root/'release/synthetic_fixture.py').is_file())
        for name in ('primary','paired'):
            token=secrets.token_hex(32)
            (root/name).write_text(token+'\n')
            digest=hashlib.sha256(token.encode()).hexdigest()
            (root/'adapter'/('sites-digest' if name=='primary' else 'sites-paired-digest')).write_text(digest+'\n')
        for name in ('bridge-internal','owner-local'):
            (root/'adapter'/name).write_text(secrets.token_hex(32)+'\n')
        for p in (root,*root.rglob('*')):
            need(not p.is_symlink())
            os.chown(p,acct.pw_uid,acct.pw_gid,follow_symlinks=False)
            p.chmod(0o700 if p.is_dir() else 0o600)
        launch='set -eu; ip link set lo up; exec runuser -u "$1" -- env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME="$2" PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -I -B "$2/runner.py" --child'
        return sp.run(['/usr/bin/unshare','--net','--pid','--fork','--kill-child','/bin/sh','-c',launch,
                       'sh',USER,str(root)],cwd=root,stdin=DEVNULL,stderr=DEVNULL,timeout=100).returncode

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ValueError('unexpected redirect')

def probe(root,process):
    client=build_opener(ProxyHandler({}),NoRedirect())
    primary=(root/'primary').read_text().strip()
    paired=(root/'paired').read_text().strip()
    body=b'{"path":"/api/v1/capabilities"}'
    def request(path,token=None):
        headers={'Content-Type':'application/json'}
        if token: headers['Authorization']='Bearer '+token
        try:
            with client.open(Request('http://127.0.0.1:8918'+path,body,headers),timeout=3) as res:
                data=res.read(65537)
                need(len(data)<=65536)
                return res.status,data
        except HTTPError as e:
            e.close()
            return e.code,b''
    path='/memory-graph-read'
    for n in range(20):
        need(process.poll() is None)
        try: status,data=request(path,paired)
        except OSError:
            if n==19: raise
            time.sleep(.5)
        else: break
    need(status==200)
    for token in (paired,primary):
        status,data=request(path,token)
        result=json.loads(data)
        need(status==200 and type(result['capabilities']['live']) is bool
             and isinstance(result['readiness'],list))
    need(request(path)[0]==401 and request(path,secrets.token_hex(32))[0]==401
         and request('/memory-graph-test-read',paired)[0]==404)

if __name__=='__main__':
    try: sys.exit(child(sys.argv[1:]) if sys.argv[1:]==['--child'] else main())
    except Exception as e:
        raise SystemExit('Synthetic Sites preflight refused: '+type(e).__name__) from None
