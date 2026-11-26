"""Synthetic, unprivileged proof for the exact five-file owner updater."""
from pathlib import Path
from tempfile import TemporaryDirectory
import fcntl
import hashlib
import json
import os
import runpy
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'tools' / 'assertion-memory-mac-v2'


class MacCaptureV2UpdaterTest(unittest.TestCase):
    def setUp(self):
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.home = Path(td.name).resolve()
        self.module = self.home / '.codex/jarvis/modules/assertion-memory'
        self.lib = self.module / 'lib'
        self.lib.mkdir(parents=True)
        self.binding = self.module / 'mcp/installed.json'
        self.binding.parent.mkdir()
        self.old = {}
        self.new = {}
        self.api = runpy.run_path(str(SCRIPT))
        self.run_updater = self.api['run']
        self.globals = self.run_updater.__globals__
        self.hash = self.api['git_blob']
        for name in self.api['PINS']:
            self.old[name] = b'# synthetic old ' + name.encode() + b'\n'
            self.new[name] = b'# synthetic new ' + name.encode() + b'\n'
            self.write(self.lib/name, self.old[name], 0o644)
        self.init = b'# unchanged hook\n'
        self.write(self.lib/'__init__.py', self.init, 0o644)
        self.untouched = self.module.parent/'another-module'
        self.untouched.mkdir()
        self.other_code = self.untouched/'kept.py'
        self.write(self.other_code, b'# unrelated module\n', 0o644)
        url = 'https://example.test/memory-api-prod'
        timestamp = '2026-01-01T00:00:00Z'
        digest = hashlib.sha256(('private_base_http\0prod\0'+url+'\0'+timestamp).encode()).hexdigest()
        self.state_parent = self.home/'state'
        self.state_parent.mkdir(mode=0o700)
        self.state = self.state_parent/digest
        self.state.mkdir(mode=0o700)
        self.outbox = self.state/'outbox.sqlite3'
        self.write(self.outbox, b'owner state never opened by updater', 0o600)
        self.lock = self.state/'worker.lock'
        self.write(self.lock, b'1234', 0o600)
        binding = {'harness':'codex','connection':{'transport':'private_base_http','url':url},
                   'capture':{'enabled':True,'capture_batch':True,'fresh_since':timestamp,
                              'state_dir':str(self.state_parent)},'endpoint_sha256':digest}
        self.raw_binding = json.dumps(binding).encode()
        self.write(self.binding,self.raw_binding,0o600)
        patched = dict(PINS={n:(self.hash(self.old[n]),self.hash(self.new[n])) for n in self.old},
                       INIT=self.hash(self.init), source=lambda _: self.new)
        stack = patch.dict(self.globals,patched)
        stack.start()
        self.addCleanup(stack.stop)

    @staticmethod
    def write(path, body, mode):
        path.write_bytes(body)
        path.chmod(mode)

    def call(self,mode,**options):
        return self.run_updater(self.home,self.home,mode=mode,**options)

    def apply(self):
        return self.call('apply',quiesced=True,prod=self.api['PROD_BASE'])

    def same(self, expected):
        self.assertEqual({name:(self.lib/name).read_bytes() for name in self.old},expected)
        self.assertEqual(self.raw_binding,self.binding.read_bytes())
        self.assertEqual(b'owner state never opened by updater',self.outbox.read_bytes())
        self.assertEqual(b'1234',self.lock.read_bytes())
        self.assertEqual(b'# unrelated module\n',self.other_code.read_bytes())
        self.assertEqual(self.init,(self.lib/'__init__.py').read_bytes())

    def test_plan_apply_and_no_clear_rollback_fixed_files_only(self):
        self.assertEqual('preflight_ok_five_files_only',self.call('plan'))
        self.assertFalse((self.module/self.api['BACKUP']).exists())
        self.same(self.old)
        with self.assertRaisesRegex(ValueError,'Confirm'):
            self.call('apply')
        with self.assertRaisesRegex(ValueError,'First verify'):
            self.call('apply',quiesced=True,prod='wrong-stage')
        self.assertEqual('installed_five_files',self.apply())
        self.same(self.new)
        self.assertEqual('already_current',self.apply())
        with self.assertRaisesRegex(ValueError,'no database clear'):
            self.call('rollback',quiesced=True)
        self.assertEqual('rolled_back_five_files',self.call('rollback',quiesced=True,no_clear=True))
        self.same(self.old)

    def test_active_worker_refuses_mutation_and_plan_still_readonly(self):
        with self.lock.open('r') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertEqual('preflight_ok_five_files_only',self.call('plan'))
            with self.assertRaisesRegex(ValueError,'still holds'):
                self.apply()
        self.assertFalse((self.module/self.api['BACKUP']).exists())
        self.same(self.old)

    def test_atomic_failure_restores_previous_and_leaves_rollback_receipt(self):
        atomic = self.api['atomic']
        def fail_after_one_write(path,body,mode):
            atomic(path,body,mode)
            if path == self.lib/'frontier.py' and body == self.new['frontier.py']:
                raise OSError('synthetic failure after rename')
        with patch.dict(self.globals,atomic=fail_after_one_write):
            with self.assertRaisesRegex(OSError,'synthetic failure'):
                self.apply()
        self.same(self.old)
        self.assertTrue((self.module/self.api['BACKUP']).is_dir())

    def test_unknown_original_and_backup_binding_change_fail_closed(self):
        target = self.lib/'capture_worker.py'
        self.write(target,b'# local change',0o644)
        with self.assertRaisesRegex(ValueError,'neither'):
            self.call('plan')
        with self.assertRaisesRegex(ValueError,'exact upgrade'):
            self.apply()
        self.write(target,self.old[target.name],0o644)
        self.assertEqual('installed_five_files',self.apply())
        self.write(self.binding,self.raw_binding+b' ',0o600)
        with self.assertRaisesRegex(ValueError,'profile changed'):
            self.call('rollback',quiesced=True,no_clear=True)
        self.write(self.binding,self.raw_binding,0o600)
        self.same(self.new)
