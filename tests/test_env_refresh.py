"""Модуль env-refresh: догоняет ли набор main и цел ли он, когда не догнал.

Репозиториев два, как в жизни: B — «origin» с веткой main, A — клон сессии.
Набор ставится из A, как это делает setup-скрипт окружения, потом в B приезжает
коммит, и хук запускается ровно той строкой, что стоит в бутстрапе репозитория,
со stdin как от харнеса.

Источник в репозиториях уменьшен до того, что нужно установщику: сам
установщик, ядро, модуль свежести и пара модулей-проб. Настоящий каталог
модулей тут ничего не проверяет, а стоит секунд.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from . import CORE_SOURCE, INSTALLER, MODULES_DIR

SLUG = 'env-refresh'
ENTRY = ('modules', SLUG, 'hooks', 'module.py')
PROBE = 'probe-skill'
PROBE_HEADER = f'slug = "{PROBE}"\n'
MAIN_FILE = 'SKILL.md'
NEW_FILE = 'НОВОЕ.md'


def session_event(session_id: str, source: str = 'startup') -> str:
    return json.dumps({
        'hook_event_name': 'SessionStart',
        'session_id': session_id,
        'cwd': '/work',
        'source': source,
    })


def git(where: Path, *args: str) -> str:
    done = subprocess.run(
        ['git', '-C', str(where), *args],
        capture_output=True, text=True, check=True,
    )
    return done.stdout


def commit(where: Path, message: str) -> str:
    git(where, 'add', '-A')
    git(where, '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-q', '-m', message)
    return git(where, 'rev-parse', 'HEAD').strip()


class EnvRefreshTest(unittest.TestCase):
    """Каждому тесту свои репозитории, свой дом и свой каталог настроек."""

    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.home = self.root / 'дом'
        self.home.mkdir()
        self.settings = self.home / '.claude'
        self.origin = self.root / 'origin'
        self.checkout = self.root / 'сессия'
        self._make_origin()
        git(self.root, 'clone', '-q', str(self.origin), str(self.checkout))

    # --- источник и установка ---

    def _make_origin(self) -> None:
        self.origin.mkdir()
        git(self.origin, 'init', '-q', '-b', 'main')
        source = self.origin / 'modules'
        (source / SLUG / 'hooks').mkdir(parents=True)
        shutil.copy2(MODULES_DIR / SLUG / 'module.toml', source / SLUG / 'module.toml')
        shutil.copy2(MODULES_DIR / SLUG / 'hooks' / 'module.py',
                     source / SLUG / 'hooks' / 'module.py')
        probe = source / PROBE / 'skill'
        probe.mkdir(parents=True)
        (source / PROBE / 'module.toml').write_text(PROBE_HEADER, encoding='utf-8')
        (probe / MAIN_FILE).write_text('# проба\n', encoding='utf-8')
        shutil.copytree(CORE_SOURCE, self.origin / 'core')
        (self.origin / 'tools').mkdir()
        shutil.copy2(INSTALLER, self.origin / 'tools' / 'jarvis-install')
        commit(self.origin, 'первый набор')

    def install(self, from_repo: Path | None = None):
        """Поставить набор так, как это делает setup-скрипт окружения."""
        repo = from_repo or self.checkout
        return subprocess.run(
            [sys.executable, str(repo / 'tools' / 'jarvis-install'),
             '--harness', 'claude',
             '--settings-dir', str(self.settings),
             '--modules', str(repo / 'modules'),
             '--core', str(repo / 'core'),
             '--state-dir', str(self.home / 'state'),
             '--python', sys.executable],
            capture_output=True, text=True, check=True,
        )

    def run_hook(self, cloud: bool = True, session: str = 'сессия-1'):
        """Хук ровно той строкой, что стоит в бутстрапе, и со stdin от харнеса."""
        env = {
            'PATH': os.environ.get('PATH', ''),
            'HOME': str(self.home),
            'CLAUDE_CONFIG_DIR': str(self.settings),
        }
        if cloud:
            env['CLAUDE_CODE_REMOTE'] = 'true'
        return subprocess.run(
            [sys.executable, str(self.checkout.joinpath(*ENTRY)),
             '--harness', 'claude', '--event', 'session-start'],
            input=session_event(session), env=env, capture_output=True, text=True,
        )

    # --- что видно снаружи ---

    def ledger(self) -> dict:
        return json.loads(
            (self.settings / 'jarvis' / 'installed.json').read_text(encoding='utf-8')
        )

    def context(self, done) -> str:
        self.assertEqual(done.returncode, 0, done.stderr)
        if not done.stdout.strip():
            return ''
        return json.loads(done.stdout)['hookSpecificOutput']['additionalContext']

    def skill_dir(self) -> Path:
        return self.settings / 'skills' / PROBE

    def add_to_origin(self, name: str = NEW_FILE) -> str:
        """Коммит в origin: новый файл в части skill модуля-пробы."""
        (self.origin / 'modules' / PROBE / 'skill' / name).write_text('#\n', encoding='utf-8')
        return commit(self.origin, 'правка модуля')

    # --- сами проверки ---

    def test_ledger_names_the_commit_the_set_came_from(self) -> None:
        self.install()
        source = self.ledger()['source']
        self.assertEqual(source['head'], git(self.checkout, 'rev-parse', 'HEAD').strip())
        self.assertEqual(source['path'], str(self.checkout / 'modules'))
        self.assertEqual(source['harness'], 'claude')

    def test_outside_the_cloud_the_hook_says_nothing(self) -> None:
        self.install()
        self.add_to_origin()
        done = self.run_hook(cloud=False)
        self.assertEqual(self.context(done), '')
        self.assertFalse((self.skill_dir() / NEW_FILE).exists())

    def test_the_hook_says_nothing_when_the_set_is_already_on_main(self) -> None:
        self.install()
        done = self.run_hook()
        self.assertEqual(self.context(done), '')

    def test_a_newer_main_is_installed_and_the_start_is_stopped(self) -> None:
        self.install()
        head = self.add_to_origin()
        done = self.run_hook()
        text = self.context(done)
        self.assertIn('СТОП', text)
        self.assertIn(head[:12], text)
        self.assertIn(PROBE, text)
        self.assertTrue((self.skill_dir() / NEW_FILE).is_file())
        self.assertEqual(self.ledger()['source']['head'], head)

    def test_the_second_run_says_nothing(self) -> None:
        self.install()
        self.add_to_origin()
        self.run_hook()
        self.assertEqual(self.context(self.run_hook(session='сессия-2')), '')

    def test_the_session_branch_never_gets_into_the_set(self) -> None:
        self.install()
        head = self.add_to_origin()
        git(self.checkout, 'checkout', '-q', '-b', 'работа')
        (self.checkout / 'modules' / PROBE / 'skill' / 'ВЕТКА.md').write_text('#\n', encoding='utf-8')
        branch_head = commit(self.checkout, 'правка ветки')

        done = self.run_hook()

        self.assertIn('СТОП', self.context(done))
        self.assertTrue((self.skill_dir() / NEW_FILE).is_file())
        self.assertFalse((self.skill_dir() / 'ВЕТКА.md').exists())
        self.assertEqual(self.ledger()['source']['head'], head)
        self.assertEqual(git(self.checkout, 'rev-parse', 'HEAD').strip(), branch_head)

    def test_the_working_tree_of_the_session_stays_as_it_was(self) -> None:
        self.install()
        self.add_to_origin()
        (self.checkout / 'своё.txt').write_text('не трогать\n', encoding='utf-8')
        before = git(self.checkout, 'status', '--porcelain')
        head_before = git(self.checkout, 'rev-parse', 'HEAD')

        self.run_hook()

        self.assertEqual(git(self.checkout, 'status', '--porcelain'), before)
        self.assertEqual(git(self.checkout, 'rev-parse', 'HEAD'), head_before)

    def test_an_unreachable_origin_leaves_the_set_alone(self) -> None:
        self.install()
        before = self.ledger()
        shutil.rmtree(self.origin)

        text = self.context(self.run_hook())

        self.assertIn('не удалось догнать', text)
        self.assertIn(before['source']['head'][:12], text)
        self.assertEqual(self.ledger(), before)
        self.assertTrue((self.skill_dir() / MAIN_FILE).is_file())

    def test_an_install_that_fails_halfway_leaves_the_previous_set_whole(self) -> None:
        self.install()
        before = self.ledger()
        settings_before = (self.settings / 'settings.json').read_text(encoding='utf-8')
        self.add_to_origin()
        # Установщик из нового main пишет половину набора и падает: так
        # выглядит установка, оборвавшаяся посреди дороги.
        (self.origin / 'tools' / 'jarvis-install').write_text(
            'import sys\n'
            'from pathlib import Path\n'
            "root = Path(sys.argv[sys.argv.index('--settings-dir') + 1])\n"
            "(root / 'jarvis' / 'modules' / 'обломок').mkdir(parents=True)\n"
            "(root / 'skills' / 'обломок').mkdir(parents=True)\n"
            'sys.exit(3)\n',
            encoding='utf-8',
        )
        commit(self.origin, 'установщик падает')

        text = self.context(self.run_hook())

        self.assertIn('не удалось догнать', text)
        self.assertEqual(self.ledger(), before)
        self.assertEqual((self.settings / 'settings.json').read_text(encoding='utf-8'),
                         settings_before)
        self.assertTrue((self.skill_dir() / MAIN_FILE).is_file())
        self.assertFalse((self.skill_dir() / NEW_FILE).exists())
        self.assertFalse((self.settings / 'skills' / 'обломок').exists())
        self.assertFalse((self.settings / 'jarvis' / 'modules' / 'обломок').exists())

    def test_the_installed_copy_stays_out_of_the_way(self) -> None:
        """Копия в каталоге настроек молчит: набор обновляет копия из чекаута."""
        self.install()
        self.add_to_origin()
        installed_entry = self.settings / 'jarvis' / 'modules' / SLUG / 'hooks' / 'module.py'
        env = {
            'PATH': os.environ.get('PATH', ''),
            'HOME': str(self.home),
            'CLAUDE_CONFIG_DIR': str(self.settings),
            'CLAUDE_CODE_REMOTE': 'true',
        }
        done = subprocess.run(
            [sys.executable, str(installed_entry),
             '--harness', 'claude', '--event', 'session-start'],
            input=session_event('сессия-3'), env=env, capture_output=True, text=True,
        )

        self.assertEqual(self.context(done), '')
        self.assertFalse((self.skill_dir() / NEW_FILE).exists())


if __name__ == '__main__':
    unittest.main()
