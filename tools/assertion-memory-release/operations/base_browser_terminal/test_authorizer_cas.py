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

    def test_exact_durable_ack_after_directory_fsync_fault(self):
        import stat
        with TemporaryDirectory() as tmp:
            path=Path(tmp); uid=os.getuid()
            _write(path,uid,None,b'protected pending')
            native=os.fsync
            def crash_after_replace(fd):
                if stat.S_ISDIR(os.fstat(fd).st_mode):
                    raise OSError('fixture directory fsync fault')
                return native(fd)
            with patch('authorizer_cas.os.fsync',side_effect=crash_after_replace):
                with self.assertRaises(OSError):
                    _write(path,uid,b'protected pending',b'exact final')
            state=path/STATE
            self.assertEqual(state.read_bytes(),b'exact final')
            before=state.stat().st_ino
            with patch('authorizer_cas.os.fsync',wraps=native) as sync:
                _write(path,uid,None,b'exact final',ack_identical=True)
                sync.assert_called_once()
            self.assertEqual(state.stat().st_ino,before)
            with self.assertRaises(ValueError):
                _write(path,uid,None,b'different final',ack_identical=True)
