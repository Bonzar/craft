"""Go готовится модулем, а сборка Codex получает его через адаптер."""

import hashlib
import importlib.util
import io
import json
import os
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from . import CORE_SOURCE, MODULES_DIR
from .test_installer import installer

GO_STUB = '''#!/bin/sh
if [ "$1" = version ]; then echo 'go version go1.24.7 linux/amd64'; exit; fi
echo "$GOCACHE|$GOPATH|$GOTOOLCHAIN" >> "$BUILD_LOG"
while [ $# -gt 0 ]; do
  if [ "$1" = -o ]; then shift; echo binary > "$1"; fi
  shift
done
'''


class GoToolchainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.module = self.root / 'go-toolchain'
        shutil.copytree(MODULES_DIR / 'go-toolchain', self.module)
        spec = importlib.util.spec_from_file_location('toolchain_test', self.module / 'lib/__init__.py')
        self.library = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.library)

    def archive(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            body = GO_STUB.encode()
            entry = tarfile.TarInfo('go/bin/go')
            entry.size, entry.mode = len(body), 0o755
            archive.addfile(entry, io.BytesIO(body))
        return buffer.getvalue()

    def test_download_is_verified_and_reused_inside_the_module(self):
        archive = self.archive()
        with patch.object(self.library.shutil, 'which', return_value=None), \
             patch.object(self.library.urllib.request, 'urlopen', return_value=io.BytesIO(archive)) as fetch, \
             patch.object(self.library, 'SHA256', hashlib.sha256(archive).hexdigest()):
            tool, env = self.library.prepare()
            again, _ = self.library.prepare()
        self.assertEqual(tool, again)
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(Path(tool).is_relative_to(self.module))
        for name in ('GOCACHE', 'GOPATH'):
            self.assertTrue(Path(env[name]).is_relative_to(self.module))
            self.assertTrue(Path(env[name]).is_dir())
        self.assertEqual(env['GOTOOLCHAIN'], 'local')

    def test_bad_checksum_cannot_install_a_program(self):
        with patch.object(self.library.shutil, 'which', return_value=None), \
             patch.object(self.library.urllib.request, 'urlopen', return_value=io.BytesIO(b'wrong')):
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                self.library.prepare()
        self.assertFalse((self.module / 'bin/go/bin/go').exists())

    def test_a_transient_download_failure_is_retried(self):
        archive = self.archive()
        with patch.object(self.library.shutil, 'which', return_value=None), \
             patch.object(self.library.urllib.request, 'urlopen', side_effect=[
                 urllib.error.URLError('temporary failure'), io.BytesIO(archive),
             ]) as fetch, patch('time.sleep'), \
             patch.object(self.library, 'SHA256', hashlib.sha256(archive).hexdigest()):
            tool, _ = self.library.prepare()
        self.assertTrue(Path(tool).is_file())
        self.assertEqual(fetch.call_count, 2)

    def test_a_compatible_path_tool_uses_local_caches_without_changing_process_env(self):
        tool = self.root / 'go'
        tool.write_text(GO_STUB)
        tool.chmod(0o755)
        before = dict(os.environ)
        with patch.object(self.library.shutil, 'which', return_value=str(tool)), \
             patch.object(self.library.urllib.request, 'urlopen') as fetch:
            found, environment = self.library.prepare()
        self.assertEqual(found, str(tool))
        self.assertEqual(os.environ, before)
        self.assertNotEqual(environment.get('GOCACHE'), before.get('GOCACHE'))
        fetch.assert_not_called()


class CodexBuildEnvironmentTest(unittest.TestCase):
    def test_both_builders_get_go_and_caches_through_the_installed_adapter(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, profile, state = root / 'source', root / 'profile', root / 'state'
            for slug in ('craft-sync', 'shell-tree', 'go-toolchain', 'go-toolchain-codex'):
                shutil.copytree(MODULES_DIR / slug, source / slug,
                                ignore=shutil.ignore_patterns('bin', '__pycache__'))
            for slug in ('craft-sync', 'shell-tree'):
                shutil.rmtree(source / slug / 'src')
                (source / slug / 'src').mkdir()
                (source / slug / 'src/main.go').write_text('package main\n')
            report = installer.install(installer.parse_args([
                '--harness', 'codex', '--no-trust', '--modules', str(source),
                '--core', str(CORE_SOURCE), '--settings-dir', str(profile), '--state-dir', str(state),
            ]))
            self.assertEqual(report.warnings, [])
            stubs = root / 'stubs'
            stubs.mkdir()
            (stubs / 'go').write_text(GO_STUB)
            (stubs / 'go').chmod(0o755)
            journal = root / 'builds'
            config = tomllib.loads((profile / 'config.toml').read_text())
            for group in config['hooks']['SessionStart']:
                done = subprocess.run(shlex.split(group['hooks'][0]['command']),
                    input=json.dumps({'hook_event_name': 'SessionStart', 'session_id': 'manual-test',
                                      'source': 'startup', 'cwd': str(root)}),
                    capture_output=True, text=True,
                    env={**os.environ, 'PATH': str(stubs) + ':/usr/bin:/bin', 'BUILD_LOG': str(journal)})
                self.assertEqual(done.returncode, 0, done.stderr)
            for slug in ('craft-sync', 'shell-tree'):
                self.assertTrue((profile / f'jarvis/modules/{slug}/bin/{slug}').is_file())
            entries = journal.read_text().splitlines()
            self.assertEqual(len(entries), 2)
            for entry in entries:
                cache, gopath, policy = entry.split('|')
                self.assertTrue(Path(cache).is_relative_to(profile))
                self.assertTrue(Path(gopath).is_relative_to(profile))
                self.assertEqual(policy, 'local')
