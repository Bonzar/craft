from contextlib import contextmanager
import fcntl
import json
import os
import shutil
from pathlib import Path
import tempfile
import unittest

import retention_authority as a

A='a'*40+'-'+'a'*64
B='b'*40+'-'+'b'*64

class AuthorityTest(unittest.TestCase):
    # Real /proc fdinfo and flock; injectable scan isolates synthetic scan outcomes.
    def test_packet_exact_and_cross_scope(self):
        data={'version':1,'scope':'base-test','store':'base','active':A,
              'inactive':B,'dev':21,'ino':22}
        raw=json.dumps(data).encode()+b'\n'
        self.assertEqual(a.request(raw,'base-test'),data)
        for malicious in (raw.replace(b'"ino": 22',b'"ino":22,"ino":23'),raw+b'\n',
                          raw.replace(B.encode(),b'../'+B.encode()),
                          raw.replace(b'"version": 1',b'"version": true')):
            with self.assertRaises(a.Denied):
                a.request(malicious,'base-test')
        with self.assertRaises(a.Denied):
            a.request(raw,'base-prod')

    def test_bind_or_mounted_descendant(self):
        host=('37',os.makedev(8,1),Path('/'),Path('/'))
        candidate=Path('/srv/example/releases/retired')
        good='37 21 8:1 / / rw - ext4 /dev/x rw\n'
        a.check_mounts(good,host,candidate,candidate,host[1])
        for mount in ('97 37 8:1 /srv/example /mnt/clone rw - ext4 /dev/x rw\n',
                      '97 37 7:7 / /srv/example/releases/retired/sub rw - ext4 /dev/y rw\n'):
            with self.assertRaises(a.Denied):
                a.check_mounts(good+mount,host,candidate,candidate,host[1])

    def test_ro_canonical_cloned_mount_requires_same_inode(self):
        host=('37',os.makedev(8,1),Path('/'),Path('/'))
        candidate=Path('/srv/example/releases/retired')
        clone='91 21 8:1 / / ro - ext4 /dev/x rw\n'
        a.check_mounts(clone,host,candidate,candidate,host[1],canonical_inode=lambda:True)
        for raw,probe in ((clone,None),(clone,lambda:False),
             (clone.replace(' / ro ',' / rw '),lambda:True),
             (clone.replace(' / ro ',' /moved ro '),lambda:True),
             (clone.replace('8:1 / / ro','8:1 /srv / ro'),lambda:True)):
            with self.assertRaises(a.Denied):
                a.check_mounts(raw,host,candidate,candidate,host[1],canonical_inode=probe)
        # A marker saying RO must not allow a mount directly over any child.
        with self.assertRaises(a.Denied):
            a.check_mounts(clone+'92 91 7:7 / /srv/example/releases/retired/sub ro - ext4 /dev/y rw\n',
                          host,candidate,candidate,host[1],canonical_inode=lambda:True)

    def test_rw_clone_cannot_expand_host_permissions_or_task_root(self):
        host=('37',os.makedev(8,1),Path('/'),Path('/'))
        candidate=Path('/srv/example/releases/retired')
        clone='91 21 8:1 / / rw,nosuid - ext4 /dev/x rw\n'
        a.check_mounts(clone,host,candidate,candidate,host[1],canonical_inode=lambda:True,
                      host_options={'rw','nosuid'},same_observer_root=True)
        for raw,root_ok,policy,probe in ((clone,False,{'rw'},lambda:True),
                                        (clone,True,{'ro','nosuid'},lambda:True),
                                        (clone,True,{'rw','nodev'},lambda:True),
                                        (clone,True,{'rw'},lambda:False),
                                        (clone.replace(' / rw', ' /relocated rw'),True,{'rw'},lambda:True)):
            with self.assertRaises(a.Denied):
                a.check_mounts(raw,host,candidate,candidate,host[1],canonical_inode=probe,
                              host_options=policy,same_observer_root=root_ok)

    @contextmanager
    def fixture(self):
        # Test this private fixture with the trusted collector's path ownership checker
        # swapped solely to permit the synthetic top-level writable /tmp ancestor.
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as work:
            root=Path(work)
            active=root/'releases'/A
            retired=root/'releases'/B
            active.mkdir(parents=True)
            retired.mkdir()
            (retired/'object').write_text('fixture')
            (root/'current').symlink_to(active)
            details=retired.stat()
            q={'store':'base','active':A,'inactive':B,'dev':details.st_dev,'ino':details.st_ino}
            def test_dir(path, owners):
                a.require(path.resolve(strict=True)==path and path.is_dir())
                return path.stat()
            fd=os.open(root/'deploy.lock',os.O_CREAT|os.O_RDWR,0o600)
            try:
                with patch.object(a,'real_dir',test_dir):
                    yield root,retired,q,fd
            finally:
                os.close(fd)

    def test_root_launcher_pins_refuse_content_drift_and_foreign_scope(self):
        from hashlib import sha256
        with self.fixture() as (root,_,q,fd):
            unit=root/'active.service';unit.write_text('[Service]\n')
            wrapper=root/'start.py';wrapper.write_text('pass\n')
            pin=lambda path:sha256(path.read_bytes()).hexdigest()
            pins={str(p):pin(p) for p in (unit,wrapper)}
            result=a.pinned_launchers(pins,roots=(root,),owner=os.getuid())
            self.assertEqual(set(result),set(pins))
            wrapper.write_text('new package path\n')
            with self.assertRaises(a.Denied):
                a.pinned_launchers(pins,roots=(root,),owner=os.getuid())
            with self.assertRaises(a.Denied):
                a.pinned_launchers(pins,roots=(root/'foreign',),owner=os.getuid())
            wrapper.unlink();wrapper.symlink_to(unit)
            with self.assertRaises(a.Denied):
                a.pinned_launchers(pins,roots=(root,),owner=os.getuid())

    def test_exact_lock_item_identity_and_journal(self):
        with self.fixture() as (root,retired,q,fd):
            def check(q=q):
                a.proof(q,root,os.getuid(),fd,lambda *_: (0,0))
            with self.assertRaises(a.Denied):
                check()
            fcntl.flock(fd,fcntl.LOCK_EX)
            check()
            with self.assertRaises(a.Denied):
                check(dict(q,ino=q['ino']+1))
            (root/'.activation-pending-v1').symlink_to('missing')
            with self.assertRaises(a.Denied):
                check()

    def test_changed_process_seal_and_hardlink_fail(self):
        with self.fixture() as (root,retired,q,fd):
            fcntl.flock(fd,fcntl.LOCK_EX)
            seals=iter([1,2])
            with self.assertRaises(a.Denied):
                a.proof(q,root,os.getuid(),fd,lambda *_:next(seals))
            os.link(retired/'object',root/'alias')
            with self.assertRaises(a.Denied):
                a.proof(q,root,os.getuid(),fd,lambda *_:0)

    def test_proc_rejects_open_file_inode_alias_maps_and_lazy_args(self):
        with self.fixture() as (root,retired,q,fd):
            proc=root/'proc';proc.mkdir()
            self_ns=proc/'self/ns';self_ns.mkdir(parents=True)
            (self_ns/'pid').touch()
            host=proc/'1/ns';host.mkdir(parents=True)
            (host/'pid').symlink_to(self_ns/'pid')
            task=proc/'1/task/1';task.mkdir(parents=True)
            (task/'ns').mkdir();(task/'ns/mnt').touch()
            (task/'fd').mkdir()
            st=os.stat(retired)
            dev=f'{os.major(st.st_dev)}:{os.minor(st.st_dev)}'
            mount=f'37 21 {dev} / / rw - ext4 /dev/x rw\n'
            (proc/'self/mountinfo').write_text(mount)
            (task/'mountinfo').write_text(mount)
            (proc/'locks').touch()
            (task/'stat').write_text('1 (fictional) S '+' '.join(['0']*19))
            for name in ('cwd','root','exe'):
                (task/name).symlink_to(root)
            for name in ('maps','cmdline','environ'):
                (task/name).touch()
            tracked={a.pair(p.lstat()) for p in (retired,retired/'object')}
            self.assertEqual(a.scan(retired,tracked,proc),a.scan(retired,tracked,proc))
            alias=root/'not-in-candidate-path'
            alias.symlink_to(retired/'object')
            (task/'fd'/'9').symlink_to(alias)
            with self.assertRaises(a.Denied):
                a.scan(retired,tracked,proc)
            (task/'fd'/'9').unlink()
            p=retired/'object';s=p.lstat()
            (task/'maps').write_text(f'1000-2000 r--p 0 {os.major(s.st_dev):x}:{os.minor(s.st_dev):x} {s.st_ino} /different/alias\n')
            with self.assertRaises(a.Denied):
                a.scan(retired,tracked,proc)
            (task/'maps').write_text('')
            (task/'environ').write_bytes(b'PYTHONPATH='+os.fsencode(retired)+b'\0')
            with self.assertRaises(a.Denied):
                a.scan(retired,tracked,proc)
            (task/'environ').write_bytes(b'')
            # Walk the task's effective canonical path, not merely equal
            # values in mountinfo. The fake namespace root now reaches it.
            (task/'root').unlink()
            (task/'root').symlink_to('/')
            (task/'mountinfo').write_text(mount.replace('37 21 ','91 21 ').replace(' / rw -',' / ro -'))
            self.assertEqual(a.scan(retired,tracked,proc),a.scan(retired,tracked,proc))
            (task/'root').unlink()
            (task/'root').symlink_to(root)
            with self.assertRaises(OSError):
                a.scan(retired,tracked,proc)
            # Stock same-root writable namespace copy: actual candidate inode
            # is the same and the host's canonical filesystem is writable.
            (task/'root').unlink();(task/'root').symlink_to('/')
            (task/'mountinfo').write_text(mount.replace('37 21 ','91 21 '))
            a.scan(retired,tracked,proc)
            # Same ns but a second task has a different effective root; a
            # prior task's positive canonical result cannot cover that view.
            second=task.parent/'2'
            shutil.copytree(task,second,symlinks=True)
            (second/'ns/mnt').unlink()
            (second/'ns/mnt').symlink_to(task/'ns/mnt')
            self.assertEqual(a.pair((second/'ns/mnt').stat()),a.pair((task/'ns/mnt').stat()))
            (second/'root').unlink()
            (second/'root').symlink_to(root)
            with self.assertRaises(a.Denied):
                a.scan(retired,tracked,proc)

    def test_base_generated_links_are_lexically_sealed_not_followed(self):
        with self.fixture() as (_,retired,q,_):
            (retired/'venv/bin').mkdir(parents=True)
            (retired/'venv/lib64').symlink_to('lib')
            (retired/'venv/bin/python').symlink_to('/usr/bin/python3')
            (retired/'venv/bin/python3.12').symlink_to('python')
            find=lambda base=True: a.tree(retired,os.getuid(),q['dev'],base=base)
            before=find()
            self.assertEqual(before[str(retired/'venv/lib64')][-1],'lib')
            self.assertEqual(len(before),7)
            with self.assertRaises(a.Denied):
                find(False)
            p=retired/'venv/bin/python3.12'
            p.unlink()
            p.symlink_to('python3')
            self.assertNotEqual(find(),before)
            for bad in ('../python3','/tmp/python3','python3.12'):
                p.unlink()
                p.symlink_to(bad)
                with self.assertRaises(a.Denied):
                    find()
            p.unlink()
            (retired/'venv/bin/fake-python').symlink_to('/usr/bin/python3')
            with self.assertRaises(a.Denied):
                find()

if __name__=='__main__':
    unittest.main()
