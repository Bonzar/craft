"""База модуля свежести: установленная копия предупреждает, что набор устарел.

База ничего не ставит и не меняет: сверяет набор с удалённым по коммитам в его
файлы и говорит в контекст, что окружение надо обновить, с версиями. Сверить не
вышло — говорит и это. Работает в любом харнесе и окружении; в облачном Claude
уступает адаптеру, который ставит набор сам.

Репозитории и установка — как у адаптера (`EnvRefreshCase`): origin с main и
клон, из которого набор поставлен. Запускается установленная копия, как её
зовёт строка хука в конфиге харнеса.
"""

import json
import shutil
import os
import subprocess
import sys
from pathlib import Path

from .test_env_refresh import (MAIN_FILE, NEW_FILE, PROBE, SLUG, EnvRefreshCase, commit, git,
                               session_event)

FAKE_ARC = Path(__file__).with_name('fake_arc.py')
EXTRA_SLUG = 'junk-probe'


class BaseCase(EnvRefreshCase):
    def run_base(self, harness: str = 'claude', settings: Path | None = None,
                 path: str | None = None, **extra: str):
        """Установленная копия модуля, как её зовёт строка хука харнеса."""
        settings = settings or self.settings
        entry = settings / 'jarvis' / 'modules' / SLUG / 'hooks' / 'module.py'
        env = {
            'PATH': path or os.environ.get('PATH', ''),
            'HOME': str(self.home),
            'CLAUDE_CONFIG_DIR': str(self.settings),
            **extra,
        }
        return subprocess.run(
            [sys.executable, str(entry), '--harness', harness, '--event', 'session-start'],
            input=session_event('база-1'), env=env, capture_output=True, text=True,
        )

    def set_commit(self, where: Path = None) -> str:
        """Последний коммит в файлы набора на голове origin."""
        return git(where or self.origin, 'log', '-1', '--format=%H', '--',
                   'modules', 'core', 'tools/jarvis-install').strip()


class BaseWarnsTest(BaseCase):
    def test_a_fresh_set_says_nothing(self) -> None:
        self.install()
        self.assertEqual(self.context(self.run_base()), '')

    def test_a_stale_set_is_reported_with_versions_and_left_alone(self) -> None:
        self.install()
        old = self.set_commit()
        before = self.ledger()
        new = self.add_to_origin()

        text = self.context(self.run_base())

        self.assertIn('надо обновить', text)
        self.assertIn(old[:12], text)
        self.assertIn(new[:12], text)
        self.assertIn(PROBE, text)
        self.assertEqual(self.ledger(), before)
        self.assertFalse((self.skill_dir() / NEW_FILE).exists())
        self.assertTrue((self.skill_dir() / MAIN_FILE).is_file())

    def test_a_main_commit_outside_the_set_says_nothing(self) -> None:
        self.install()
        (self.origin / 'README.md').write_text('# про репозиторий\n', encoding='utf-8')
        commit(self.origin, 'мимо набора')
        self.assertEqual(self.context(self.run_base()), '')

    def test_a_set_from_a_branch_says_nothing(self) -> None:
        git(self.checkout, 'checkout', '-q', '-b', 'работа')
        (self.checkout / 'modules' / PROBE / 'skill' / 'ВЕТКА.md').write_text('#\n', encoding='utf-8')
        commit(self.checkout, 'коммит ветки')
        self.install()
        self.add_to_origin()
        self.assertEqual(self.context(self.run_base()), '')

    def test_a_check_that_fails_says_so_with_the_reason(self) -> None:
        self.install()
        shutil.rmtree(self.origin)

        text = self.context(self.run_base())

        self.assertIn('проверить не удалось', text)
        self.assertIn('ls-remote', text)

    def test_a_ledger_without_the_source_commit_says_the_check_failed(self) -> None:
        self.install()
        ledger_path = self.settings / 'jarvis' / 'installed.json'
        ledger = self.ledger()
        ledger['source']['head'] = None
        ledger_path.write_text(json.dumps(ledger), encoding='utf-8')

        self.assertIn('проверить не удалось', self.context(self.run_base()))

    def test_an_autonomous_run_says_nothing(self) -> None:
        self.install()
        self.add_to_origin()
        self.assertEqual(self.context(self.run_base(JARVIS_AUTONOMOUS='1')), '')

    def test_in_a_cloud_without_the_adapter_the_base_warns(self) -> None:
        """Облачная сессия чужого репозитория: адаптера нет — предупреждает база."""
        self.install()
        self.add_to_origin()
        text = self.context(self.run_base(CLAUDE_CODE_REMOTE='true',
                                          CLAUDE_PROJECT_DIR=str(self.root / 'чужой')))
        self.assertIn('надо обновить', text)

    def test_the_checkout_copy_outside_the_cloud_leaves_warning_to_the_base(self) -> None:
        """Локально предупреждает установленная копия, копия из чекаута молчит."""
        self.install()
        self.add_to_origin()
        self.assertEqual(self.context(self.run_hook(cloud=False)), '')


class CodexBaseTest(BaseCase):
    """Codex ставит модуль свежести наравне с Claude и предупреждает так же."""

    def test_codex_gets_the_warning(self) -> None:
        codex_home = self.home / '.codex'
        self.install(None, '--no-trust', harness='codex', settings=codex_home)
        self.add_to_origin()

        done = self.run_base(harness='codex', settings=codex_home, CODEX_HOME=str(codex_home))

        # На старте сессии Codex уносит в контекст весь stdout как есть.
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('надо обновить', done.stdout)


class ExtraGitSourceTest(BaseCase):
    """Дополнительный источник сверяется по коммитам в свою папку целиком."""

    def setUp(self) -> None:
        super().setUp()
        self.extra_origin = self.root / 'junk-origin'
        self.extra_origin.mkdir()
        git(self.extra_origin, 'init', '-q', '-b', 'main')
        module = self.extra_origin / 'modules' / EXTRA_SLUG / 'skill'
        module.mkdir(parents=True)
        (module.parent / 'module.toml').write_text(f'slug = "{EXTRA_SLUG}"\n', encoding='utf-8')
        (module / 'SKILL.md').write_text('# junk\n', encoding='utf-8')
        (self.extra_origin / 'modules' / 'other').mkdir()
        (self.extra_origin / 'modules' / 'other' / 'note.md').write_text('#\n', encoding='utf-8')
        self.extra_first = commit(self.extra_origin, 'модуль junk')
        self.extra = self.root / 'junk'
        git(self.root, 'clone', '-q', str(self.extra_origin), str(self.extra))

    def install_with_extra(self):
        return self.install(None, '--extra-modules', str(self.extra / 'modules'))

    def test_the_ledger_records_the_commit_of_the_extra_source(self) -> None:
        self.install_with_extra()
        source = self.ledger()['source']
        self.assertEqual(source['extra_origins'], [
            {'path': str((self.extra / 'modules').resolve()), 'commit': self.extra_first}])
        self.assertNotIn('origin', self.ledger()['modules'][EXTRA_SLUG])

    def test_a_newer_extra_module_is_reported(self) -> None:
        self.install_with_extra()
        (self.extra_origin / 'modules' / EXTRA_SLUG / 'skill' / 'НОВОЕ.md').write_text(
            '#\n', encoding='utf-8')
        new = commit(self.extra_origin, 'правка junk')

        text = self.context(self.run_base())

        self.assertIn('надо обновить', text)
        self.assertIn(f'изменились модули: {EXTRA_SLUG}', text)
        self.assertIn(new[:12], text)
        self.assertIn(self.extra_first[:12], text)

    def test_a_commit_outside_the_extra_source_says_nothing(self) -> None:
        self.install_with_extra()
        (self.extra_origin / 'README.md').write_text('# junk\n', encoding='utf-8')
        commit(self.extra_origin, 'мимо источника')
        self.assertEqual(self.context(self.run_base()), '')

    def test_an_old_ledger_without_source_commits_says_the_check_failed(self) -> None:
        self.install_with_extra()
        ledger_path = self.settings / 'jarvis' / 'installed.json'
        ledger = self.ledger()
        del ledger['source']['extra_origins']
        ledger_path.write_text(json.dumps(ledger), encoding='utf-8')

        text = self.context(self.run_base())

        self.assertIn('проверить не удалось', text)
        self.assertIn('поставь набор заново', text)


class ExtraArcSourceTest(BaseCase):
    """Источник junk в Аркадии: последний коммит в папку на trunk через arc."""

    def setUp(self) -> None:
        super().setUp()
        self.mount = self.root / 'arcadia'
        self.extra = self.mount / 'junk' / 'ai' / 'modules'
        module = self.extra / EXTRA_SLUG
        module.mkdir(parents=True)
        (module / 'module.toml').write_text(f'slug = "{EXTRA_SLUG}"\n', encoding='utf-8')
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        arc = bin_dir / 'arc'
        arc.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_ARC}" "$@"\n', encoding='utf-8')
        arc.chmod(0o755)
        self.path = f'{bin_dir}{os.pathsep}{os.environ.get("PATH", "")}'
        self.scenario = self.root / 'arc.json'

    def scene(self, **extra) -> None:
        self.scenario.write_text(json.dumps({
            'root': str(self.mount), 'trunk': 'trunk-2',
            'log': {'HEAD': ['ветка-1', '2026-10-27T11:38:48+03:00'],
                    'ветка-1': ['ветка-1', '2026-10-27T11:38:48+03:00']},
            **extra,
        }), encoding='utf-8')

    def install_from_mount(self):
        env = {**os.environ, 'PATH': self.path, 'FAKE_ARC': str(self.scenario)}
        return self.install(None, '--extra-modules', str(self.extra), env=env)

    def run_arc_base(self):
        return self.run_base(path=self.path, FAKE_ARC=str(self.scenario))

    def test_the_ledger_records_the_arc_commit_of_the_source(self) -> None:
        self.scene()
        self.install_from_mount()
        self.assertEqual(self.ledger()['source']['extra_origins'][0]['commit'], 'ветка-1')

    def test_a_source_not_yet_in_trunk_says_nothing(self) -> None:
        self.scene()
        self.install_from_mount()
        self.assertEqual(self.context(self.run_arc_base()), '')

    def test_a_newer_trunk_commit_into_the_folder_is_reported(self) -> None:
        self.scene()
        self.install_from_mount()
        self.scene(log={'ветка-1': ['ветка-1', '2026-10-27T11:38:48+03:00'],
                        'trunk-2': ['trunk-2', '2026-10-28T09:00:00+03:00']},
                   diff={'ветка-1..trunk-2': ['junk-probe/skill/SKILL.md']})

        text = self.context(self.run_arc_base())

        self.assertIn('надо обновить', text)
        self.assertIn('trunk-2', text)
        self.assertIn(f'изменились модули: {EXTRA_SLUG}', text)

    def test_the_same_content_in_trunk_says_nothing(self) -> None:
        """PR влит: в trunk другой коммит, но папка та же — набор свежий."""
        self.scene()
        self.install_from_mount()
        self.scene(log={'ветка-1': ['ветка-1', '2026-10-27T11:38:48+03:00'],
                        'trunk-2': ['trunk-2', '2026-10-28T09:00:00+03:00']})
        self.assertEqual(self.context(self.run_arc_base()), '')

    def test_a_failed_fetch_says_the_check_failed(self) -> None:
        self.scene()
        self.install_from_mount()
        self.scene(fetch_fails=True)

        text = self.context(self.run_arc_base())

        self.assertIn('проверить не удалось', text)
        self.assertIn('сеть недоступна', text)
