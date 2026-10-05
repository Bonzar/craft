"""Setup готовит набор worktree, первый SessionStart использует его целиком."""

import json
import importlib.machinery
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from . import REPO_ROOT


class DesktopSetupTrustTest(unittest.TestCase):
    def setUp(self) -> None:
        loader = importlib.machinery.SourceFileLoader(
            'desktop_setup_test', str(REPO_ROOT / 'tools/jarvis-desktop-setup'))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.setup = importlib.util.module_from_spec(spec)
        loader.exec_module(self.setup)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = Path(temporary.name) / 'config.toml'

    def test_missing_source_config_stops_before_trusting(self) -> None:
        with mock.patch.object(self.setup, 'project_config', return_value=self.config), \
                mock.patch.object(self.setup, 'installer_module') as installer:
            with self.assertRaisesRegex(RuntimeError, 'ранний конфиг проекта отсутствует'):
                self.setup.trust_project_hooks()
            installer.return_value.trust_hooks.assert_not_called()

    def run_trust(self, count: int) -> None:
        self.config.touch()
        installer = mock.Mock()
        report = installer.Report.return_value
        report.warnings = []
        report.parts = [('codex', 'доверие', str(i)) for i in range(count)]
        with mock.patch.object(self.setup, 'project_config', return_value=self.config), \
                mock.patch.object(self.setup, 'installer_module', return_value=installer):
            self.setup.trust_project_hooks()

    def test_partial_hook_registration_is_not_success(self) -> None:
        with self.assertRaisesRegex(RuntimeError, '1 из 3'):
            self.run_trust(1)

    def test_all_three_early_hooks_are_required(self) -> None:
        self.run_trust(3)


class DesktopBootstrapTest(unittest.TestCase):
    """Проверяем исходники в чистом worktree, не профиль текущей сессии."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.worktree = Path(temporary.name) / 'worktree'
        self.worktree.mkdir()
        for name in ('core', 'modules', 'tools'):
            shutil.copytree(REPO_ROOT / name, self.worktree / name,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'bin'))
        (self.worktree / '.codex').mkdir()
        shutil.copy2(REPO_ROOT / '.codex' / 'config.toml', self.worktree / '.codex' / 'config.toml')
        shutil.copytree(REPO_ROOT / '.agents', self.worktree / '.agents', symlinks=True)

    def install(self) -> None:
        subprocess.run([
            sys.executable, str(self.worktree / 'tools' / 'jarvis-install'),
            '--harness', 'codex', '--no-trust',
            '--settings-dir', str(self.worktree / '.codex'),
            '--state-dir', str(self.worktree / '.codex/state'),
        ], check=True, capture_output=True)

    def fire_start(self, session: str = 'desktop-bootstrap') -> subprocess.CompletedProcess:
        # Поставщик Craft остановится на отсутствии API base до вызова бинарника;
        # файл нужен только чтобы эта проверка не зависела от build-hook.
        binary = self.worktree / 'craft-sync'
        binary.touch()
        event = json.dumps({
            'hook_event_name': 'SessionStart', 'session_id': session,
            'cwd': str(self.worktree), 'source': 'startup',
        })
        environment = {**os.environ, 'CRAFT_SYNC_BIN': str(binary)}
        environment.pop('CRAFT_API_BASE', None)
        return subprocess.run(
            [sys.executable, str(self.worktree / 'tools' / 'jarvis-project-hook'),
             '--module', 'start-context-codex', '--harness', 'codex',
             '--event', 'session-start'],
            input=event, text=True, capture_output=True,
            env=environment, cwd=self.worktree,
        )

    def test_static_config_is_present_before_setup_and_registers_source_hooks(self) -> None:
        config = (self.worktree / '.codex' / 'config.toml').read_text(encoding='utf-8')
        self.assertEqual(config.count('[[hooks.SessionStart.hooks]]'), 3)
        self.assertIn('tools/jarvis-project-hook --module craft-sync', config)
        self.assertIn('tools/jarvis-project-hook --module shell-tree', config)
        self.assertIn('tools/jarvis-project-hook --module start-context-codex', config)
        self.assertIn('additionalContextLimit = 0', config)
        self.assertNotIn(str(REPO_ROOT), config, 'versioned config не привязан к машине')

    def test_start_hook_reads_installed_rules_and_keeps_its_state_in_worktree(self) -> None:
        self.install()
        result = self.fire_start()
        self.assertEqual(result.returncode, 0, result.stderr)
        for slug in ('behavior', 'code', 'craft-call'):
            self.assertIn(f'<!-- jarvis:{slug} -->', result.stdout)
        self.assertNotIn('{{', result.stdout, 'первый ход не должен видеть исходные плейсхолдеры')
        self.assertIn('стартовый контекст: снимок Craft не собран', result.stdout)
        state = self.worktree / '.codex' / 'state' / 'desktop-bootstrap' / 'start-context.json'
        self.assertTrue(state.is_file(), 'след стартового хука лежит в worktree')

    def test_worktreeinclude_copies_dotenv_and_setup_never_sources_it(self) -> None:
        self.assertEqual((REPO_ROOT / '.worktreeinclude').read_text(encoding='utf-8').strip().splitlines()[-1], '.env')
        source = (REPO_ROOT / 'tools' / 'jarvis-desktop-setup').read_text(encoding='utf-8')
        self.assertNotRegex(source, re.compile(r'^\s*(?:source|\.)\s+', re.M))
        self.assertIn('dotenv_value', source)

    def test_project_skills_resolve_to_rendered_worktree_copies(self) -> None:
        self.install()
        for slug in ('craft-call', 'craft-groceries', 'craft-inbox', 'craft-incident',
                     'craft-project-review', 'probe'):
            link = self.worktree / '.agents' / 'skills' / slug
            self.assertTrue(link.is_symlink(), slug)
            self.assertEqual(link.resolve(), (self.worktree / '.codex/skills' / slug).resolve())
            self.assertNotIn('{{', (link / 'SKILL.md').read_text(), slug)
        reference = self.worktree / '.agents/skills/craft-call/craft-tool.md'
        self.assertTrue(reference.is_file(), 'объявленный соседний файл скилла приехал')
        self.assertNotIn('{{', reference.read_text())

    def test_missing_setup_fails_instead_of_using_a_global_module(self) -> None:
        result = self.fire_start()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('jarvis-desktop-setup', result.stderr)


if __name__ == '__main__':
    unittest.main()
