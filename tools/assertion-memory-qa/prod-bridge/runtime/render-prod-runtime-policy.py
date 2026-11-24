"""Stage private, unarmed base-runtime configuration; no app, DB or proxy actions."""
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys

ROOT=Path('/srv/assertion-release/production')
MCP=8903
GRAPH=8901


def check(ok,message):
    if not ok:
        raise ValueError(message)


def bundle(d, meta, token, password):
    check(set(d)=={'canonical_production_db','public_host','legacy_system_units'},'Invalid review input')
    raw,host,units=(d['canonical_production_db'],d['public_host'],d['legacy_system_units'])
    check(isinstance(raw,str) and re.fullmatch(r'/[A-Za-z0-9_./-]{8,240}[.]sqlite3',raw)
          and not any(x in Path(raw).parts for x in ('.','..','test','qa'))
          and not any(k in raw for k in ('synthetic','fictional')), 'Unsafe canonical DB scope')
    check(isinstance(host,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,244}(?::[0-9]{1,5})?',host),'Invalid exact public host')
    check(isinstance(units,list) and len(units)==2 and len(set(units))==2
          and all(isinstance(x,str) and re.fullmatch(r'[A-Za-z0-9_.@-]{1,100}[.]service',x)
                  and not x.startswith('assertion-production-deploy-') for x in units),'Invalid legacy unit handoff')
    check(stat.S_ISREG(meta.st_mode) and not(meta.st_mode&0o002) and meta.st_ino>0,'Invalid database metadata')
    check(re.fullmatch('[0-9a-f]{64}',token) and re.fullmatch('[0-9a-f]{64}',password)
          and token!=password,'Provide independent random credentials')
    config={'service':{'db':raw,'provider':'none','embedding_mode':'lexical','processing_session_ids':[]},
            'token_file':str(ROOT/'state/mcp-token'),'public_host':host,'port':MCP}
    policy={'database':raw,'db_dev':meta.st_dev,'db_inode':meta.st_ino,'mcp_config':str(ROOT/'state/http.json'),
            'mcp_token':str(ROOT/'state/mcp-token'),'graph_password':str(ROOT/'state/graph-password'),
            'mcp_port':MCP,'graph_port':GRAPH,'services_ownership_attested':False,'legacy_system_units':units}
    return {'http.json.candidate':json.dumps(config,sort_keys=True)+'\n',
            'application-policy.json.unarmed':json.dumps(policy,sort_keys=True)+'\n',
            'app-runtime.env.candidate':'PROD_CANONICAL_DB='+raw+'\n',
            'mcp-token.new':token+'\n','graph-password.new':password+'\n'}


def input_json(path):
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as f:
        m=os.fstat(f.fileno())
        check(m.st_uid==0 and stat.S_ISREG(m.st_mode) and not(m.st_mode&0o077),'Untrusted input')
        data=f.read(4097)
    check(len(data)<=4096,'Input too large')
    return json.loads(data)


def main():
    check(os.geteuid()==0 and len(sys.argv)==2,'Root-private review files only')
    base=Path(sys.argv[1]); m=base.lstat()
    check(base.parent==Path('/root') and base.resolve()==base and stat.S_ISDIR(m.st_mode)
          and m.st_uid==0 and stat.S_IMODE(m.st_mode)==0o700,'Unsafe review directory')
    d=input_json(base/'runtime-input.json')
    db=Path(d['canonical_production_db'])
    check(db.is_absolute() and db.resolve()==db,'Unsafe database path')
    # Inspection is metadata only. Never open SQLite, its WAL/SHM, or any record.
    values=bundle(d,db.lstat(),secrets.token_hex(32),secrets.token_hex(32))
    check(not any((base/n).exists() or (base/n).is_symlink() for n in values),'Review output exists')
    os.umask(0o077)
    for n,v in values.items():
        with (base/n).open('x',encoding='ascii') as f:
            f.write(v)
    print('Five root-only unarmed review files rendered; no host configuration, secrets in output, or state change.')


if __name__=='__main__':
    try: main()
    except (ValueError,OSError,KeyError,TypeError):
        raise SystemExit('Private base runtime render refused; no live changes') from None
