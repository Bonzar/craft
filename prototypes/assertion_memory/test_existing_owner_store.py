import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from apps.graph_viewer.server.auth import OwnerStore
from apps.graph_viewer.server.existing_owner_store import ExistingOwnerStore

class NoInit(TestCase):
    def test_same_store_no_byte_or_metadata_changes_then_existing_auth(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'owner'
            standard=OwnerStore(path,'fictional-very-long-password')
            old_token, old_csrf=standard.login('fictional-very-long-password','existing-client')
            before=standard.path.stat()
            sha=hashlib.sha256(standard.path.read_bytes()).hexdigest()
            opened=ExistingOwnerStore(path)
            after=standard.path.stat()
            self.assertEqual((before.st_ino,before.st_mtime_ns,before.st_mode),
                             (after.st_ino,after.st_mtime_ns,after.st_mode))
            self.assertEqual(sha,hashlib.sha256(standard.path.read_bytes()).hexdigest())
            self.assertEqual(opened.session_digest(hashlib.sha256(old_token.encode()).hexdigest()),old_csrf)
            self.assertTrue(opened.login('fictional-very-long-password','fixture-local'))
            link=Path(tmp)/'link'
            link.symlink_to(path,target_is_directory=True)
            with self.assertRaises(ValueError):
                ExistingOwnerStore(link)

    def test_missing_or_empty_never_initializes(self):
        with TemporaryDirectory() as tmp:
            missing=Path(tmp)/'missing'
            with self.assertRaises(FileNotFoundError):
                ExistingOwnerStore(missing)
            self.assertFalse(missing.exists())
            standard=OwnerStore(missing)
            with self.assertRaises(ValueError):
                ExistingOwnerStore(missing)
            self.assertEqual(standard.path.name,'viewer.sqlite3')
