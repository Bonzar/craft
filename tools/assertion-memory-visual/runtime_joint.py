"""Visual two-pointer transaction; only fixed callbacks, no root operations."""
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile

from runtime_switch import DIGEST, move, sync_directory, target
from runtime_bootstrap import USED

JOURNAL = '.visual-runtime-joint-transition'
STATIC_PENDING = '.activation-pending-v1'


def static_target(root, value, validate):
    if (type(value) is not str or not value.startswith('releases/')
            or not DIGEST.fullmatch(value.removeprefix('releases/'))):
        raise ValueError('Invalid Visual static target')
    where=root/value
    if where.resolve(strict=True)!=where or validate(where)!=where.name:
        raise ValueError('Unverified Visual static release')
    return value


def pair(value, visual, runtime, validate):
    if type(value) is not dict or set(value)!={'static','runtime'}:
        raise ValueError('Invalid Visual pointer pair')
    static_target(visual,value['static'],validate)
    link=value['runtime']
    if type(link) is not str or not link.startswith('versions/') or target(runtime,link[9:])!=link:
        raise ValueError('Invalid Visual runtime target')
    return value


def run(visual, runtime, candidate, *, validate, restart, smoke, bootstrap=None):
    """Both candidates must already be prepared and immutable; use canonical Visual lock."""
    visual,runtime=Path(visual),Path(runtime)
    if any(root.resolve(strict=True)!=root for root in (visual,runtime)) or visual==runtime:
        raise ValueError('Invalid Visual state roots')
    fd=os.open(visual/'deploy.lock',os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077:
            raise ValueError('Untrusted existing Visual release lock')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        marker=runtime/JOURNAL
        seal=runtime/USED
        if os.path.lexists(visual/STATIC_PENDING):
            raise ValueError('Unsettled static release requires protected recovery')
        if bootstrap is not None and os.path.lexists(seal):
            raise ValueError('Legacy PROD Visual bootstrap already used')
        if bootstrap is None and os.path.lexists(marker):
            raise ValueError('Unsettled Visual release requires protected recovery')
        candidate=pair(candidate() if callable(candidate) else candidate,visual,runtime,validate)
        def static_current():
            old_static=os.readlink(visual/'current')
            return old_static.removeprefix(str(visual)+'/')
        def current():
            return pair({'static':static_current(),'runtime':os.readlink(runtime/'current')},
                        visual,runtime,validate)
        def restore(before, initial_process=None):
            if bootstrap:
                bootstrap.old_verified(runtime,before)
            move(runtime,before['runtime'])
            move(visual,before['static'])
            restart()
            if bootstrap:
                bootstrap.health(before,initial_process)
            else:
                smoke(before)
        if os.path.lexists(marker):
            with os.fdopen(os.open(marker,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as src:
                if not stat.S_ISREG(os.fstat(src.fileno()).st_mode):
                    raise ValueError('Untrusted bootstrap journal')
                journal=json.loads(src.read(4097))
            before=bootstrap.old_verified(runtime,bootstrap.before)
            required={'old','new','initial_process','bootstrap'}
            current_pair=current()
            if (type(journal) is not dict or set(journal)!=required or journal['old']!=before
                    or journal['new']!=candidate or journal['bootstrap'] is not True
                    or not isinstance(journal['initial_process'],str)
                    or any(current_pair[k] not in (before[k],candidate[k]) for k in before)):
                raise ValueError('Interrupted bootstrap must match the exact signed pair')
            restore(before,journal['initial_process'])
            marker.unlink()
            sync_directory(runtime)
            raise RuntimeError('Interrupted bootstrap recovered previous Visual; no new activation')
        if bootstrap and not os.path.lexists(runtime/'current'):
            if static_target(visual,static_current(),validate)!=bootstrap.before['static']:
                raise ValueError('Legacy active Visual does not match bootstrap authority')
            bootstrap.health(bootstrap.before,None)
            bootstrap.seed_old_entry(runtime)
        before=current()
        record={'old':before,'new':candidate}
        if bootstrap:
            bootstrap.old_verified(runtime,before)
            initial=bootstrap.health(before,None)
            if type(initial) is not str or not 0<len(initial)<=128:
                raise ValueError('Legacy running baseline could not be proven')
            record.update(bootstrap=True,initial_process=initial)
        with marker.open('x',encoding='ascii') as file:
            json.dump(record,file,sort_keys=True,separators=(',',':'))
            file.flush()
            os.fsync(file.fileno())
        sync_directory(runtime)
        try:
            move(visual,candidate['static'])
            move(runtime,candidate['runtime'])
            restart()
            smoke(candidate)
        except Exception:
            try:
                restore(before,record.get('initial_process'))
            except Exception:
                raise RuntimeError('Visual pair rollback was not verified') from None
            marker.unlink()
            sync_directory(runtime)
            raise RuntimeError('Visual activation failed; old pair restored') from None
        if bootstrap:
            os.replace(marker,seal)  # one durable rename, no ambiguous journal/seal overlap
        else:
            marker.unlink()
        sync_directory(runtime)
        return candidate
    finally:
        os.close(fd)

def activate_verified(visual, runtime, expected, archive, runtime_sha, files, *, restart, smoke, bootstrap=None):
    """Connect authenticated receiver bytes to prepared files and joint rollback."""
    import release_core
    import visual_policy
    from runtime_companion import digest
    visual,runtime=Path(visual),Path(runtime)

    def static_validate(path):
        return visual_policy.validate(path,allow_prepared=True)

    def prepare():
        with tempfile.TemporaryDirectory(dir=visual,prefix='.joint-prepare-') as work:
            with Path(archive).open('rb') as stream:
                extracted=release_core.unpack(stream,Path(work),expected['static_sha'],visual_policy.archive_spec())
            static_id=visual_policy.validate(extracted)
            release_core.prepare(extracted,visual/'releases'/static_id,visual,lambda _a,_r:None,
                                 visual_policy.retained)
        versions=runtime/'versions'
        if versions.resolve(strict=True)!=versions:
            raise ValueError('Untrusted Visual runtime versions')
        folder=versions/runtime_sha
        if not DIGEST.fullmatch(runtime_sha) or set(files)!={'asset-origin.py','sites-shell.mjs'}:
            raise ValueError('Invalid prepared Visual runtime')
        if not os.path.lexists(folder):
            with tempfile.TemporaryDirectory(dir=versions,prefix='.runtime-prepared-') as work:
                new=Path(work)/'candidate'
                new.mkdir(mode=0o750)
                for name,data in files.items():
                    with (new/name).open('xb') as out:
                        out.write(data)
                        os.fchmod(out.fileno(),0o640)
                        out.flush()
                        os.fsync(out.fileno())
                sync_directory(new)
                os.rename(new,folder)
                sync_directory(versions)
        if folder.resolve(strict=True)!=folder or {p.name for p in folder.iterdir()}!=set(files):
            raise ValueError('Untrusted existing Visual runtime')
        for name, data in files.items():
            path=folder/name
            if path.is_symlink() or not path.is_file() or path.stat().st_size!=len(data) or digest(path.read_bytes())!=digest(data):
                raise ValueError('Retained Visual runtime changed')
        return {'static':'releases/'+static_id,'runtime':'versions/'+runtime_sha}

    if bootstrap:
        bootstrap.authorize(expected,runtime_sha)
    return run(visual,runtime,prepare,validate=static_validate,restart=restart,smoke=smoke,bootstrap=bootstrap)
