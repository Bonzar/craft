import tempfile
import unittest
from pathlib import Path, PurePosixPath
from hashlib import sha256
from browser_contract import FILES, NAME, WIRE, qualified


class BrowserContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = {}

    def add(self, item, value):
        path = self.root/item
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(value)
        self.manifest[PurePosixPath(item)] = sha256(value).hexdigest()

    def complete(self):
        for f in FILES:
            self.add(f, b'fixture\n')
        self.add(NAME, WIRE)

    def test_legacy_and_unexpected_member(self):
        self.assertFalse(qualified(self.root, self.manifest, expected=False))
        self.add(NAME,WIRE)
        with self.assertRaisesRegex(ValueError,'unexpected'):
            qualified(self.root,self.manifest,expected=False)

    def test_marked_exact_bytes_are_manifest_bound(self):
        self.complete()
        self.assertTrue(qualified(self.root,self.manifest,expected=True))
        (self.root/FILES[0]).write_bytes(b'changed\n')
        with self.assertRaisesRegex(ValueError,'digest_or_version'):
            qualified(self.root,self.manifest,expected=True)

    def test_unknown_version_and_missing_checksum_denied(self):
        self.complete()
        self.add(NAME,b'assertion-memory-base-browser-listener-v2\n')
        with self.assertRaisesRegex(ValueError,'digest_or_version'):
            qualified(self.root,self.manifest,expected=True)
        self.add(NAME,WIRE)
        del self.manifest[PurePosixPath(FILES[0])]
        with self.assertRaisesRegex(ValueError,'digest_or_version'):
            qualified(self.root,self.manifest,expected=True)

    def test_symlink_denied_even_if_target_matches(self):
        self.complete()
        name=self.root/FILES[0]
        name.unlink()
        name.symlink_to(self.root/FILES[1])
        with self.assertRaisesRegex(ValueError,'not_regular'):
            qualified(self.root,self.manifest,expected=True)
