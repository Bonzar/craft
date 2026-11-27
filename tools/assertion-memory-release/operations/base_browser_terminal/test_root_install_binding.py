from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import os
import root_install_binding as binding

class Binding(TestCase):
    def test_recovery_note_stable_and_exact_no_replacement(self):
        with TemporaryDirectory() as tmp:
            base=Path(tmp); record=base/'exact-journal'
            def regular(fd,name,mode):
                x=base/name
                self.assertEqual(x.stat().st_mode&0o777,mode)
                return x.read_bytes()
            with (patch.object(binding,'RECORD',record),
                    patch.object(binding,'_open_parent',lambda p:os.open(base,os.O_DIRECTORY)),
                    patch.object(binding,'_regular_at',side_effect=regular),
                    patch('os.fchown',return_value=None)):
                state=binding.RootRecovery()
                self.assertIsNone(state.read())
                state.write('a'*64)
                with self.assertRaises(FileExistsError):state.write('b'*64)
                self.assertEqual(state.read(),'a'*64)
                with self.assertRaises(ValueError):state.finish('b'*64)
                state.finish('a'*64)
                self.assertFalse(record.exists())

    def test_preflight_preserves_root_native_journal(self):
        with TemporaryDirectory() as tmp:
            journal=Path(tmp)/'existing-pending'
            journal.touch()
            with (patch.object(binding,'ACTIVE_RECEIVER_JOURNAL',journal),
                    patch.object(binding,'apply_exact',side_effect=lambda **kw:kw['namespace_preflight']())):
                self.assertFalse(binding.apply_versioned_source(verified_bundle=(),
                    immutable_bundle_sha='a'*64,approve_exact_bundle=lambda *_:True,
                    verify_quiesced=lambda:self.fail('does not read when pending')))
            self.assertTrue(journal.exists())
