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
SEED_SHA='e740e9d0bbc634a7ae96e7604ccc06f3ed0e6b1d3a4e8bb6cdeffdb374acbec0'
SEED_ID='2703e6ed52f9be870405305be7d6379974bf551b4ab91f5a5f0c67a0735c19f3'


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
        if receipt!={'archive_sha256':SEED_SHA,'release':SEED_ID,'bytes':1085440}:
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
        # A later valid request can select the new artifact; old URL keeps the original.
        self.assertEqual(deploy_verified(io.BytesIO(b),hash_bytes(b),root=self.root),b_id)
        self.assertNotIn('synthetic release B',(self.root/'releases'/SEED_ID/'index.html').read_text())


if __name__=='__main__': unittest.main()
