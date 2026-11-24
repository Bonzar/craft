"""One root-only sterile Linux fixture; no host paths except proc and account metadata."""
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile
from unittest.mock import patch
import guard as g

def main():
    if not sys.platform.startswith('linux') or os.geteuid()!=0:
        print('SKIP: require root inside disposable Linux fixture')
        return
    old=pwd.getpwnam('daemon')
    new=pwd.getpwnam('nobody')
    assert old.pw_uid!=new.pw_uid and old.pw_gid!=new.pw_gid
    owners={g.LEGACY_ACCOUNT:(old.pw_uid,old.pw_gid),g.ACCOUNT:(new.pw_uid,new.pw_gid)}
    with tempfile.TemporaryDirectory(prefix='assertion-custody-sterile-') as temp:
        d=Path(temp); db=d/'only-fixture.sqlite3'; wal=Path(str(db)+'-wal'); shm=Path(str(db)+'-shm')
        for p in (db,wal):
            p.write_bytes(b'sterile fixture'); p.chmod(0o600); os.chown(p,*owners[g.LEGACY_ACCOUNT])
        d.chmod(0o700); os.chown(d,*owners[g.LEGACY_ACCOUNT])
        ds,fs=d.stat(),db.stat()
        v={'directory':[ds.st_dev,ds.st_ino],'inode':[fs.st_dev,fs.st_ino]}
        child=subprocess.Popen([sys.executable,'-I','-B','-c',
            "import sys,time; f=open(sys.argv[1],'rb'); print('held',flush=True); time.sleep(30)",str(db)],
            stdout=subprocess.PIPE,text=True)
        try:
            assert child.stdout.readline()=='held\n'
            with patch.object(g,'DB',db), patch.object(g,'exact_owner',new=owners.__getitem__):
                try: g.custody(v,owners[g.ACCOUNT])
                except RuntimeError as exc: assert 'unquiesced SQLite holder' in str(exc)
                else: raise AssertionError('holder missed')
                assert db.stat().st_uid==old.pw_uid
                child.terminate(); child.wait(timeout=2)
                g.custody(v,owners[g.ACCOUNT])
                assert all(p.stat().st_uid==new.pw_uid for p in (d,db,wal))
                wal.unlink()
                shm.write_bytes(b'new simulated sidecar'); shm.chmod(0o600); os.chown(shm,*owners[g.ACCOUNT])
                g.custody(v,owners[g.LEGACY_ACCOUNT])
                assert all(p.stat().st_uid==old.pw_uid for p in (d,db,shm))
                assert not wal.exists() and db.stat().st_ino==fs.st_ino and db.read_bytes()==b'sterile fixture'
        finally:
            if child.poll() is None: child.kill(); child.wait(timeout=2)
    print('Linux exact custody, live holder rejection, sidecar recreation and restore passed')

if __name__=='__main__': main()
