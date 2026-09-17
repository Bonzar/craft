"""Вход обёртки Claude: путь от события харнеса до следа, без самого харнеса."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jarvis.storage import Storage
from jarvis.trace import Trace

from . import CORE_SOURCE, INSTALLER, MODULES_DIR

PROBE_ENTRY = ('probe', 'hooks', 'module.py')


def prompt_event(session_id: str, text: str = 'привет') -> str:
    return json.dumps(
        {
            'hook_event_name': 'UserPromptSubmit',
            'session_id': session_id,
            'cwd': '/work',
            'prompt': text,
        }
    )


class InstalledProbeTest(unittest.TestCase):
    """Проба ставится установщиком и проходит путь целиком."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.settings_root = cls.root / 'settings'
        cls.home = cls.root / 'home'
        cls.home.mkdir()
        subprocess.run(
            [
                sys.executable, str(INSTALLER),
                '--settings-dir', str(cls.settings_root),
                '--modules', str(MODULES_DIR),
            ],
            check=True,
            capture_output=True,
        )
        cls.entry = cls.settings_root.joinpath('jarvis', 'modules', *PROBE_ENTRY)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def fire(self, session_id: str, event: str = 'prompt', extra_env: dict | None = None):
        return subprocess.run(
            [sys.executable, str(self.entry), '--event', event],
            input=prompt_event(session_id),
            text=True,
            capture_output=True,
            env={'PATH': '/usr/bin:/bin', 'HOME': str(self.home), **(extra_env or {})},
        )

    def trace(self, session_id: str) -> list[dict]:
        state = self.home / '.local' / 'state' / 'jarvis'
        return Trace(Storage(state, session_id)).read()

    def test_probe_answers_with_silence_and_prints_nothing(self) -> None:
        result = self.fire('sess-silence')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')

    def test_trace_lands_in_the_storage(self) -> None:
        self.fire('sess-trace')
        lines = self.trace('sess-trace')
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['module'], 'probe')
        self.assertEqual(lines[0]['event'], 'prompt')
        self.assertEqual(lines[0]['response'], 'silence')

    def test_autonomy_flag_reaches_the_trace(self) -> None:
        self.fire('sess-auto', extra_env={'JARVIS_AUTONOMOUS': '1'})
        self.assertTrue(self.trace('sess-auto')[0]['autonomous'])
        self.fire('sess-human')
        self.assertFalse(self.trace('sess-human')[0]['autonomous'])

    def test_mode_source_reaches_the_trace(self) -> None:
        state = self.home / '.local' / 'state' / 'jarvis'
        Storage(state, 'sess-off').write_json('modes.json', {'probe': False})
        self.fire('sess-off')
        line = self.trace('sess-off')[0]
        self.assertFalse(line['mode_enabled'])
        self.assertEqual(line['mode_source'], 'session')

    def test_line_registered_on_another_event_is_a_defect_not_a_silent_pass(self) -> None:
        result = self.fire('sess-mismatch', event='pre-tool')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('разошлись с установкой', result.stderr)

    def test_the_core_lands_inside_the_module_folder(self) -> None:
        core = self.entry.parents[1] / '_core' / 'jarvis'
        self.assertTrue((core / 'storage.py').is_file())

    def test_the_registered_line_points_at_the_module_own_file(self) -> None:
        settings = json.loads((self.settings_root / 'settings.json').read_text(encoding='utf-8'))
        args = settings['hooks']['UserPromptSubmit'][0]['hooks'][0]['args']
        self.assertEqual(args[0], str(self.entry))
        self.assertEqual(args[1:], ['--event', 'prompt'])


class SharedModuleTest(unittest.TestCase):
    """Модуль самодостаточен: голая папка с ядром внутри работает без установщика.

    Так модули и приезжают к коллегам — набором папок, без нашего установщика.
    """

    def test_a_bare_module_folder_with_the_core_inside_runs_on_its_own(self) -> None:
        ignore = shutil.ignore_patterns('__pycache__')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module_dir = root / 'modules' / 'probe'
            shutil.copytree(MODULES_DIR / 'probe', module_dir, ignore=ignore)
            shutil.copytree(CORE_SOURCE, module_dir / '_core', ignore=ignore)
            result = subprocess.run(
                [sys.executable, str(module_dir / 'hooks' / 'module.py'), '--event', 'prompt'],
                input=prompt_event('sess-plain'),
                text=True,
                capture_output=True,
                env={'PATH': '/usr/bin:/bin', 'HOME': str(root)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            state = root / '.local' / 'state' / 'jarvis'
            self.assertEqual(Trace(Storage(state, 'sess-plain')).read()[0]['module'], 'probe')

    def test_two_modules_may_carry_different_core_copies(self) -> None:
        # Разные версии ядра в одном наборе допустимы: модуль самодостаточен.
        ignore = shutil.ignore_patterns('__pycache__')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for slug in ('probe', 'probe-two'):
                module_dir = root / 'modules' / slug
                shutil.copytree(MODULES_DIR / 'probe', module_dir, ignore=ignore)
                (module_dir / 'module.toml').write_text(
                    f'slug = "{slug}"\nevents = ["prompt"]\n', encoding='utf-8'
                )
                shutil.copytree(CORE_SOURCE, module_dir / '_core', ignore=ignore)
            marker = root / 'modules' / 'probe-two' / '_core' / 'jarvis' / 'marker.py'
            marker.write_text('VALUE = "своя копия ядра"\n', encoding='utf-8')
            for slug in ('probe', 'probe-two'):
                result = subprocess.run(
                    [sys.executable, str(root / 'modules' / slug / 'hooks' / 'module.py'), '--event', 'prompt'],
                    input=prompt_event(f'sess-{slug}'),
                    text=True,
                    capture_output=True,
                    env={'PATH': '/usr/bin:/bin', 'HOME': str(root)},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            state = root / '.local' / 'state' / 'jarvis'
            self.assertEqual(Trace(Storage(state, 'sess-probe')).read()[0]['module'], 'probe')
            self.assertEqual(Trace(Storage(state, 'sess-probe-two')).read()[0]['module'], 'probe-two')
            self.assertTrue(marker.is_file(), 'копии ядра независимы')


class RunHookTest(unittest.TestCase):
    """Обёртка вызывается напрямую: проверяем печать ответа в форме Claude."""

    def test_answer_is_printed_in_the_claude_form(self) -> None:
        import jarvis
        from jarvis.wrappers import claude

        with tempfile.TemporaryDirectory() as tmp:
            module_dir = Path(tmp) / 'modules' / 'talker'
            (module_dir / 'hooks').mkdir(parents=True)
            (module_dir / 'module.toml').write_text('slug = "talker"\nevents = ["stop"]\n', encoding='utf-8')
            entry = module_dir / 'hooks' / 'module.py'
            entry.write_text('', encoding='utf-8')

            class Talker(jarvis.Module):
                def handle(self, event, runtime):
                    return jarvis.Block('доделай')

            out, err = io.StringIO(), io.StringIO()
            # Каталог состояния один и берётся от домашнего: уводим домашний в
            # временный, чтобы тест не писал след в настоящее хранилище.
            with mock.patch.dict(os.environ, {'HOME': tmp}):
                code = claude.run_hook(
                    str(entry),
                    Talker,
                    argv=['--event', 'stop'],
                    stdin=io.StringIO(
                        json.dumps({'hook_event_name': 'Stop', 'session_id': 'sess-1', 'cwd': '/w'})
                    ),
                    stdout=out,
                    stderr=err,
                    env={},
                )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue()), {'decision': 'block', 'reason': 'доделай'})


if __name__ == '__main__':
    unittest.main()
