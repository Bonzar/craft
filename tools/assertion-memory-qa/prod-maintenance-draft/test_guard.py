import importlib.util, pathlib, tempfile, unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('g',pathlib.Path(__file__).with_name('guard.py'))
g=importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
class Guard(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        d=pathlib.Path(self.tmp.name); self.v={'inode':[1,42],'old_nginx':g.digest(b'old'),'new_nginx':g.digest(b'new'),'old_nginx_mode':0o644,'phase':'armed'}
        self.calls=[]; self.saved=None; self.contents={d/'approved-candidate.nginx':b'new',d/'baseline.nginx':b'old',d/'ingress':b'old'}; (d/'ingress').touch()
        self.live={**dict.fromkeys(g.OLD,True),**dict.fromkeys(g.NEW,False),g.SITES:True,g.NEW_SITES:False,g.TIMER+'.timer':True}
        for k,v in dict(STATE=d,INGRESS=d/'ingress',inode=lambda:[1,42],active=self.live.__getitem__,safe_bytes=self.contents.__getitem__,custody=lambda *args,**kw:self.calls.append(('custody',args[1])),exact_owner=lambda name:(name,name),publish_permit=lambda v:self.calls.append('publish'),revoke_permit=lambda:None,verify_database_access=self.access,write=self.write,units=self.units,replace_file=self.replace,clear=lambda:self.calls.append('clear'),run=lambda a:self.calls.append(tuple(a))).items():
            pt=patch.object(g,k,v);pt.start();self.addCleanup(pt.stop)
    def units(self,action,names):
        self.calls.append((action,names)); self.live.update(dict.fromkeys(names,action=='start'))
    def replace(self,p,data,mode): self.contents[p]=data; self.calls.append(('replace',p,data,mode))
    def access(self,account,writable):
        self.calls.append(('access',account,writable))
    def write(self,v):
        self.saved=v
    def activate_via_github(self):
        self.live.update(dict.fromkeys(g.NEW,True)); self.live[g.NEW_SITES]=True; self.calls.append('github-started-new')
    def test_split_never_starts_candidate(self):
        g.drain(self.v); self.assertEqual(self.saved['phase'],'drained')
        self.assertFalse(any(self.live[u] for u in g.NEW+g.OLD))
        self.assertNotIn(('start',g.NEW),self.calls)
        with self.assertRaisesRegex(RuntimeError,'unit state'): g.switch(self.saved)
        self.activate_via_github(); g.switch(self.saved)
        self.assertEqual(self.contents[g.INGRESS],b'new')
        self.assertNotIn(('start',g.NEW),self.calls); self.assertNotIn('clear',self.calls)
    def test_first_failure_restores_old_and_route(self):
        g.drain(self.v); self.activate_via_github(); g.switch(self.saved)
        from types import SimpleNamespace
        with patch.object(pathlib.Path,'lstat',return_value=SimpleNamespace(st_gid=0)):
            g.rollback(self.saved)
        self.assertEqual(self.contents[g.INGRESS],b'old'); self.assertTrue(all(self.live[u] for u in g.OLD))
        self.assertLess(self.calls.index(('stop',g.NEW)),self.calls.index(('start',g.OLD)))
        self.assertEqual(self.calls[-1],'clear')
    def test_drift_does_not_overwrite_or_stop(self):
        self.contents[g.INGRESS]=b'concurrently changed'
        with self.assertRaisesRegex(RuntimeError,'differs'): g.rollback(self.v)
        self.assertEqual(self.contents[g.INGRESS],b'concurrently changed'); self.assertEqual(self.calls,[])
    def test_replaced_inode_refuses_writers(self):
        with patch.object(g,'inode',return_value=[2,42]),self.assertRaisesRegex(RuntimeError,'inode changed'):
            g.rollback(self.v)
        self.assertNotIn(('start',g.OLD),self.calls); self.assertNotIn('clear',self.calls)
    def test_timer_must_be_active(self):
        self.live[g.TIMER+'.timer']=False
        with self.assertRaisesRegex(RuntimeError,'timer is not active'): g.drain(self.v)
        self.assertEqual(self.calls,[])
    def test_new_uid_cannot_reopen_after_acl_change(self):
        g.drain(self.v); self.activate_via_github()
        with patch.object(g,'verify_database_access',side_effect=RuntimeError('legacy UID permission')),self.assertRaisesRegex(RuntimeError,'permission'):
            g.switch(self.saved)
        self.assertEqual(self.contents[g.INGRESS],b'old')
        self.assertFalse(self.live[g.SITES]); self.assertNotIn('clear',self.calls)
    def test_sites_reader_stopped_blocks_switch(self):
        g.drain(self.v); self.activate_via_github(); self.live[g.NEW_SITES]=False
        with self.assertRaisesRegex(RuntimeError,'Sites reader'): g.switch(self.saved)
        self.assertEqual(self.contents[g.INGRESS],b'old')
class ReceiverFence(unittest.TestCase):
    def exercise(self,permit,expect_error=None):
        import json
        from types import SimpleNamespace
        fd=tempfile.TemporaryFile(); self.addCleanup(fd.close)
        with patch.object(g.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=501)), \
             patch.object(g.os,'getuid',return_value=501), patch.object(g.os,'geteuid',return_value=501), \
             patch.object(g,'fence_lock',return_value=fd), \
             patch.object(g,'safe_bytes',return_value=json.dumps(permit).encode()), \
             patch.object(g,'inode',return_value=[1,42]), \
             patch.object(g.time,'monotonic_ns',return_value=1_000_000_000), \
             patch.object(g,'userctl',return_value=['mock-userctl']), \
             patch.object(g,'run') as run, \
             patch.object(g.subprocess,'run',return_value=SimpleNamespace(stdout='active\nactive\n')):
            if expect_error:
                with self.assertRaisesRegex(RuntimeError,expect_error): g.receive_start()
                run.assert_not_called()
            else:
                g.receive_start()
                run.assert_called_once_with(['mock-userctl','start',*g.NEW],timeout=18)
    def test_short_remaining_window_never_starts(self):
        self.exercise({'generation':'a'*32,'expiry':50_000_000_000,'inode':[1,42]},'expired')
    def test_stale_database_never_starts(self):
        self.exercise({'generation':'a'*32,'expiry':90_000_000_000,'inode':[1,43]},'inode changed')
    def test_fresh_exact_start_is_non_root_fixed_units(self):
        self.exercise({'generation':'a'*32,'expiry':90_000_000_000,'inode':[1,42]})
class CustodyFiles(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup); self.d=pathlib.Path(tmp.name)
        self.d.chmod(0o700); self.db=self.d/'memory.sqlite3'; self.write(self.db)
        for attr,val in {'DB':self.db}.items():
            p=patch.object(g,attr,val);p.start();self.addCleanup(p.stop)
        self.v={'directory':[self.d.stat().st_dev,self.d.stat().st_ino],'inode':[self.db.stat().st_dev,self.db.stat().st_ino]}
    def write(self,p):
        p.write_bytes(b'synthetic, do not write'); p.chmod(0o600)
    def test_sidecars_disappear_and_reappear(self):
        wal=self.d/(self.db.name+'-wal'); shm=self.d/(self.db.name+'-shm')
        self.write(wal)
        with g.custody_objects(self.v) as a: self.assertEqual(len(a),3)
        wal.unlink(); self.write(shm)
        with g.custody_objects(self.v) as a:
            self.assertEqual(len(a),3); self.assertEqual(a[-1][1].st_ino,shm.stat().st_ino)
        self.assertEqual(self.v['inode'][1],self.db.stat().st_ino)
    def test_symlink_sidecar_denied(self):
        (self.d/(self.db.name+'-wal')).symlink_to(self.db)
        with self.assertRaises(OSError):
            with g.custody_objects(self.v): pass
    def test_unknown_holder_prohibits_all_changes(self):
        from types import SimpleNamespace
        import os
        cur=(os.getuid(),os.getgid())
        target=(cur[0]+1,cur[1]+1)
        with patch.object(g,'exact_owner',side_effect=lambda x:cur if x==g.LEGACY_ACCOUNT else target), \
             patch.object(g,'no_extended_acl',return_value=None), \
             patch.object(g,'assert_unheld',side_effect=RuntimeError('unquiesced holder')), \
             patch.object(g.os,'fchown') as change:
            with self.assertRaisesRegex(RuntimeError,'unquiesced'): g.custody(self.v,target)
            change.assert_not_called()
    def test_appearance_during_handoff_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'pathname set changed'):
            with g.custody_objects(self.v): self.write(self.d/(self.db.name+'-wal'))
    def test_swapping_sidecar_path_identities_denied(self):
        wal=self.d/(self.db.name+'-wal'); shm=self.d/(self.db.name+'-shm'); tmp=self.d/'tmp'
        self.write(wal); self.write(shm)
        with self.assertRaisesRegex(RuntimeError,'pathname set changed'):
            with g.custody_objects(self.v):
                wal.rename(tmp); shm.rename(wal); tmp.rename(shm)
    def test_extended_acl_fails_without_fchown(self):
        from unittest.mock import create_autospec
        import os
        cur=(os.getuid(),os.getgid()); target=(cur[0]+1,cur[1]+1)
        with patch.object(g,'exact_owner',side_effect=lambda x:cur if x==g.LEGACY_ACCOUNT else target), \
             patch.object(g.os,'listxattr',return_value=['system.posix_acl_access'],create=True), \
             patch.object(g.os,'fchown') as change:
            with self.assertRaisesRegex(RuntimeError,'ACL'): g.custody(self.v,target)
            change.assert_not_called()
    def test_stdlib_probe_is_readonly_and_never_creates_missing_db(self):
        import sqlite3,subprocess,sys
        self.db.unlink()
        with sqlite3.connect(self.db) as db: db.execute('CREATE TABLE t(x)')
        cmd=lambda path:[sys.executable,'-I','-B','-c',g.SQLITE_PROBE,str(path)]
        before=self.db.read_bytes()
        good=subprocess.run(cmd(self.db),capture_output=True)
        self.assertEqual((good.returncode,good.stdout), (0,b''))
        self.assertEqual(self.db.read_bytes(),before)
        missing=self.d/'does-not-exist.db'
        bad=subprocess.run(cmd(missing),capture_output=True)
        self.assertNotEqual(bad.returncode,0); self.assertFalse(missing.exists())
if __name__=='__main__': unittest.main()
