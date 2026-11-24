"""Install only a dormant dedicated unit and restricted graph configuration."""
import hashlib, os, pathlib, re, secrets, stat
P=pathlib.Path
APP=P('/opt/assertion-memory-site-graph/start.py')
APP_SHA='ddd52ae6332ef3a7478e0bb88e101d9d30e95f8fdd198598fdac4a0c5a4db2c4'
NEW='assertion-memory-prod-site-graph'
USER='assertion-production-deploy'
DB=P('/var/lib/assertion-memory/memory.sqlite3')
VIEW=P('/var/lib')/NEW/'viewer'
UNIT=P('/etc/systemd/system')/(NEW+'.service')

def require(ok):
    if not ok: raise ValueError('unsafe or unexpected adapter staging input')

def read_root(path,maxsize,owners=(0,)):
    require(path.is_absolute() and path.resolve()==path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        st=os.fstat(f.fileno())
        require(stat.S_ISREG(st.st_mode) and st.st_uid in owners and not st.st_mode&0o022 and st.st_size<=maxsize)
        result=f.read(maxsize+1)
    require(len(result)<=maxsize)
    return result

def put_new(path,body,mode):
    require(path.parent.resolve()==path.parent and path.parent.stat().st_uid==0
            and not path.parent.stat().st_mode&0o022)
    # No mutable destination will be replaced. Stage+fsync before publish.
    tmp=path.parent/('.site-graph-stage-'+secrets.token_hex(16))
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp,path,follow_symlinks=False)
    finally:
        tmp.unlink()

OLD_DATA=P('/var/lib/assertion-memory-site-graph')
NEW_DATA=VIEW.parent
OLD_UNIT=P('/etc/systemd/system/memory-site-graph.service')
INTERPRETER='/opt/assertion-memory-qa/.venv/bin/python'
SOURCE=P('/opt/assertion-memory-qa/source')
NEW_DIR=P('/opt')/NEW
NEW_APP=NEW_DIR/'start.py'

def modify_source(body):
    import ast, io, tokenize
    source=body.decode('utf-8')
    ast.parse(source)
    tokens=[]; base=port=host=0
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        new=tok.string
        if tok.type==tokenize.STRING:
            try: val=ast.literal_eval(new)
            except (ValueError,SyntaxError): val=None
            if val==str(OLD_DATA):
                new=repr(str(NEW_DATA)); base+=1
            elif val==b'127.0.0.1:8818':
                new=repr(b'127.0.0.1:8918'); host+=1
        elif tok.type==tokenize.NUMBER and new=='8818':
            new='8918'; port+=1
        tokens.append((tok.type,new))
    require(base==port==host==1)
    out=tokenize.untokenize(tokens)
    ast.parse(out)
    return out.encode('utf-8')

def digests(blob):
    # Preserve scoped auth bytes exactly; never copy old plaintext credentials.
    require(re.fullmatch(rb'[0-9a-f]{64}\n?',blob))
    return blob

def adapter_unit():
    return f"""[Unit]
Description=Dedicated production Sites graph adapter
After=network.target

[Service]
Type=simple
User={USER}
Group={USER}
UMask=0077
WorkingDirectory={NEW_DIR}
Environment=PYTHONPATH={SOURCE}
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart={INTERPRETER} -B {NEW_APP}
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ReadWritePaths={VIEW}
ReadOnlyPaths={DB.parent}
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
IPAddressDeny=any
IPAddressAllow=localhost
Restart=no
""".encode('ascii')

def main():
    import argparse, grp, pwd
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage-only',action='store_true',required=True)
    args=p.parse_args()
    require(os.geteuid()==0 and args.stage_only)
    os.umask(0o077)
    body=read_root(APP,256000)
    require(hashlib.sha256(body).hexdigest()==APP_SHA)
    require(P(INTERPRETER).resolve().stat().st_uid==0 and SOURCE.resolve()==SOURCE and SOURCE.stat().st_uid==0)
    old=read_root(OLD_UNIT,16384).decode('ascii')
    require('ProtectSystem=strict' in old and
            'ReadOnlyPaths='+str(DB.parent) in old and
            'ReadWritePaths='+str(OLD_DATA/'viewer') in old and
            'ExecStart='+INTERPRETER+' '+str(APP) in old)
    new=modify_source(body)
    old_uid=pwd.getpwnam('assertion-memory-qa').pw_uid
    auth={name:digests(read_root(OLD_DATA/name,8192,(0,old_uid)))
          for name in ('sites-digest','sites-paired-digest')}
    acct=pwd.getpwnam(USER)
    require(acct.pw_gid==grp.getgrnam(USER).gr_gid)
    require(all(not x.exists() and not x.is_symlink() for x in (NEW_DIR,NEW_DATA,UNIT)))
    added=[]
    try:
        for directory in (NEW_DIR,NEW_DATA,VIEW):
            directory.mkdir(mode=0o700); added.append(directory)
        NEW_DIR.chmod(0o755)
        os.chown(NEW_DATA,0,acct.pw_gid,follow_symlinks=False)
        NEW_DATA.chmod(0o750)
        os.chown(VIEW,acct.pw_uid,acct.pw_gid,follow_symlinks=False)
        for name in ('bridge-internal','owner-local'):
            auth[name]=(secrets.token_hex(32)+'\n').encode('ascii')
        for name,value in auth.items():
            path=NEW_DATA/name
            put_new(path,value,0o640); added.append(path)
            os.chown(path,0,acct.pw_gid,follow_symlinks=False)
            path.chmod(0o640)
        put_new(NEW_APP,new,0o644); added.append(NEW_APP)
        NEW_APP.chmod(0o644)
        put_new(UNIT,adapter_unit(),0o644); added.append(UNIT)
        UNIT.chmod(0o644)
    except Exception:
        for path in reversed(added):
            if path in (NEW_DIR,NEW_DATA,VIEW): path.rmdir()
            else: path.unlink()
        raise
    print('Production Sites graph unit staged dormant; no service, route or database change.')

if __name__=='__main__':
    try: main()
    except Exception as e:
        raise SystemExit('Production Sites graph staging refused: '+type(e).__name__) from None
