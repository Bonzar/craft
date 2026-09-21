"""Ранний bootstrap Desktop: до setup доступны хук, rules и project skills."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from . import REPO_ROOT


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

    def fire_start(self, session: str = 'desktop-bootstrap') -> subprocess.CompletedProcess:
        # Поставщик Craft остановится на отсутствии API base до вызова бинарника;
        # файл нужен только чтобы эта проверка не зависела от build-hook.
        binary = self.worktree / 'craft-sync'
        binary.touch()
        event = json.dumps({
            'hook_event_name': 'SessionStart', 'session_id': session,
            'cwd': str(self.worktree), 'source': 'startup',
        })
        return subprocess.run(
            [sys.executable, str(self.worktree / 'tools' / 'jarvis-project-hook'),
             '--module', 'start-context-codex', '--harness', 'codex',
             '--event', 'session-start'],
            input=event, text=True, capture_output=True,
            env={**os.environ, 'CRAFT_SYNC_BIN': str(binary)},
        )

    def test_static_config_is_present_before_setup_and_registers_source_hooks(self) -> None:
        config = (self.worktree / '.codex' / 'config.toml').read_text(encoding='utf-8')
        self.assertEqual(config.count('[[hooks.SessionStart.hooks]]'), 3)
        self.assertIn('tools/jarvis-project-hook --module craft-sync', config)
        self.assertIn('tools/jarvis-project-hook --module shell-tree', config)
        self.assertIn('tools/jarvis-project-hook --module start-context-codex', config)
        self.assertIn('additionalContextLimit = 0', config)
        self.assertNotIn('jarvis/modules', config, 'ранний хук не ждёт setup-копию')

    def test_start_hook_reads_source_rules_and_keeps_its_state_in_worktree(self) -> None:
        result = self.fire_start()
        self.assertEqual(result.returncode, 0, result.stderr)
        for slug in ('behavior', 'code', 'craft-call'):
            self.assertIn(f'## Правила модуля {slug}', result.stdout)
        self.assertIn('стартовый контекст: снимок Craft не собран', result.stdout)
        state = self.worktree / '.codex' / 'state' / 'desktop-bootstrap' / 'start-context.json'
        self.assertTrue(state.is_file(), 'след стартового хука лежит в worktree')

    def test_worktreeinclude_copies_dotenv_and_setup_never_sources_it(self) -> None:
        self.assertEqual((REPO_ROOT / '.worktreeinclude').read_text(encoding='utf-8').strip().splitlines()[-1], '.env')
        source = (REPO_ROOT / 'tools' / 'jarvis-desktop-setup').read_text(encoding='utf-8')
        self.assertNotRegex(source, re.compile(r'^\s*(?:source|\.)\s+', re.M))
        self.assertIn('dotenv_value', source)

    def test_project_skills_are_links_to_their_single_source(self) -> None:
        for slug in ('craft-call', 'craft-groceries', 'craft-inbox', 'craft-incident',
                     'craft-project-review', 'probe'):
            link = REPO_ROOT / '.agents' / 'skills' / slug
            self.assertTrue(link.is_symlink(), slug)
            self.assertTrue((link / 'SKILL.md').is_file(), slug)


if __name__ == '__main__':
    unittest.main()
