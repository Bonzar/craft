"""Модуль env-refresh: догоняет ли набор main и цел ли он, когда не догнал.

Репозиториев два, как в жизни: B — «origin» с веткой main, A — клон сессии.
Набор ставится из A, как это делает setup-скрипт окружения, потом в B приезжает
коммит, и хук запускается ровно той строкой, что стоит в бутстрапе репозитория,
со stdin как от харнеса.

Источник в репозиториях уменьшен до того, что нужно установщику: сам
установщик, ядро, модуль свежести и пара модулей-проб. Настоящий каталог
модулей тут ничего не проверяет, а стоит секунд.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from . import CORE_SOURCE, INSTALLER, MODULES_DIR

SLUG = 'env-refresh'
ENTRY = ('modules', SLUG, 'hooks', 'module.py')
BUILT_FILE = 'собранное'
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

    def run_hook(self, cloud: bool = True, session: str = 'сессия-1', **extra: str):
        """Хук ровно той строкой, что стоит в бутстрапе, и со stdin от харнеса."""
        env = {
            'PATH': os.environ.get('PATH', ''),
            'HOME': str(self.home),
            'CLAUDE_CONFIG_DIR': str(self.settings),
            **extra,
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

    def put_in_main(self, path: str, text: str | None, message: str) -> str:
        """Коммит в main: подменить или снять файл. Так меняется то, что в распаковке."""
        target = self.origin / path
        if text is None:
            target.unlink()
        else:
            target.write_text(text, encoding='utf-8')
        return commit(self.origin, message)

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

    def test_what_a_module_built_itself_survives_the_refresh(self) -> None:
        """`bin` — не часть модуля, и переустановка его не трогает."""
        self.install()
        built = self.settings / 'jarvis' / 'modules' / PROBE / 'bin'
        built.mkdir(parents=True)
        (built / BUILT_FILE).write_text('бинарник\n', encoding='utf-8')
        self.add_to_origin()

        self.assertIn('СТОП', self.context(self.run_hook()))

        self.assertEqual((built / BUILT_FILE).read_text(encoding='utf-8'), 'бинарник\n')

    def test_the_work_goes_to_the_copy_from_the_unpacked_main(self) -> None:
        """Ставит набор копия модуля из распаковки, своим установщиком из того же коммита."""
        self.install()
        self.put_in_main(
            f'modules/{SLUG}/hooks/module.py',
            'import sys\n'
            f'print("ПЕРЕДАНО", " ".join(sys.argv[1:]))\n',
            'работник-заглушка в main',
        )

        text = self.context(self.run_hook())

        self.assertIn('ПЕРЕДАНО', text)
        self.assertIn('--install', text)
        self.assertIn(str(self.checkout), text)

    def test_main_without_the_module_leaves_everything_alone(self) -> None:
        """Передавать работу некому — одна строка в контекст, набор не тронут."""
        self.install()
        before = self.ledger()
        self.put_in_main(f'modules/{SLUG}/hooks/module.py', None, 'модуль снят с main')

        text = self.context(self.run_hook())

        self.assertIn('в main нет модуля свежести', text)
        self.assertEqual(self.ledger(), before)
        self.assertTrue((self.skill_dir() / MAIN_FILE).is_file())

    def test_a_set_installed_from_a_branch_is_left_alone(self) -> None:
        """Набор из ветки — осознанный выбор человека: не откатываем и молчим."""
        git(self.checkout, 'checkout', '-q', '-b', 'работа')
        (self.checkout / 'modules' / PROBE / 'skill' / 'ВЕТКА.md').write_text('#\n', encoding='utf-8')
        branch_head = commit(self.checkout, 'коммит ветки')
        self.install()
        self.assertEqual(self.ledger()['source']['head'], branch_head)
        self.add_to_origin()

        self.assertEqual(self.context(self.run_hook()), '')

        self.assertEqual(self.ledger()['source']['head'], branch_head)
        self.assertFalse((self.skill_dir() / NEW_FILE).exists())
        self.assertTrue((self.skill_dir() / 'ВЕТКА.md').is_file())

    def test_without_a_ledger_nothing_is_installed(self) -> None:
        """Журнала нет — окружение не наше: набор туда не приносим."""
        self.add_to_origin()

        self.assertEqual(self.context(self.run_hook()), '')

        self.assertFalse((self.settings / 'jarvis').exists())
        self.assertFalse(self.skill_dir().exists())

    def test_an_autonomous_run_touches_nothing(self) -> None:
        """Остановку старта в рутине некому исполнить — набор не трогаем."""
        self.install()
        self.add_to_origin()

        self.assertEqual(self.context(self.run_hook(JARVIS_AUTONOMOUS='1')), '')

        self.assertFalse((self.skill_dir() / NEW_FILE).exists())

    def test_the_reason_reaches_the_human_whole(self) -> None:
        """Причина отказа читается целиком, а не обрывается посреди слова."""
        self.install()
        self.add_to_origin()
        self.put_in_main(
            'tools/jarvis-install',
            'import sys\n'
            'print("usage: jarvis-install [-h] [--harness {claude,codex}]", file=sys.stderr)\n'
            'print(" " * 200, file=sys.stderr)\n'
            'print("jarvis-install: error: неизвестный флаг --source-head", file=sys.stderr)\n'
            'sys.exit(2)\n',
            'установщик в main отказывает',
        )

        text = self.context(self.run_hook())

        self.assertIn('неизвестный флаг --source-head', text)
        self.assertNotIn('usage:', text)

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


def load_module():
    """Хук модуля — обычный файл, грузим его по пути, как установщик в тестах."""
    entry = MODULES_DIR / SLUG / 'hooks' / 'module.py'
    spec = importlib.util.spec_from_file_location('env_refresh_hook', entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = load_module()


class AsideTest(unittest.TestCase):
    """Отставленный набор: что бы ни упало, настройки харнеса остаются на месте."""

    def setUp(self) -> None:
        self.settings = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'настройки'
        (self.settings / 'jarvis').mkdir(parents=True)
        (self.settings / 'jarvis' / 'installed.json').write_text('{}', encoding='utf-8')
        self.settings_file = self.settings / 'settings.json'
        self.settings_file.write_text('{"hooks": {"своё": []}}', encoding='utf-8')

    def test_settings_survive_a_take_that_fails_halfway(self) -> None:
        aside = hook.Aside(self.settings, {})
        with mock.patch.object(hook.os, 'replace', side_effect=OSError('диск')):
            with self.assertRaises(OSError):
                aside.take()
            aside.restore()

        self.assertEqual(self.settings_file.read_text(encoding='utf-8'),
                         '{"hooks": {"своё": []}}')

    def test_settings_that_were_not_there_do_not_appear(self) -> None:
        self.settings_file.unlink()
        aside = hook.Aside(self.settings, {})
        aside.take()
        (self.settings / 'settings.json').write_text('{"новое": 1}', encoding='utf-8')
        aside.restore()

        self.assertFalse(self.settings_file.exists())


if __name__ == '__main__':
    unittest.main()
