from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
import os
import controller_install_lease as lease

class InstallLock(TestCase):
    def test_real_existing_descriptors_exclude_overlap_and_never_create(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'existing'
            path.touch(mode=0o600)
            with lease._lock(path,owner=os.getuid(),mode=0o600):
                with self.assertRaises(BlockingIOError):
                    with lease._lock(path,owner=os.getuid(),mode=0o600): self.fail()
            missing=Path(tmp)/'missing'
            with self.assertRaises(FileNotFoundError):
                with lease._lock(missing,owner=os.getuid(),mode=0o600): self.fail()
            self.assertFalse(missing.exists())
            linked=Path(tmp)/'symlink'
            linked.symlink_to(path)
            with self.assertRaises(OSError):
                with lease._lock(linked,owner=os.getuid(),mode=0o600): self.fail()

    def test_root_path_order_not_external_or_user_configurable(self):
        from contextlib import contextmanager
        seen=[]
        @contextmanager
        def lock(path,*,owner,mode):
            seen.append((path,owner,mode)); yield
        with (patch('os.getuid',return_value=0),patch('os.geteuid',return_value=0),
                  patch.object(lease,'_lock',side_effect=lock)):
            with lease.existing_root_install_lease():
                self.assertEqual(seen,[(lease.DEPLOY,986,0o600),(lease.TRANSITION,0,0o644)])
