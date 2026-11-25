"""Real shared receiver tests against the pinned seed, no remote writes."""
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import subprocess
import tempfile
import unittest

import release_core
from visual_policy import deploy_verified, make_hook

ROOT=Path(__file__).resolve().parent
SEED_SHA='5f0c19c0e08a052853e373bd4128cbbafa0daaadd5519d211dc0e2861acce250'
SEED_ID='04c6cce2c16881a1af048b699e8d7229e119c283ed84d4f991f9d0c2c4f939eb'


def hash_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def variant(seed):
    # Test only: use synthetic HTML instead of the real client on the second immutable release.
    with tarfile.open(seed) as origin:
        files={entry.name:origin.extractfile(entry).read() for entry in origin if entry.name!='manifest.json'}
    files['index.html']=b'<html>synthetic release B</html>'
    manifest=json.dumps({k:hash_bytes(v) for k,v in files.items()},sort_keys=True,separators=(',', ':')).encode()+b'\n'
    files['manifest.json']=manifest
    dest=io.BytesIO()
    with tarfile.open(fileobj=dest,mode='w',format=tarfile.USTAR_FORMAT) as archive:
        for name,data in sorted(files.items()):
            item=tarfile.TarInfo(name);item.size=len(data)
            archive.addfile(item,io.BytesIO(data))
    return dest.getvalue(),hash_bytes(manifest)


class IntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if 'VISUAL_SEED_PATH' in os.environ:
            cls.seed=Path(os.environ['VISUAL_SEED_PATH'])
            receipt=json.loads(Path(os.environ['VISUAL_SEED_RECEIPT']).read_bytes())
        else:
            draft=tempfile.TemporaryDirectory()
            cls.addClassCleanup(draft.cleanup)
            cls.seed=Path(draft.name)/'visual.tar'
            result=subprocess.run(['node',str(ROOT/'build-archive.mjs'),str(ROOT),str(cls.seed)],
                                  check=True,capture_output=True,text=True)
            receipt=json.loads(result.stdout)
        if receipt!={'archive_sha256':SEED_SHA,'release':SEED_ID,'bytes':1044480}:
            raise AssertionError('Visual assembler changed reviewed exact bytes')
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        (self.root/'releases').mkdir()

    def deploy_seed(self):
        self.assertEqual(hash_bytes(self.seed.read_bytes()),SEED_SHA)
        with self.seed.open('rb') as payload:
            self.assertEqual(deploy_verified(payload,SEED_SHA,root=self.root),SEED_ID)
        self.assertEqual((self.root/'current').readlink(),self.root/'releases'/SEED_ID)

    def test_exact_seed_replays_and_manifest_remains_verbatim(self):
        self.deploy_seed()
        self.deploy_seed()
        self.assertEqual(hash_bytes((self.root/'current'/'manifest.json').read_bytes()),SEED_ID)
        self.assertEqual((self.root/'current'/'.prepared-v1').read_bytes(),b'ok\n')

    def test_shared_automatic_rollback_restores_prior_verified_assets(self):
        self.deploy_seed()
        b,b_id=variant(self.seed)
        # Hook injection simulates a synchronous activation smoke failure; then the common core
        # must restore and smoke the previous release with the actual static product hook.
        real=make_hook(self.root)
        from unittest.mock import patch
        def check(action, location):
            if action=='smoke' and location.name==b_id:
                raise RuntimeError('injected application smoke error')
            real(action,location)
        with patch('visual_policy.make_hook', return_value=check):
            with self.assertRaisesRegex(RuntimeError,'smoke'):
                deploy_verified(io.BytesIO(b),hash_bytes(b),root=self.root)
        self.assertEqual((self.root/'current').readlink(),self.root/'releases'/SEED_ID)
        self.assertNotIn('synthetic release B',(self.root/'current'/'index.html').read_text())
        self.assertFalse((self.root/release_core.PENDING).exists())
        # A later valid request selects the new artifact and only then prunes the prior.
        self.assertEqual(deploy_verified(io.BytesIO(b),hash_bytes(b),root=self.root),b_id)
        self.assertFalse((self.root/'releases'/SEED_ID).exists())
        self.assertEqual({x.name for x in (self.root/'releases').iterdir()},{b_id})
        # Redeploy the archived immutable older release bytes; no local history needed.
        self.deploy_seed()
        self.assertFalse((self.root/'releases'/b_id).exists())
        self.assertEqual({x.name for x in (self.root/'releases').iterdir()},{SEED_ID})

    def test_cleanup_does_not_follow_symlinks_or_remove_unverified_state(self):
        self.deploy_seed()
        unknown=self.root/'releases'/('f'*64)
        unknown.mkdir()
        (unknown/'source').write_text('outside the visual payload policy')
        elsewhere=self.root/'separate-state'
        elsewhere.mkdir()
        (elsewhere/'keep').write_text('not a visual asset')
        link=self.root/'releases'/('e'*64)
        link.symlink_to(elsewhere)
        b,b_id=variant(self.seed)
        self.assertEqual(deploy_verified(io.BytesIO(b),hash_bytes(b),root=self.root),b_id)
        self.assertTrue((unknown/'source').is_file())
        self.assertTrue(link.is_symlink())
        self.assertTrue((elsewhere/'keep').is_file())
        self.assertFalse((self.root/'releases'/SEED_ID).exists())
        self.assertEqual((self.root/'current').resolve(),(self.root/'releases'/b_id).resolve())

    def test_cleanup_observes_committed_pointer_under_existing_writer_fence(self):
        import fcntl
        from unittest.mock import patch
        from visual_policy import prune_inactive_visual
        observed=[]
        def check(root,active):
            self.assertEqual((root/'current').readlink(),active)
            self.assertFalse((root/release_core.PENDING).exists())
            with (root/'deploy.lock').open('rb') as another:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(another,fcntl.LOCK_EX|fcntl.LOCK_NB)
            observed.append(active.name)
            prune_inactive_visual(root,active)
        with patch('visual_policy.prune_inactive_visual',side_effect=check):
            self.deploy_seed()
        self.assertEqual(observed,[SEED_ID])


if __name__=='__main__': unittest.main()
