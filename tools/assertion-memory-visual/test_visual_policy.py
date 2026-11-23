import hashlib, json, pathlib, tarfile, tempfile, unittest
from pathlib import PurePosixPath
from visual_policy import accept_member, make_hook, retained, validate
from test_asset_origin import ORIGINAL


class TestVisualPolicy(unittest.TestCase):
    def setUp(self):
        dir=tempfile.TemporaryDirectory()
        self.addCleanup(dir.cleanup)
        self.root=pathlib.Path(dir.name)
        (self.root/'assets').mkdir()
        for k,v in ORIGINAL.items(): (self.root/k).write_bytes(v)
        d={k:hashlib.sha256(v).hexdigest() for k,v in ORIGINAL.items()}
        self.payload=(json.dumps(d, sort_keys=True, separators=(',',':'))+'\n').encode()
        (self.root/'manifest.json').write_bytes(self.payload)

    def test_stable_id_and_explicit_shape(self):
        self.assertEqual(validate(self.root),hashlib.sha256(self.payload).hexdigest())
        (self.root/'additional').write_bytes(b'not approved')
        with self.assertRaisesRegex(ValueError,'membership'): validate(self.root)

    def test_tamper_and_symlink_deny(self):
        (self.root/'index.html').write_bytes(b'replaced')
        with self.assertRaisesRegex(ValueError,'digest'): validate(self.root)
        (self.root/'index.html').unlink()
        (self.root/'index.html').symlink_to(self.root/'manifest.json')
        with self.assertRaisesRegex(ValueError,'member'): validate(self.root)

    def test_member_policy_never_accepts_the_ownership_marker(self):
        entry=tarfile.TarInfo('manifest.json'); entry.size=50
        self.assertTrue(accept_member(entry,PurePosixPath('manifest.json')))
        self.assertFalse(accept_member(entry,PurePosixPath('.prepared-v1')))
        entry.size=1025
        self.assertFalse(accept_member(entry,PurePosixPath('manifest.json')))
        entry.type=tarfile.SYMTYPE
        self.assertFalse(accept_member(entry,PurePosixPath('assets/index-ab.js')))

    def test_no_restart_scoped_pointer_and_retained_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=pathlib.Path(tmp)
            release=base/'releases'/validate(self.root)
            import shutil
            shutil.copytree(self.root,release)
            (release/'.prepared-v1').write_text('core-owned')
            retained(self.root,release)
            with self.assertRaisesRegex(ValueError,'membership'): validate(release)
            hook=make_hook(base)
            hook('prepare',release)
            hook('restart',release)
            hook('stop',base)
            (base/'current').symlink_to(release)
            hook('smoke',release)
            hook('restore',release)
            with self.assertRaisesRegex(ValueError,'still_present'): hook('stop',base)
            with self.assertRaisesRegex(ValueError,'scope'): hook('smoke',self.root)
if __name__=='__main__': unittest.main()
