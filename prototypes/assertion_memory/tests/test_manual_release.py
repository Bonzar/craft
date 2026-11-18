"""Synthetic code-only channel delivery; no personal data or processes."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from prototypes.assertion_memory import manual_release as release


class ManualReleaseTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / 'source'
        self.package = self.source / 'prototypes' / 'assertion_memory'
        self.package.mkdir(parents=True)
        (self.package / '__init__.py').write_text('VALUE = 42\n')
        (self.package / 'sample.py').write_text('from . import VALUE\n')
        self.channel = self.root / 'channel'
        self.sha = 'a' * 40

    def stage(self, sha=None):
        return release.stage_release(self.source, self.channel, sha or self.sha)

    def test_staging_excludes_data_tests_caches_and_preserves_imports(self):
        for name in ('.local/runtime/bin/python', 'tests/test_private.py',
                     '__pycache__/cache.py', 'state.sqlite3', 'weights.bin',
                     'desktop_test.py', 'private.json'):
            path = self.package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic excluded bytes')
        (self.package / 'requirements-runtime.txt').write_text('example==1\n')
        destination = self.stage()
        manifest = release.validate_release(destination)
        self.assertEqual(manifest['source_commit'], self.sha)
        self.assertEqual(set(manifest['files']), {
            'prototypes/assertion_memory/__init__.py',
            'prototypes/assertion_memory/sample.py',
            'prototypes/assertion_memory/requirements-runtime.txt',
        })
        result = subprocess.run([sys.executable, '-B', '-c',
            'from prototypes.assertion_memory.sample import VALUE; print(VALUE)'],
            cwd=destination, env={'PYTHONPATH': str(destination)},
            text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.strip(), '42')

    def test_normal_python_import_keeps_release_valid(self):
        destination = self.stage()
        subprocess.run([sys.executable, '-c',
            'from prototypes.assertion_memory.sample import VALUE'],
            cwd=destination, env={'PYTHONPATH': str(destination)}, check=True)
        release.validate_release(destination)

    def test_failed_atomic_switch_keeps_current_and_candidate(self):
        first = self.stage()
        release.promote_release(self.channel, self.sha)
        second = self.stage('b' * 40)
        replace = release.os.replace
        def fail_current(source, target):
            if Path(target).name == 'current':
                raise OSError('synthetic atomic replacement failure')
            return replace(source, target)
        with patch.object(release.os, 'replace', side_effect=fail_current):
            with self.assertRaises(OSError):
                release.promote_release(self.channel, 'b' * 40)
        self.assertEqual((self.channel / 'current').resolve(), first)
        self.assertTrue(second.exists())
        self.assertFalse(list(self.channel.glob('.current-*')))

    def test_invalid_hash_cannot_switch_current(self):
        old = self.stage()
        release.promote_release(self.channel, self.sha)
        candidate = self.stage('b' * 40)
        code = candidate / 'prototypes/assertion_memory/sample.py'
        code.chmod(0o644)
        code.write_text('tampered')
        with self.assertRaises(ValueError):
            release.promote_release(self.channel, 'b' * 40)
        self.assertEqual((self.channel / 'current').resolve(), old)

    def test_idempotent_promotion_and_rollback_keep_both_releases_and_database(self):
        database = self.root / 'memory.sqlite3'
        database.write_bytes(b'synthetic database bytes')
        first = self.stage()
        receipt = release.promote_release(self.channel, self.sha)
        self.assertTrue(receipt['changed'])
        self.assertEqual(self.stage(), first)
        self.assertFalse(release.promote_release(self.channel, self.sha)['changed'])
        second = self.stage('b' * 40)
        receipt = release.promote_release(self.channel, 'b' * 40)
        self.assertEqual(receipt['previous_release'], self.sha)
        self.assertTrue((self.channel / 'current').is_symlink())
        self.assertEqual((self.channel / 'current').resolve(), second)
        self.assertEqual((self.channel / 'previous').resolve(), first)
        self.assertEqual(release.release_info(self.channel / 'current')['source_commit'], 'b'*40)
        receipt = release.rollback_release(self.channel)
        self.assertEqual(receipt['release_id'], self.sha)
        self.assertEqual((self.channel / 'current').resolve(), first)
        self.assertTrue(second.exists())
        self.assertEqual(database.read_bytes(), b'synthetic database bytes')
        self.assertEqual(json.loads((self.channel / 'receipt.json').read_text()), receipt)

    def test_source_symlink_cannot_escape_into_release(self):
        external = self.root / 'external.py'
        external.write_text('SECRET = "synthetic"')
        (self.package / 'escape.py').symlink_to(external)
        with self.assertRaises(ValueError):
            self.stage()
        self.assertFalse((self.channel / 'current').exists())

    def test_extra_file_and_manifest_path_traversal_are_rejected(self):
        destination = self.stage()
        destination.chmod(0o755)  # Deliberately bypass immutable tree protection.
        (destination / 'untracked.sqlite3').write_bytes(b'synthetic')
        with self.assertRaises(ValueError):
            release.validate_release(destination)
        (destination / 'untracked.sqlite3').unlink()
        manifest_path = destination / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['files']['../outside'] = '0'*64
        manifest_path.chmod(0o644)
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            release.validate_release(destination)

    def test_existing_current_directory_is_never_replaced(self):
        self.stage()
        (self.channel / 'current').mkdir()
        database = self.channel / 'current' / 'state.sqlite3'
        database.write_bytes(b'synthetic protected bytes')
        with self.assertRaises(ValueError):
            release.promote_release(self.channel, self.sha)
        self.assertEqual(database.read_bytes(), b'synthetic protected bytes')


if __name__ == '__main__':
    unittest.main()
