"""Предстарт локального Codex: изолированный worktree до процесса CLI."""

import importlib.util
import json
import os
import stat
import subprocess
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

from . import REPO_ROOT


def load_launcher():
    path = REPO_ROOT / 'tools' / 'jarvis-codex'
    loader = SourceFileLoader('jarvis_codex_launcher', str(path))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


class CodexLauncherTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.launcher = load_launcher()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.worktree = Path(temporary.name) / 'worktree'
        self.worktree.mkdir()
        self.calls = []

    def runner(self, command, **kwargs):
        self.calls.append((command, kwargs))

    def test_bootstrap_keeps_the_whole_profile_in_the_worktree(self) -> None:
        auth = json.dumps({'tokens': {'access_token': 'not-a-real-token'}})
        environment = self.launcher.bootstrap(
            self.worktree, REPO_ROOT, {'CODEX_AUTH_JSON': auth}, runner=self.runner
        )

        home = (self.worktree / '.codex').resolve()
        self.assertEqual(environment['CODEX_HOME'], str(home))
        self.assertEqual((home / 'auth.json').read_text(encoding='utf-8'), auth)
        self.assertEqual(stat.S_IMODE(home.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((home / 'auth.json').stat().st_mode), 0o600)
        self.assertEqual(len(self.calls), 1)
        command, kwargs = self.calls[0]
        self.assertEqual(command[0:3], [self.launcher.sys.executable, str(REPO_ROOT / 'tools' / 'jarvis-install'), '--harness'])
        self.assertEqual(command[3], 'codex')
        self.assertIn(str(home), command)
        self.assertIn(str(home / 'state'), command)
        self.assertEqual(kwargs['env']['CODEX_HOME'], str(home))

    def test_worktree_dotenv_is_not_an_auth_source(self) -> None:
        auth = json.dumps({'tokens': {'access_token': 'not-a-real-token'}})
        (self.worktree / '.env').write_text(
            "CODEX_AUTH_JSON='" + auth + "'\n",
            encoding='utf-8',
        )

        with self.assertRaisesRegex(RuntimeError, 'CODEX_AUTH_JSON'):
            self.launcher.bootstrap(self.worktree, REPO_ROOT, {}, runner=self.runner)
        self.assertEqual(self.calls, [])

    def test_bootstrap_refuses_to_fall_back_to_a_global_login(self) -> None:
        with self.assertRaisesRegex(RuntimeError, 'CODEX_AUTH_JSON'):
            self.launcher.bootstrap(self.worktree, REPO_ROOT, {}, runner=self.runner)
        self.assertEqual(self.calls, [])

    def test_clean_worktree_gets_modules_rules_and_start_hook_before_codex(self) -> None:
        bin_dir = self.worktree / 'bin'
        bin_dir.mkdir()
        fake_codex = bin_dir / 'codex'
        fake_codex.write_text(
            '#!/usr/bin/env python3\n'
            'import json, sys\n'
            'for line in sys.stdin:\n'
            '    message = json.loads(line)\n'
            "    if message.get('id') is not None:\n"
            "        result = {'data': []} if message.get('method') == 'hooks/list' else {}\n"
            "        print(json.dumps({'id': message['id'], 'result': result}), flush=True)\n",
            encoding='utf-8',
        )
        fake_codex.chmod(0o755)
        auth = json.dumps({'tokens': {'access_token': 'not-a-real-token'}})
        environment = {
            **os.environ,
            'PATH': str(bin_dir) + os.pathsep + os.environ['PATH'],
            'CODEX_AUTH_JSON': auth,
        }

        self.launcher.bootstrap(self.worktree, REPO_ROOT, environment, runner=subprocess.run)

        home = (self.worktree / '.codex').resolve()
        self.assertTrue((home / 'jarvis' / 'modules' / 'start-context-codex').is_dir())
        self.assertIn('jarvis:behavior', (home / 'AGENTS.md').read_text(encoding='utf-8'))
        config = (home / 'config.toml').read_text(encoding='utf-8')
        self.assertIn('[[hooks.SessionStart]]', config)
        ledger = json.loads((home / 'jarvis' / 'installed.json').read_text(encoding='utf-8'))
        self.assertEqual(ledger['source']['state_dir'], str(home / 'state'))


if __name__ == '__main__':
    unittest.main()
