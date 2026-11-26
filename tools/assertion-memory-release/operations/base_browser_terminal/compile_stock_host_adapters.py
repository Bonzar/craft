"""Compile the two scoped adapters; do not install or export host source."""
from hashlib import sha256
import os
from pathlib import Path
import stat

from compose_pinned_host_pair import compose

MOUNT=Path('/etc/assertion-release/configured-base-prod/base-terminal-binding')
GUARD=Path('/usr/local/sbin/assertion-base-prod-transition')
HOOK=Path('/etc/assertion-release/production/prod_service_hook.py')
_COMMON='''def _base_exact_binding(name):
    from importlib import import_module
    from pathlib import Path
    import stat
    import sys
    root=Path('/etc/assertion-release/configured-base-prod/base-terminal-binding')
    if root.resolve(strict=True)!=root:
        raise ValueError('wrong exact protected Base binding path')
    for parent in (root,*root.parents):
        inode=parent.lstat()
        if (not stat.S_ISDIR(inode.st_mode) or inode.st_uid!=0 or inode.st_gid!=0
                or inode.st_mode&0o022):
            raise ValueError('untrusted protected Base binding directory')
    path=root/(name+'.py')
    inode=path.lstat()
    if (not stat.S_ISREG(inode.st_mode) or inode.st_uid!=0 or inode.st_gid!=0
            or inode.st_mode&0o022 or inode.st_nlink!=1 or path.resolve(strict=True)!=path):
        raise ValueError('untrusted protected Base binding module')
    sys.path.insert(0,str(root))
    try:
        mod=import_module(name)
    finally:
        del sys.path[0]
    if getattr(mod,'__file__',None)!=str(path):
        raise ValueError('wrong protected Base module origin')
    return mod

'''
GUARD_ADAPTER=(_COMMON+'''def _base_browser_restart_selected():
    from pathlib import Path
    marker=Path('/etc/assertion-release/production/base-browser-listener.json')
    try:
        marker.lstat()
    except FileNotFoundError:
        return run([*userctl(False),'restart',*NEW],timeout=18)
    return _base_exact_binding('settled_guard_binding').restart_selected(
        run=run,userctl=userctl,active=active)
''').encode('ascii')
HOOK_ADAPTER=(_COMMON+'''def _base_browser_sidecar_enabled():
    from pathlib import Path
    marker=Path('/etc/assertion-release/production/base-browser-listener.json')
    try:
        marker.lstat()
    except FileNotFoundError:
        return False
    return _base_exact_binding('host_hook_bindings').sidecar_enabled()

def _base_browser_sidecar_check_live(policy,release,uid,*,trusted_pid,not_listening):
    return _base_exact_binding('host_hook_bindings').sidecar_check_live(
        policy,release,uid,trusted_pid=trusted_pid,not_listening=not_listening)
''').encode('ascii')

def _stock(path,mode):
    if path.resolve(strict=True)!=path:
        raise ValueError('untrusted stock Base source path')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        i=os.fstat(stream.fileno())
        if (not stat.S_ISREG(i.st_mode) or i.st_uid!=0 or i.st_gid!=0 or i.st_nlink!=1
                or stat.S_IMODE(i.st_mode)!=mode or not os.path.samestat(i,path.lstat())):
            raise ValueError('untrusted stock Base source inode')
        raw=stream.read(1024*1024+1)
    if not 0<len(raw)<=1024*1024:
        raise ValueError('wrong stock Base source size')
    return raw

def receipt():
    if os.getuid()!=0 or os.geteuid()!=0:
        raise ValueError('existing root operator compile probe only')
    output=compose(raw_guard=_stock(GUARD,0o755),raw_hook=_stock(HOOK,0o644),
        guard_adapter=GUARD_ADAPTER,guard_adapter_pin=sha256(GUARD_ADAPTER).hexdigest(),
        hook_adapter=HOOK_ADAPTER,hook_adapter_pin=sha256(HOOK_ADAPTER).hexdigest())
    return {'guard_compiled':sha256(output[0]).hexdigest(),
            'hook_compiled':sha256(output[1]).hexdigest(),
            'source_exported':False,'host_written':False}

if __name__=='__main__':
    import json
    print(json.dumps(receipt(),sort_keys=True))
