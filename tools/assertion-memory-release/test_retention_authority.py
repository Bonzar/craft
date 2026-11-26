from contextlib import contextmanager
import fcntl
import json
import os
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
