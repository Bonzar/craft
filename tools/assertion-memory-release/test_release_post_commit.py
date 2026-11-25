"""Test only shared release-lock and transaction semantics with fictional files."""
import fcntl
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest

import release_core as core


def digest(data):
    return sha256(data).hexdigest()


def archive(data):
    out=BytesIO()
    with tarfile.open(fileobj=out,mode='w',format=tarfile.USTAR_FORMAT) as tar:
        member=tarfile.TarInfo('fictional')
        member.size=len(data)
        tar.addfile(member,BytesIO(data))
    return out.getvalue()


class SharedPostCommitTest(unittest.TestCase):
    def setUp(self):
        work=TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root=Path(work.name).resolve()
        (self.root/'releases').mkdir()
        self.events=[]
        self.fail=None

    def deploy(self,data,callback=None):
        payload=archive(data)
        spec=core.ArchiveSpec('r|',100_000,100_000,1,lambda item,name:item.isfile() and name.as_posix()=='fictional',False)
        return core.deploy(BytesIO(payload),digest(payload),root=self.root,spec=spec,
                           validate=lambda p:digest((p/'fictional').read_bytes()),
                           hook=self.hook,after_committed=callback)

    def hook(self,action,target):
        self.events.append((action,target.name))
        if action=='smoke' and target.name==self.fail:
            raise ValueError('fictional smoke failure')

    def test_committed_hook_sees_same_lock_and_no_pending_journal(self):
        def observed(root,target):
            self.assertEqual(root,self.root)
            self.assertEqual(core.current_target(root),str(target))
            self.assertFalse((root/core.PENDING).exists())
            self.assertTrue(core.is_prepared(target))
            self.assertEqual(self.events[-1],('smoke',target.name))
            with (root/'deploy.lock').open('rb') as contender:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(contender,fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.events.append(('committed',target.name))
        current=self.deploy(b'candidate',observed)
        self.assertEqual(self.events[-1],('committed',current))

    def test_rolled_back_candidate_never_triggers_committed_hook(self):
        prior=self.deploy(b'prior')
        self.fail=digest(b'failed')
        called=[]
        with self.assertRaisesRegex(ValueError,'fictional smoke failure'):
            self.deploy(b'failed',lambda *args:called.append(args))
        self.assertEqual(called,[])
        self.assertEqual(self.events[-2:],[('restore',prior),('smoke',prior)])
        self.assertEqual((self.root/'current').readlink().name,prior)
        self.assertFalse((self.root/core.PENDING).exists())

    def test_hook_error_leaves_committed_activation_and_logs_only_fixed_message(self):
        def warning(*_):
            raise RuntimeError('fictional sensitive error detail')
        with self.assertLogs('release_core',level='WARNING') as logs:
            committed=self.deploy(b'current',warning)
        self.assertEqual(logs.output,['WARNING:release_core:release_post_commit_housekeeping_incomplete'])
        self.assertEqual((self.root/'current').readlink().name,committed)
        self.assertFalse((self.root/core.PENDING).exists())


if __name__=='__main__':
    unittest.main()
