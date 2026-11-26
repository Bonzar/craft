"""Operator-local read-only facts, no source export and no candidate write."""
import ast
from hashlib import sha256
import json
import os
from pathlib import Path
import stat

GUARD=(Path('/usr/local/sbin/assertion-base-prod-transition'),
       '1b09e5d77c338a148a9df471e0ed010c82f104a2f310a0e45b73cd31f67c6c34',0o755)
HOOK=(Path('/etc/assertion-release/production/prod_service_hook.py'),
      '0b3f585ffc1668fe3627a967de66d21fb275271c1e891915466542b9a1445c5e',0o644)
AUTH=(Path('/etc/assertion-release/configured-base-prod/authorizer-entry.py'),
      '4fb7e3809728a4ac4d5cf70f9c61b240117a7fc1b91dafbc9ddb952dc4f781c7',0o644)
def read_exact(pinned):
    path,pin,mode=pinned
    if path.resolve(strict=True)!=path:
        raise ValueError('noncanonical exact protected source path')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as f:
        info=os.fstat(f.fileno())
        if (not stat.S_ISREG(info.st_mode) or (info.st_uid,info.st_gid)!=(0,0)
                or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=mode
                or not os.path.samestat(info,path.lstat())):
            raise ValueError('protected source metadata mismatch')
        data=f.read(512*1024+1)
    if not 0<len(data)<=512*1024 or sha256(data).hexdigest()!=pin:
        raise ValueError('protected source SHA mismatch')
    return ast.parse(data)
def funcs(tree,names):
    selected={node.name:node for node in tree.body if type(node) is ast.FunctionDef and node.name in names}
    if len(selected)!=len(names):
        raise ValueError('protected function set changed')
    return selected
def inspect():
    g=funcs(read_exact(GUARD),{'receive_restart_after_settlement','receive_start','verify_settled'})
    h=funcs(read_exact(HOOK),{'run','start_services','check_live','wait_until_listening'})
    fixed=ast.dump(ast.parse("run([*userctl(False), 'restart', *NEW], timeout=18)",mode='eval').body)
    checks=[n for n in ast.walk(g['receive_restart_after_settlement']) if type(n) is ast.Call
            and ast.dump(n)==fixed]
    if len(checks)!=1 or checks[0].lineno!=243 or checks[0].end_lineno!=243:
        raise ValueError('exact scoped settled call mismatch')
    entry=funcs(read_exact(AUTH),{'main'})
    return {'guard_bound':True,'settled_call':243,
            'legacy_first_entry':g['receive_start'].lineno,
            'hook':{key:func.lineno for key,func in h.items()},'auth_entry':entry['main'].lineno,
            'changed_source':False,'source_exported':False}
if __name__=='__main__':
    try: print(json.dumps(inspect(),sort_keys=True,separators=(',',':')))
    except Exception:
        print('{"review":"exact_source_or_ast_mismatch","changed_source":false}')
        raise SystemExit(1)
