"""Desktop использует проектный набор с отдельным активным профилем."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch

from . import REPO_ROOT
from .test_installer import installer


class DesktopBootstrapTest(unittest.TestCase):
    def test_release_check_rejects_bootstrap_present_only_in_dirty_working_tree(self):
        path = REPO_ROOT / 'tools/check-desktop-release'
        loader = SourceFileLoader('desktop_release_test', str(path))
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c',
                            'user.email=test@example.invalid', 'commit', '--allow-empty', '-qm', 'before-bootstrap'], check=True)
            for name in module.REQUIRED:
                file = root / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('uncommitted bootstrap')
            with self.assertRaisesRegex(RuntimeError, 'коммит не содержит'):
                module.validate_ref(root, 'HEAD')
            subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c',
                            'user.email=test@example.invalid', 'commit', '-qm', 'deliver-bootstrap'], check=True)
            self.assertEqual(module.validate_ref(root, 'HEAD'), subprocess.check_output(
                ['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip())

    def test_project_hook_commands_are_identical_between_worktrees(self):
        backend = installer.CodexBackend(project=True)
        first = backend.command('/one/python', Path('/one/.codex'), 'craft-sync', ('session-start',))
        second = backend.command('/two/python', Path('/two/.codex'), 'craft-sync', ('session-start',))
        self.assertEqual(first, second)
        self.assertIn('git rev-parse --show-toplevel', first)
        self.assertNotIn('/one', first)

    def test_desktop_setup_finds_bundled_cli_without_path(self):
        path = REPO_ROOT / 'tools' / 'jarvis-desktop-setup'
        loader = SourceFileLoader('desktop_setup_test', str(path))
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(module)
        with patch.object(module.shutil, 'which', return_value=None), \
                patch.object(module.Path, 'is_file', return_value=True):
            self.assertEqual(module.codex_binary(), module.DESKTOP_BINARIES[0])

    def test_dispatcher_uses_own_worktree_and_session_state(self):
        with tempfile.TemporaryDirectory(prefix='desktop worktree ') as directory:
            parent = Path(directory).resolve()
            main, root = parent / 'main', parent / 'worktree'
            subprocess.run(['git', 'init', '-q', str(main)], check=True)
            subprocess.run(['git', '-C', str(main), '-c', 'user.name=Test', '-c',
                            'user.email=test@example.invalid', 'commit', '--allow-empty', '-qm', 'fixture'], check=True)
            subprocess.run(['git', '-C', str(main), 'worktree', 'add', '-q', '--detach', str(root)], check=True)
            (root / 'tools').mkdir()
            (root / 'tools' / 'jarvis-hook').write_bytes((REPO_ROOT / 'tools' / 'jarvis-hook').read_bytes())
            module = root / '.codex/jarvis/modules/example/hooks'
            module.mkdir(parents=True)
            (module / 'module.py').write_text('import json,sys\nprint(json.dumps(sys.argv[1:]))\n')
            (root / '.codex/jarvis/desktop.json').write_text(json.dumps({'worktree': str(root)}))
            nested = root / 'nested'
            nested.mkdir()
            command = installer.CodexBackend(project=True).command(
                'unused', root / '.codex', 'example', ('session-start', 'after-compact'))
            output = subprocess.check_output(command, cwd=nested, shell=True, text=True)
            self.assertEqual(json.loads(output), [
                '--harness', 'codex', '--state-dir', str(root / '.codex/state'),
                '--event', 'session-start', '--event', 'after-compact',
            ])

    def test_shared_hooks_do_nothing_in_an_ordinary_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            marker = root / '.codex/jarvis/desktop.json'
            marker.parent.mkdir(parents=True)
            marker.write_text(json.dumps({'worktree': str(root)}))
            (root / 'tools').mkdir()
            (root / 'tools/jarvis-hook').write_text('raise RuntimeError("module must not run")')
            command = installer.CodexBackend(project=True).command(
                'unused', root / '.codex', 'example', ('session-start',))
            result = subprocess.run(command, cwd=root, shell=True, text=True, capture_output=True)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))

    def test_unprepared_checkout_does_not_need_a_hook_script(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            command = installer.CodexBackend(project=True).command(
                'unused', root / '.codex', 'example', ('session-start',))
            result = subprocess.run(command, cwd=root, shell=True, text=True, capture_output=True)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))

    def test_rules_provider_delivers_worktree_rules_to_external_desktop_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / '.codex'
            installer.install(installer.parse_args([
                '--harness', 'codex', '--project', '--no-trust', '--settings-dir', str(home),
                '--state-dir', str(home / 'state'),
            ]))
            rules = home / 'jarvis/modules/codex-rules/lib/__init__.py'
            spec = importlib.util.spec_from_file_location('desktop_rules_test', rules)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with patch.dict(os.environ, {'CODEX_HOME': str(root / 'external-profile')}):
                self.assertEqual(module.provide(None, None), (home / 'AGENTS.md').read_text())
            with patch.dict(os.environ, {'CODEX_HOME': str(home)}):
                self.assertEqual(module.provide(None, None), '')

    def test_failed_app_server_cannot_be_reported_as_a_successful_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'broken-codex'
            binary.write_text('#!/bin/sh\nexit 17\n')
            binary.chmod(0o755)
            with self.assertRaisesRegex(RuntimeError, 'код завершения 17'):
                installer.app_server_call(str(binary), root, [{'id': 1, 'method': 'initialize'}])

    def test_missing_hook_trust_stops_required_install(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            reply = {2: {'result': {'data': []}}}
            with patch.object(installer, 'app_server_call', return_value=reply):
                with self.assertRaisesRegex(RuntimeError, 'не видит'):
                    installer.install(installer.parse_args([
                        '--harness', 'codex', '--project', '--require-trust',
                        '--settings-dir', str(home), '--trust-home', str(home / 'active'),
                        '--state-dir', str(home / 'state'),
                    ]))

    def test_only_hook_trust_is_written_to_the_external_profile_and_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'worktree/.codex/config.toml'
            active = root / 'desktop-profile'
            hook = {'key': 'project-hook-key', 'sourcePath': str(config),
                    'command': 'local-worktree-command', 'eventName': 'sessionStart', 'currentHash': 'sha256:test',
                    'enabled': True, 'trustStatus': 'untrusted'}
            calls = []

            def rpc(cli, home, requests):
                calls.append((home, requests))
                if requests[-1]['method'] == 'hooks/list':
                    result_hook = {**hook, 'trustStatus': 'trusted' if len(calls) == 3 else 'untrusted'}
                    return {2: {'result': {'data': [{'hooks': [result_hook]}]}}}
                return {request['id']: {'result': {}} for request in requests if 'id' in request}

            report = installer.Report()
            with patch.object(installer, 'app_server_call', side_effect=rpc):
                installer.trust_hooks('codex', config, active, {('sessionstart', hook['command'])}, report,
                                      cwd=config.parent.parent)
            self.assertEqual(report.errors, [])
            self.assertTrue(all(home == active for home, _ in calls))
            writes = [request for _, requests in calls for request in requests
                      if request['method'] == 'config/value/write']
            self.assertEqual([request['params']['keyPath'] for request in writes], [
                'hooks.state."project-hook-key".trusted_hash',
                'hooks.state."project-hook-key".enabled',
            ])
            for _, requests in (calls[0], calls[2]):
                self.assertEqual(requests[-1]['params'], {'cwds': [str(config.parent.parent)]})


if __name__ == '__main__':
    unittest.main()
