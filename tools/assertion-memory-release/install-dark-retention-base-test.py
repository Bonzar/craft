"""One-time dark shared receiver upgrade. No service, pointer or policy edits."""
import argparse
from contextlib import ExitStack
import fcntl
from hashlib import sha256
import json
import os
import pwd
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from urllib.request import urlopen

CONTROL=Path('/etc/assertion-release/shared')
PROFILE=Path('/etc/assertion-release-retention')
FENCE=Path('/run/assertion-base-prod-transition/fence.lock')
GUARD=Path('/var/lib/assertion-base-prod-transition')
BASE_TEST=Path('/srv/assertion-release/test')
BASE_PROD=Path('/srv/assertion-release/production')
VISUAL_TEST=Path('/var/lib/assertion-memory-visual/test')
VISUAL_PROD=Path('/var/lib/assertion-memory-visual/prod')
ROOTS=(BASE_TEST,BASE_PROD,VISUAL_TEST,VISUAL_PROD)
SCOPES=('base-test','base-prod','visual-test','visual-prod')
ACCOUNTS=('assertion-test-deploy','assertion-production-deploy',
          'assertion-visual-test-deploy','assertion-visual-prod-deploy')
# Additional dependencies go in first; the old and new core are compatible
# with the previously installed Base module during the locked handoff.
MODULES=('release_retention.py','retention_authority.py','release_core.py','base_payload.py')
NEEDED={'release_core.py','base_payload.py'}
ENV={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'}
SHA=re.compile(r'[a-f0-9]{64}\Z')
RELEASE_NAME=re.compile(r'[a-f0-9]{40}-[a-f0-9]{64}\Z')
LIMIT=262144


def need(ok, reason):
    if not ok:
        raise ValueError(reason)


def sig(st):
    return st.st_dev,st.st_ino,st.st_mode,st.st_uid,st.st_gid,st.st_nlink,st.st_size,st.st_mtime_ns,st.st_ctime_ns


def root_dir(path):
    need(path.resolve(strict=True)==path,'canonical_control_path')
    for folder in (*path.parents,path):
        meta=folder.lstat()
        need(stat.S_ISDIR(meta.st_mode) and meta.st_uid==0 and not meta.st_mode&0o022,
             'root_owned_control_ancestor')
    return path.lstat()


def read_root_file(path):
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW),'rb') as f:
        meta=os.fstat(f.fileno())
        need(stat.S_ISREG(meta.st_mode) and meta.st_uid==meta.st_gid==0 and meta.st_nlink==1
             and stat.S_IMODE(meta.st_mode)==0o644 and 0<meta.st_size<=LIMIT,'root_control_file')
        raw=f.read(LIMIT+1)
        need(len(raw)==meta.st_size and sig(path.lstat())==sig(meta),'root_control_drift')
        return raw,sig(meta)


def source_bytes(revision,pins):
    data={}
    for name in MODULES:
        url=f'https://raw.githubusercontent.com/Bonzar/craft/{revision}/tools/assertion-memory-release/{name}'
        with urlopen(url,timeout=8) as response:
            raw=response.read(LIMIT+1)
            need(response.geturl()==url and 0<len(raw)<=LIMIT and sha256(raw).hexdigest()==pins[name],
                 'expected_public_source_hash')
        compile(raw,name,'exec')
        data[name]=raw
    return data


def capture_original(new):
    result={}
    for name in MODULES:
        path=CONTROL/name
        if os.path.lexists(path):
            raw,seal=read_root_file(path)
            if name not in NEEDED:
                need(raw==new[name],'unknown_existing_new_module')
            result[name]=(raw,seal)
        else:
            need(name not in NEEDED,'required_prior_controller')
            result[name]=(None,None)
    manifest=json.dumps({k:sha256(raw).hexdigest() if raw is not None else None
                         for k,(raw,_) in result.items()},sort_keys=True,separators=(',',':')).encode()
    return result,sha256(manifest).hexdigest()


def read_snapshot():
    need(not any(os.path.lexists(PROFILE/(s+'.enabled')) for s in SCOPES),'retention_already_armed')
    need(not any(os.path.lexists(GUARD/q) for q in ('pending.json','permit.json')),'prod_guard_active')
    manifest={}
    for root in ROOTS:
        need(root.resolve(strict=True)==root,'unexpected_scope_root')
        product=root if root in (BASE_TEST,BASE_PROD) else root/'runtime'
        for marker in (root/'.activation-pending-v1',product/'.runtime-transition',
                       product/'.visual-runtime-joint-transition'):
            need(not os.path.lexists(marker),'scope_transition_unsettled')
        folders=[(root,'releases')]
        if product!=root:
            folders.append((product,'versions'))
        for parent,child in folders:
            pointer=parent/'current'
            raw=os.readlink(pointer)
            resolved=pointer.resolve(strict=True)
            regex=RELEASE_NAME if parent in (BASE_TEST,BASE_PROD) else SHA
            need(resolved.parent==parent/child and regex.fullmatch(resolved.name)
                 and resolved.resolve(strict=True)==resolved and not resolved.is_symlink()
                 and resolved.is_dir(),'wrong_live_release')
            manifest[str(pointer)]=(raw,sig(pointer.lstat()),sig(resolved.lstat()))
    return manifest


def lock_file(stack,path,uid):
    f=stack.enter_context(os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb'))
    actual=os.fstat(f.fileno())
    mode=0o644 if path==FENCE else 0o600
    need(stat.S_ISREG(actual.st_mode) and actual.st_uid==uid and actual.st_nlink==1
         and stat.S_IMODE(actual.st_mode)==mode and sig(path.lstat())==sig(actual),'scope_lock_identity')
    fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)


def scope_locks(stack):
    root_dir(FENCE.parent)
    lock_file(stack,FENCE,0)
    for root,account in zip(ROOTS,ACCOUNTS):
        owner=pwd.getpwnam(account).pw_uid
        need(owner!=0 and root.lstat().st_uid==owner,'wrong_deploy_root_owner')
        lock_file(stack,root/'deploy.lock',owner)
    for root in (VISUAL_TEST,VISUAL_PROD):
        marker=root/'runtime'/'runtime.lock'
        if os.path.lexists(marker):
            lock_file(stack,marker,root.lstat().st_uid)


def sync_directory(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def replace(name,data):
    path=CONTROL/name
    if data is None:
        path.unlink()
    else:
        fd,temporary=tempfile.mkstemp(prefix='.dark-retention-control-',dir=CONTROL)
        try:
            with os.fdopen(fd,'wb') as f:
                os.fchmod(f.fileno(),0o644)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary,path)
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)
    sync_directory(CONTROL)


def import_check():
    text=("import importlib,pathlib,sys; here=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(here)); "
          "modules=[importlib.import_module(n) for n in sys.argv[2:]]; "
          "assert all(pathlib.Path(m.__file__)==here/(m.__name__+'.py') for m in modules); "
          "rt=modules[0]; assert all(rt.bindings(path,()) is None for path,_ in rt.SCOPES.values())")
    subprocess.run([sys.executable,'-I','-B','-c',text,str(CONTROL),*(n[:-3] for n in MODULES)],
                   env=ENV,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                   check=True,timeout=15)


def save_prior(prior,new):
    # A killed installer may not run in-process recovery. Save only the
    # previously reviewed controller bytes, never an application build.
    recovery=Path(tempfile.mkdtemp(prefix='.dark-retention-rollback-',dir=CONTROL))
    metadata={}
    for name,(old,_) in prior.items():
        metadata[name]={'old':sha256(old).hexdigest() if old is not None else None,
                        'new':sha256(new[name]).hexdigest()}
        if old is not None:
            with (recovery/name).open('xb') as out:
                os.fchmod(out.fileno(),0o600)
                out.write(old);out.flush();os.fsync(out.fileno())
    with (recovery/'manifest.json').open('x',encoding='ascii') as out:
        os.fchmod(out.fileno(),0o600)
        json.dump(metadata,out,sort_keys=True,separators=(',',':'))
        out.flush();os.fsync(out.fileno())
    sync_directory(recovery)
    sync_directory(CONTROL)
    return recovery


def clear_prior(recovery,prior):
    for name,(old,_) in prior.items():
        if old is not None:
            (recovery/name).unlink()
    (recovery/'manifest.json').unlink()
    recovery.rmdir()
    sync_directory(CONTROL)


def apply(new,prior,pointers):
    written=[]
    recovery=save_prior(prior,new)
    try:
        for name in MODULES:
            old,seal=prior[name]
            if old==new[name]:
                continue
            path=CONTROL/name
            need((not os.path.lexists(path) if seal is None else sig(path.lstat())==seal),'baseline_changed')
            written.append(name)
            replace(name,new[name])
        for name,expected in new.items():
            need(read_root_file(CONTROL/name)[0]==expected,'installed_controller_hash')
        import_check()
        need(read_snapshot()==pointers,'serving_pointer_changed')
    except BaseException:
        for name in reversed(written):
            path=CONTROL/name
            current=read_root_file(path)[0] if os.path.lexists(path) else None
            if current==prior[name][0]:
                continue
            need(current==new[name],'foreign_change_during_recovery')
            replace(name,prior[name][0])
        clear_prior(recovery,prior)
        raise
    clear_prior(recovery,prior)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('--check','--apply'))
    parser.add_argument('revision')
    parser.add_argument('pins',help='comma-separated SHA256 in module order')
    parser.add_argument('expected_baseline',nargs='?')
    # Treat the documented --check/--apply as literal modes.
    args=parser.parse_args(['--',*sys.argv[1:]])
    need(os.geteuid()==os.getegid()==0 and sys.flags.isolated and sys.dont_write_bytecode,'confined_root_invocation')
    need(bool(re.fullmatch('[a-f0-9]{40}',args.revision)),'exact_reviewed_revision')
    inputs=args.pins.split(',')
    need(len(inputs)==len(MODULES) and all(SHA.fullmatch(d) for d in inputs),'exact_source_pin_set')
    root_dir(CONTROL)
    need(not any(p.name.startswith('.dark-retention-rollback-') for p in CONTROL.iterdir()),
         'unsettled_dark_controller_backup')
    before=read_snapshot()
    new=source_bytes(args.revision,dict(zip(MODULES,inputs)))
    _,baseline=capture_original(new)
    if args.mode=='--check':
        need(args.expected_baseline is None and read_snapshot()==before,'read_only_mode')
        print(json.dumps({'dark_check':True,'baseline':baseline,'module_count':len(MODULES),
                          'files_written':0,'locks_acquired':0,'retention_armed':False}))
        return
    need(args.expected_baseline==baseline,'must_match_checked_baseline')
    with ExitStack() as stack:
        scope_locks(stack)
        current=read_snapshot()
        prior,current_baseline=capture_original(new)
        need(current==before and current_baseline==baseline,'scope_changed_before_dark_install')
        apply(new,prior,current)
    print(json.dumps({'dark_controller_install':True,'revision':args.revision,'module_count':len(MODULES),
                      'pointers_unchanged':True,'retention_armed':False}))


if __name__=='__main__':
    try:
        main()
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError):
        print('{"dark_controller_install":false,"error":"proof_or_install_failed"}')
        sys.exit(1)
