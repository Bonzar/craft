"""Validate upgrade/rollback on an isolated SQLite shadow, never on live rows."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time
import uuid

WORKER = r"""
import sys
from pathlib import Path
source,db,action,n,r,fd=sys.argv[1:]
import os
fd=int(fd)
def worker_run(source,db,action,n,r):
    db=Path(db)
    if not db.parent.name.startswith('prod-shadow-') or db.name!='shadow.sqlite3' or db.resolve()!=db:
        raise ProbeMismatch()
    sys.path.insert(0,source)
    from prototypes.assertion_memory.storage import MemoryStore
    def put(store,mid):
        store.append_assertion(assertion_id=mid,text='synthetic migration probe',
            actor='synthetic',source='isolated-rehearsal',episode='fixture',
            recorded_at='2000-01-01T00:00:00Z')
    def ok(store,mid):
        return getattr(store.get_assertion(mid),'text',None)=='synthetic migration probe'
    with MemoryStore(db) as store:
        version = store.version
        if callable(version):
            version()
        if action=='old_before':
            pass
        elif action=='new_write':
            put(store,n)
            if not ok(store,n): raise ProbeMismatch()
        elif action=='old_rollback':
            if not ok(store,n): raise ProbeMismatch()
            put(store,r)
            if not ok(store,r): raise ProbeMismatch()
        elif action=='new_readback':
            if not(ok(store,n) and ok(store,r)): raise ProbeMismatch()
        else:
            raise ProbeMismatch()
class ProbeMismatch(Exception): pass
CODES={name:bytes([i+65]) for i,name in enumerate((
    'ModuleNotFoundError','ImportError','TypeError','AttributeError','OperationalError',
    'IntegrityError','DatabaseError','PermissionError','FileNotFoundError','AssertionError',
    'ValueError','ProbeMismatch','RuntimeError'))}
try:
    worker_run(source,db,action,n,r)
except Exception as exc:
    os.write(fd,CODES.get(type(exc).__name__,b'?'))
    raise SystemExit(11) from None
else:
    os.write(fd,b'!')

"""


PARENT_PHASES = frozenset(('preflight','backup','metadata_before','old_before','new_write',
    'metadata_after_new','old_rollback','new_readback','metadata_after_rollback','cleanup'))
CATEGORIES = (
    'ModuleNotFoundError','ImportError','TypeError','AttributeError','OperationalError',
    'IntegrityError','DatabaseError','PermissionError','FileNotFoundError','AssertionError',
    'ValueError','ProbeMismatch','RuntimeError')
SAFE_CATEGORIES = frozenset(CATEGORIES+('TimeoutExpired','TimeoutError','OSError','CalledProcessError',
                                         'WorkerUnknown','OtherError'))


class RehearsalFailed(Exception):
    def __init__(self, phase, category):
        self.phase = phase if phase in PARENT_PHASES else 'preflight'
        self.category = category if category in SAFE_CATEGORIES else 'OtherError'


def require(o):
    if not o: raise ValueError('Unsafe or incompatible shadow rehearsal')


def canonical_directory(path, *, private=False):
    require(path.is_absolute() and path.resolve()==path and path.is_dir())
    s=path.stat()
    if private: require(s.st_uid==os.geteuid() and not (s.st_mode&0o077))


def schema(con):
    return frozenset((str(a),str(b),str(c),str(d or '')) for a,b,c,d in con.execute(
        'SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE "sqlite_%"'))


def metadata(path):
    cx=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)
    try:
        require(cx.execute('PRAGMA quick_check(1)').fetchone()==('ok',))
        return schema(cx)
    finally: cx.close()


def consistent_copy(source, target):
    # File is opened read-only. SQLite applies existing WAL transaction view.
    require(source.resolve()==source and stat.S_ISREG(source.lstat().st_mode))
    original=os.stat(source,follow_symlinks=False)
    require(original.st_size<=16*1024**3 and shutil.disk_usage(target.parent).free>=
            max(128*1024**2,original.st_size*3))
    fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600); os.close(fd)
    start=time.monotonic()
    cx=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=2)
    co=sqlite3.connect(target,timeout=2)
    try:
        def progress(*_):
            if time.monotonic()-start>60: raise TimeoutError('Backup progress limit exceeded')
        cx.backup(co,pages=512,progress=progress,sleep=.02)
    finally:
        cx.close(); co.close()
    latest=os.stat(source,follow_symlinks=False)
    require((original.st_dev,original.st_ino)==(latest.st_dev,latest.st_ino))


def worker(py, source, shadow, phase, ids):
    require(py.is_absolute() and py.is_file() and os.access(py,os.X_OK))
    canonical_directory(source)
    rd,wr = os.pipe()
    try:
        os.set_blocking(rd,False)
        completed=subprocess.run([str(py),'-I','-B','-c',WORKER,str(source),str(shadow),phase,*ids,str(wr)],
            cwd=shadow.parent,env={'PATH':'/usr/bin:/bin','HOME':str(shadow.parent),'LANG':'C.UTF-8'},
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            pass_fds=(wr,),timeout=35,check=False)
        try: result=os.read(rd,2)
        except BlockingIOError: result=b''
        if completed.returncode!=0 or result!=b'!':
            index=result[0]-65 if len(result)==1 else -1
            safe=CATEGORIES[index] if 0 <= index < len(CATEGORIES) else 'WorkerUnknown'
            raise RehearsalFailed(phase,safe)
    finally:
        os.close(rd); os.close(wr)


def run(base, source, old_py, old_source, new_py, new_source):
    phase='preflight'
    try:
        canonical_directory(base,private=True)
        require(source.is_absolute() and ':memory:' not in str(source))
        with tempfile.TemporaryDirectory(prefix='prod-shadow-',dir=base) as d:
            temp=Path(d); shadow=temp/'shadow.sqlite3'
            phase='backup'
            consistent_copy(source,shadow)
            phase='metadata_before'
            baseline=metadata(shadow)
            token=uuid.uuid4().hex
            ids=('shadow-new-'+token,'shadow-rollback-'+token)
            phase='old_before'
            worker(old_py,old_source,shadow,phase,ids)
            phase='new_write'
            worker(new_py,new_source,shadow,phase,ids)
            phase='metadata_after_new'
            after_new=metadata(shadow)
            phase='old_rollback'
            worker(old_py,old_source,shadow,phase,ids)
            phase='new_readback'
            worker(new_py,new_source,shadow,phase,ids)
            phase='metadata_after_rollback'
            after_rollback=metadata(shadow)
            changes=(baseline!=after_new, after_new!=after_rollback)
            phase='cleanup'
        return {'old_new_old_new_marker_roundtrip':True,'quick_check_ok':True,
                'schema_changed_on_new':changes[0],'schema_changed_while_rolling_back':changes[1],
                'private_shadow_removed':True}
    except RehearsalFailed:
        raise
    except Exception as exc:
        raise RehearsalFailed(phase,type(exc).__name__) from None


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('scratch','snapshot-source','old-python','old-source','new-python','new-source'):
        p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args()
    return run(a.scratch,a.snapshot_source,a.old_python,a.old_source,a.new_python,a.new_source)


if __name__=='__main__':
    try: print(json.dumps(main(),sort_keys=True))
    except RehearsalFailed as e:
        print(json.dumps({'stage':e.phase,'class':e.category},sort_keys=True))
        raise SystemExit(1) from None
