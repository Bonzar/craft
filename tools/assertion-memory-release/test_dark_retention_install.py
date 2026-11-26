"""Dark host install proof in an isolated root-shaped fixture, no live service."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=spec_from_file_location('install_dark_fixture',HERE/'install-dark-retention-base-test.py')
d=module_from_spec(spec)
spec.loader.exec_module(d)


class DarkInstall(unittest.TestCase):
    def setUp(self):
        fixture=TemporaryDirectory()
        self.addCleanup(fixture.cleanup)
        self.root=Path(fixture.name).resolve()
        self.new={name:f'# signed exact fixture {name}\n'.encode() for name in d.MODULES}
        self.pointers={'stable':(1,'pointer')}
        self.prior={}
        for name in d.MODULES:
            p=self.root/name
            if name in d.NEEDED:
                p.write_bytes(f'# prior module {name}\n'.encode())
                self.prior[name]=(p.read_bytes(),d.sig(p.lstat()))
            else:
                self.prior[name]=(None,None)
        marker=self.root/'foreign.fixture'
        marker.write_bytes(b'untouched')
        ctl=patch.object(d,'CONTROL',self.root)
        ctl.start();self.addCleanup(ctl.stop)
        reader=patch.object(d,'read_root_file',side_effect=lambda p:(p.read_bytes(),d.sig(p.lstat())))
        reader.start();self.addCleanup(reader.stop)
        snap=patch.object(d,'read_snapshot',return_value=self.pointers)
        snap.start();self.addCleanup(snap.stop)

    def test_only_exact_modules_installed_in_dark_mode(self):
        with patch.object(d,'import_check') as check:
            d.apply(self.new,self.prior,self.pointers)
        check.assert_called_once_with()
        self.assertEqual({k:(self.root/k).read_bytes() for k in self.new},self.new)
        self.assertEqual({x.name for x in self.root.iterdir()},set(d.MODULES)|{'foreign.fixture'})
        self.assertEqual((self.root/'foreign.fixture').read_bytes(),b'untouched')

    def test_failed_dark_import_restores_old_modules_and_removes_only_our_additions(self):
        with patch.object(d,'import_check',side_effect=ValueError('synthetic import failure')):
            with self.assertRaisesRegex(ValueError,'synthetic import failure'):
                d.apply(self.new,self.prior,self.pointers)
        self.assertEqual({x.name for x in self.root.iterdir()},d.NEEDED|{'foreign.fixture'})
        self.assertEqual({k:(self.root/k).read_bytes() for k in d.NEEDED},
                         {k:self.prior[k][0] for k in d.NEEDED})
        self.assertEqual((self.root/'foreign.fixture').read_bytes(),b'untouched')

    def test_changed_baseline_refuses_foreign_bytes_and_reverses_our_earlier_changes(self):
        changed=self.root/'base_payload.py';changed.write_bytes(b'changed independently')
        with patch.object(d,'import_check') as check:
            with self.assertRaisesRegex(ValueError,'baseline_changed'):
                d.apply(self.new,self.prior,self.pointers)
        check.assert_not_called()
        self.assertEqual(changed.read_bytes(),b'changed independently')
        self.assertEqual((self.root/'release_core.py').read_bytes(),self.prior['release_core.py'][0])
        self.assertFalse((self.root/'release_retention.py').exists())
        self.assertFalse((self.root/'retention_authority.py').exists())


if __name__=='__main__':
    unittest.main()
