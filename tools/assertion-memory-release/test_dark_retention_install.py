"""Dark host install proof in an isolated root-shaped fixture, no live service."""
from contextlib import ExitStack, redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
import json
from types import SimpleNamespace
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

    def run_entrypoint(self, mode, baseline=None):
        pins=','.join(d.sha256(self.new[k]).hexdigest() for k in d.MODULES)
        args=['fixture',mode,'a'*40,pins]
        if baseline is not None:
            args.append(baseline)
        output=StringIO()
        with ExitStack() as scope:
            scope.enter_context(patch.object(d.sys,'argv',args))
            scope.enter_context(patch.object(d.sys,'flags',SimpleNamespace(isolated=True)))
            scope.enter_context(patch.object(d.sys,'dont_write_bytecode',True))
            scope.enter_context(patch.object(d.os,'geteuid',return_value=0))
            scope.enter_context(patch.object(d.os,'getegid',return_value=0))
            scope.enter_context(patch.object(d,'root_dir'))
            scope.enter_context(patch.object(d,'source_bytes',return_value=self.new))
            scope.enter_context(redirect_stdout(output))
            d.main()
        return json.loads(output.getvalue())

    def test_read_only_cli_does_not_lock_or_apply(self):
        with patch.object(d,'scope_locks') as locks,patch.object(d,'apply') as apply:
            view=self.run_entrypoint('--check')
        locks.assert_not_called()
        apply.assert_not_called()
        self.assertFalse(view['retention_armed'])
        self.assertEqual((view['files_written'],view['locks_acquired']),(0,0))

    def test_recovery_created_while_waiting_cannot_be_ignored_after_locks(self):
        baseline=d.capture_original(self.new)[1]
        marker=self.root/'.dark-retention-rollback-previous'
        def acquired(_stack):
            marker.mkdir()
        with patch.object(d,'scope_locks',side_effect=acquired),patch.object(d,'apply') as apply:
            with self.assertRaisesRegex(ValueError,'unsettled_dark_controller_backup'):
                self.run_entrypoint('--apply',baseline)
        apply.assert_not_called()
        self.assertTrue(marker.is_dir())

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
