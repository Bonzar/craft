"""Install only already-rendered unarmed config and new scoped credential files."""
import json
import os
from pathlib import Path
import pwd
import re
import stat
import sys

ETC=Path('/etc/assertion-release/production')
STATE=Path('/srv/assertion-release/production/state')
USER='assertion-production-deploy'
NAMES=('http.json.candidate','application-policy.json.unarmed','app-runtime.env.candidate',
       'mcp-token.new','graph-password.new')


def need(ok):
    if not ok: raise ValueError('Unsafe or already configured unarmed runtime')


def parse(bundle, db_meta):
    need(set(bundle)==set(NAMES))
    cfg=json.loads(bundle[NAMES[0]])
    policy=json.loads(bundle[NAMES[1]])
    db=policy['database']
    need(isinstance(db,str) and re.fullmatch(r'/[A-Za-z0-9_./-]{8,240}[.]sqlite3',db)
         and not any(n in Path(db).parts for n in ('test','qa','..','.'))
         and not any(x in db for x in ('fictional','synthetic')))
    need(set(cfg)=={'service','token_file','public_host','port'}
         and cfg['service']=={'db':db,'provider':'none','embedding_mode':'lexical','processing_session_ids':[]}
         and cfg['port']==8903 and cfg['token_file']==str(STATE/'mcp-token')
         and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,244}(?::[0-9]{1,5})?',cfg['public_host']))
    units=policy.get('legacy_system_units')
    need(isinstance(units,list) and len(units)==2 and len(set(units))==2 and
         all(re.fullmatch(r'[A-Za-z0-9_.@-]{1,100}[.]service',u) and
             not u.startswith('assertion-production-deploy-') for u in units))
    need(policy=={'database':db,'db_dev':db_meta.st_dev,'db_inode':db_meta.st_ino,
                  'mcp_config':str(STATE/'http.json'),'mcp_token':str(STATE/'mcp-token'),
                  'graph_password':str(STATE/'graph-password'),'mcp_port':8903,'graph_port':8901,
                  'services_ownership_attested':False,'legacy_system_units':units}
         and policy['services_ownership_attested'] is False
         and stat.S_ISREG(db_meta.st_mode) and not(db_meta.st_mode&0o002))
    need(bundle[NAMES[2]]==('PROD_CANONICAL_DB='+db+'\n').encode('ascii'))
    a,b=(bundle[n] for n in NAMES[3:])
    need(a!=b and all(re.fullmatch(rb'[0-9a-f]{64}\n',t) for t in (a,b)))
    return db


def private_read(path):
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as f:
        m=os.fstat(f.fileno()); need(m.st_uid==0 and stat.S_ISREG(m.st_mode) and stat.S_IMODE(m.st_mode)==0o600)
        data=f.read(4097)
    need(len(data)<=4096)
    return data


def root_confined_dir(p):
    m=p.lstat()
    need(p.is_absolute() and p.resolve()==p and stat.S_ISDIR(m.st_mode) and m.st_uid==0 and not(m.st_mode&0o022))


def main():
    need(os.geteuid()==0 and len(sys.argv)==2)
    source=Path(sys.argv[1])
    root_confined_dir(source)
    need(source.parent==Path('/root') and stat.S_IMODE(source.stat().st_mode)==0o700)
    root_confined_dir(ETC)
    account=pwd.getpwnam(USER)
    need(account.pw_uid>0 and account.pw_gid>0 and Path(account.pw_dir)==STATE.parent
         and STATE.parent.resolve()==STATE.parent and STATE.resolve()==STATE)
    prior=STATE.lstat()
    need(stat.S_ISDIR(prior.st_mode) and (prior.st_uid,prior.st_gid,stat.S_IMODE(prior.st_mode))==
         (account.pw_uid,account.pw_gid,0o700) and not any(STATE.iterdir()))
    current=STATE.parent/'current'
    need(not current.exists() and not current.is_symlink())
    bundle={n:private_read(source/n) for n in NAMES}
    policy_hint=json.loads(bundle[NAMES[1]])
    db=Path(policy_hint['database'])
    need(db.is_absolute() and db.resolve()==db and db.parent!=STATE)
    parse(bundle,db.lstat())  # metadata only; the SQLite file is not opened
    plan=[(STATE/'http.json',bundle[NAMES[0]],0,account.pw_gid,0o640),
          (ETC/'application-policy.json',bundle[NAMES[1]],0,account.pw_gid,0o640),
          (ETC/'app-runtime.env',bundle[NAMES[2]],0,account.pw_gid,0o640),
          (STATE/'mcp-token',bundle[NAMES[3]],account.pw_uid,account.pw_gid,0o600),
          (STATE/'graph-password',bundle[NAMES[4]],account.pw_uid,account.pw_gid,0o600)]
    for p,*_ in plan: need(not p.exists() and not p.is_symlink())
    # No services are loaded, no current pointer, and initial state is empty.
    # Freeze further path creation under STATE to root before placing configs.
    statefd=os.open(STATE,os.O_DIRECTORY|os.O_RDONLY|os.O_NOFOLLOW)
    old=os.fstat(statefd)
    need((old.st_dev,old.st_ino)==(prior.st_dev,prior.st_ino))
    created=[]
    try:
        os.fchown(statefd,0,account.pw_gid)
        os.fchmod(statefd,0o750)
        need(not os.listdir(statefd))
        cache=STATE/'graph-state'
        os.mkdir(cache.name,0o700,dir_fd=statefd); created.append(cache)
        os.chown(cache.name,account.pw_uid,account.pw_gid,dir_fd=statefd,follow_symlinks=False)
        for p,data,uid,gid,mode in plan:
            target=p.name if p.parent==STATE else p
            fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,
                       dir_fd=statefd if p.parent==STATE else None)
            created.append(p)
            with os.fdopen(fd,'wb') as f:
                f.write(data); f.flush(); os.fchown(f.fileno(),uid,gid); os.fchmod(f.fileno(),mode); os.fsync(f.fileno())
        os.fsync(statefd)
        current_meta=STATE.lstat()
        need((current_meta.st_dev,current_meta.st_ino)==(old.st_dev,old.st_ino))
    except Exception:
        for p in reversed(created):
            if p.name=='graph-state': os.rmdir(p.name,dir_fd=statefd)
            elif p.parent==STATE: os.unlink(p.name,dir_fd=statefd)
            else: p.unlink()
        os.fchown(statefd,prior.st_uid,prior.st_gid); os.fchmod(statefd,stat.S_IMODE(prior.st_mode))
        raise
    finally: os.close(statefd)
    print('Installed exact scoped config and independent credentials, policy remains UNARMED. No units/route/SQLite/flags changed.')


if __name__=='__main__':
    try: main()
    except (ValueError,TypeError,KeyError,OSError):
        raise SystemExit('Unarmed scoped runtime install failed closed') from None
