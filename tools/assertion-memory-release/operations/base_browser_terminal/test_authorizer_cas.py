import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from authorizer_cas import _write,STATE,publish

class Cas(TestCase):
    def test_exact_prior_and_no_overlap_or_overwrite(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp); uid=os.getuid()
            _write(path,uid,None,b'admit old + new')
            with self.assertRaises(ValueError):
                _write(path,uid,None,b'forged overlapping record')
            _write(path,uid,b'admit old + new',b'old restored')
            self.assertEqual((path/STATE).read_bytes(),b'old restored')
            with self.assertRaises(ValueError):
                _write(path,uid,b'admit old + new',b'candidate ready')

    def test_symlink_and_wrong_role_denied(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp); uid=os.getuid()
            target=path/'untouched'
            target.write_bytes(b'no live access')
            (path/STATE).symlink_to(target)
            with self.assertRaises(OSError):
                _write(path,uid,None,b'no')
            self.assertEqual(target.read_bytes(),b'no live access')
        with patch('authorizer_cas.os.geteuid',return_value=986):
            with self.assertRaises(ValueError):
                publish(expected=None,replacement=b'no')
