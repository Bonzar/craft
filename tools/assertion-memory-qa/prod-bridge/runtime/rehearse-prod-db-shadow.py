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
source,db,action,n,r=sys.argv[1:]
db=Path(db)
if not db.parent.name.startswith('prod-shadow-') or db.name!='shadow.sqlite3' or db.resolve()!=db:
    raise SystemExit(4)
sys.path.insert(0,source)
from prototypes.assertion_memory.storage import MemoryStore
def put(store,mid):
    store.append_assertion(assertion_id=mid,text='synthetic migration probe',
        actor='synthetic',source='isolated-rehearsal',episode='fixture',
        recorded_at='2000-01-01T00:00:00Z')
def ok(store,mid):
    return getattr(store.get_assertion(mid),'text',None)=='synthetic migration probe'
with MemoryStore(db) as store:
    store.version()
    if action=='old_before':
        pass
    elif action=='new_write':
        put(store,n)
        if not ok(store,n): raise SystemExit(8)
    elif action=='old_rollback':
        if not ok(store,n): raise SystemExit(8)
        put(store,r)
        if not ok(store,r): raise SystemExit(8)
    elif action=='new_readback':
        if not(ok(store,n) and ok(store,r)): raise SystemExit(8)
    else:
        raise SystemExit(8)
"""


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
    subprocess.run([str(py),'-I','-B','-c',WORKER,str(source),str(shadow),phase,*ids],
        cwd=shadow.parent,env={'PATH':'/usr/bin:/bin','HOME':str(shadow.parent),'LANG':'C.UTF-8'},
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        timeout=35,check=True)


def run(base, source, old_py, old_source, new_py, new_source):
    canonical_directory(base,private=True)
    require(source.is_absolute() and ':memory:' not in str(source))
    with tempfile.TemporaryDirectory(prefix='prod-shadow-',dir=base) as d:
        temp=Path(d); shadow=temp/'shadow.sqlite3'
        consistent_copy(source,shadow)
        baseline=metadata(shadow)
        token=uuid.uuid4().hex
        ids=('shadow-new-'+token,'shadow-rollback-'+token)
        worker(old_py,old_source,shadow,'old_before',ids)
        worker(new_py,new_source,shadow,'new_write',ids)
        after_new=metadata(shadow)
        worker(old_py,old_source,shadow,'old_rollback',ids)
        worker(new_py,new_source,shadow,'new_readback',ids)
        after_rollback=metadata(shadow)
        changes=(baseline!=after_new, after_new!=after_rollback)
    return {'old_new_old_new_marker_roundtrip':True,'quick_check_ok':True,
            'schema_changed_on_new':changes[0],'schema_changed_while_rolling_back':changes[1],
            'private_shadow_removed':True}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('scratch','snapshot-source','old-python','old-source','new-python','new-source'):
        p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args()
    return run(a.scratch,a.snapshot_source,a.old_python,a.old_source,a.new_python,a.new_source)


if __name__=='__main__':
    try: print(json.dumps(main(),sort_keys=True))
    except (ValueError,ArithmeticError,OSError,sqlite3.Error,subprocess.SubprocessError):
        raise SystemExit('Private shadow rehearsal refused or failed; no live DB modifications requested.') from None
