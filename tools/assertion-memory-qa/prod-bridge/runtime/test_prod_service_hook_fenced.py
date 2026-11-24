import json
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase,main
from unittest.mock import patch
import prod_service_hook_fenced as h

class FencedStart(TestCase):
    policy=SimpleNamespace(db_dev=42,db_inode=99)
    release=Path('/unused')/h.FIRST_RELEASE

    def test_first_uses_exact_guard_and_not_sysctl(self):
        def meta(p):
            mode=0o100755 if p==h.FIRST_GUARD else 0o040755
            return SimpleNamespace(st_uid=0,st_gid=0,st_nlink=1,st_mode=mode)
        with patch.object(h,'first_maintenance_complete',return_value=False), \
             patch.object(Path,'lstat',meta), patch.object(Path,'resolve',lambda p:p), \
             patch.object(h,'run_command') as cmd, patch.object(h,'systemd') as systemd:
            h.start_services('restart',self.release,self.policy)
            cmd.assert_called_once_with([str(h.FIRST_GUARD),'receiver-first-start'],timeout=45)
            systemd.assert_not_called()
            cmd.side_effect=OSError('revoked')
            with self.assertRaises(OSError): h.start_services('restart',self.release,self.policy)
            systemd.assert_not_called()

    def test_first_restore_and_other_archive_refused_early(self):
        with patch.object(h,'first_maintenance_complete',return_value=False), \
             patch.object(h,'run_command') as cmd, patch.object(h,'systemd') as systemd:
            for role,release in (('restore',self.release),('restart',Path('/other/archive'))):
                with self.assertRaises(ValueError): h.start_services(role,release,self.policy)
            cmd.assert_not_called()
            systemd.assert_not_called()

    def test_missing_or_untrusted_guard_never_falls_back(self):
        with patch.object(h,'first_maintenance_complete',return_value=False), \
             patch.object(Path,'lstat',side_effect=FileNotFoundError), patch.object(h,'systemd') as systemd:
            with self.assertRaises(FileNotFoundError): h.start_services('restart',self.release,self.policy)
            systemd.assert_not_called()
        m=SimpleNamespace(st_uid=100,st_gid=0,st_nlink=1,st_mode=0o040755)
        with patch.object(h,'first_maintenance_complete',return_value=False), \
             patch.object(Path,'lstat',return_value=m), patch.object(Path,'resolve',lambda p:p), \
             patch.object(h,'systemd') as ctl:
            with self.assertRaises(ValueError): h.start_services('restart',self.release,self.policy)
            ctl.assert_not_called()

    def test_stable_requires_root_inode_and_exact_release(self):
        marker={'version':1,'state':'settled','inode':[42,99]}
        meta=SimpleNamespace(st_uid=0,st_gid=0,st_nlink=1,st_mode=0o100644)
        with patch.object(Path,'lstat',return_value=meta), \
             patch.object(h,'checked_file',return_value=json.dumps(marker).encode()), \
             patch.object(h,'run_command') as cmd, patch.object(h,'systemd') as systemd:
            self.assertTrue(h.first_maintenance_complete(self.policy))
            with patch.object(h,'first_maintenance_complete',return_value=True), \
                 patch.object(Path,'resolve',lambda p:p), \
                 patch.object(Path,'lstat',lambda p: SimpleNamespace(st_uid=0,st_gid=0,st_nlink=1,st_mode=(0o100755 if p==h.FIRST_GUARD else 0o040755))):
                h.start_services('restore',Path('/any'),self.policy)
            cmd.assert_called_once_with([str(h.FIRST_GUARD),'receiver-restart-after-settlement'],timeout=45)
            systemd.assert_not_called()
        for bad in ({**marker,'inode':[42,98]},{**marker,'state':'x'},None):
            with patch.object(Path,'lstat',return_value=meta), \
                 patch.object(h,'checked_file',return_value=json.dumps(bad).encode()):
                with self.assertRaises(ValueError): h.first_maintenance_complete(self.policy)
        with patch.object(Path,'lstat',side_effect=FileNotFoundError):
            self.assertFalse(h.first_maintenance_complete(self.policy))

if __name__=='__main__': main()
