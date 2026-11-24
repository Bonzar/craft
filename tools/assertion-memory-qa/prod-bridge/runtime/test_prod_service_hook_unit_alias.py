from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase,main
from unittest.mock import patch
import prod_service_hook_fenced_v2 as h

class TrustedUnit(TestCase):
    unit=h.UNITS[0]
    path=Path('/etc/systemd/user')/unit
    alias=Path('/etc/xdg/systemd/user')/unit

    def test_exact_path_and_verified_alias(self):
        for fragment in (self.path,self.alias):
            with patch.object(Path,'resolve',lambda p,strict=False: self.path), \
                 patch.object(Path,'stat',lambda p: SimpleNamespace(st_uid=0,
                   st_mode=(0o040755 if p in {self.path.parent,self.alias.parent} else 0o100644),
                   st_dev=5,st_ino=42)), patch.object(h,'checked_file') as checked:
                h.trusted_unit_fragment(self.unit,{'FragmentPath':str(fragment),'DropInPaths':''})
                checked.assert_called_once_with(self.path,owner=0,limit=16384)

    def test_unknown_alias_and_dropin_rejected(self):
        for fragment,dropin in [(Path('/tmp')/self.unit,''),(self.alias,'/etc/dropin')]:
            with patch.object(Path,'resolve') as resolve,self.assertRaises(ValueError):
                h.trusted_unit_fragment(self.unit,{'FragmentPath':str(fragment),'DropInPaths':dropin})
                resolve.assert_not_called()

    def test_alias_points_to_other_file_rejected(self):
        with patch.object(Path,'resolve',lambda p,strict=False:p),self.assertRaisesRegex(ValueError,'alias'):
            h.trusted_unit_fragment(self.unit,{'FragmentPath':str(self.alias)})

    def test_inode_or_root_owner_mismatch_rejected(self):
        for changed_inode,changed_uid in ((43,0),(42,500)):
            def fake_stat(p):
                parent=p in {self.path.parent,self.alias.parent}
                return SimpleNamespace(st_mode=0o040755 if parent else 0o100644,
                          st_uid=changed_uid if p==self.alias else 0,st_dev=5,
                          st_ino=changed_inode if p==self.alias else 42)
            with patch.object(Path,'resolve',lambda p,strict=False:self.path), \
                 patch.object(Path,'stat',fake_stat),patch.object(h,'checked_file'), \
                 self.assertRaisesRegex(ValueError,'identity differs'):
                h.trusted_unit_fragment(self.unit,{'FragmentPath':str(self.alias)})

if __name__=='__main__':main()
