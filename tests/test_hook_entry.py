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


# Сырые события трёх заложенных заранее хуков — из замера 18.09.2026
# (Claude Code 2.1.276).
LAID_IN_EVENTS = {
    'permission-request': {
        'hook_event_name': 'PermissionRequest',
        'cwd': '/work',
        'permission_mode': 'default',
        'tool_name': 'Bash',
        'tool_input': {'command': "echo 'ЗАМЕР' > marker.txt"},
        'permission_suggestions': [{'type': 'setMode', 'mode': 'acceptEdits',
                                    'destination': 'session'}],
    },
    'subagent-start': {
        'hook_event_name': 'SubagentStart',
        'cwd': '/work',
        'agent_id': 'af53b93ea6e4abebd',
        'agent_type': 'general-purpose',
    },
    'model-message': {
        'hook_event_name': 'MessageDisplay',
        'cwd': '/work',
        'turn_id': '28d6b096-1571-44f9-95b3-b008c8d5400f',
        'message_id': 'e59d45d2-42c6-4a6f-bf88-a33f11302838',
        'index': 0,
        'final': True,
        'delta': 'ФИОЛЕТ',
    },
}


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
                '--state-dir', str(cls.home / '.local' / 'state' / 'jarvis'),
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
            [sys.executable, str(self.entry), '--harness', 'claude', '--event', event],
            input=prompt_event(session_id),
            text=True,
            capture_output=True,
            env={'PATH': '/usr/bin:/bin', 'HOME': str(self.home), **(extra_env or {})},
        )

    def fire_raw(self, session_id: str, unified: str):
        payload = dict(LAID_IN_EVENTS[unified], session_id=session_id)
        return subprocess.run(
            [sys.executable, str(self.entry), '--harness', 'claude', '--event', unified],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env={'PATH': '/usr/bin:/bin', 'HOME': str(self.home)},
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

    def test_the_three_laid_in_events_reach_the_probe_and_its_trace(self) -> None:
        for unified in LAID_IN_EVENTS:
            with self.subTest(unified):
                session = f'sess-{unified}'
                result = self.fire_raw(session, unified)
                self.assertEqual(result.returncode, 0, result.stderr)
                # Проба не меняет поведение харнеса: на запросе разрешения она
                # молчит, а не разрешает и не запрещает.
                self.assertEqual(result.stdout, '')
                line = self.trace(session)[0]
                self.assertEqual(line['event'], unified)
                self.assertEqual(line['response'], 'silence')

    def test_the_probe_writes_the_event_composition_into_the_trace(self) -> None:
        self.fire_raw('sess-composition-permission', 'permission-request')
        self.assertEqual(
            self.trace('sess-composition-permission')[0]['reason'],
            'состав события: tool_name, tool_input, permission_options',
        )
        self.fire_raw('sess-composition-subagent', 'subagent-start')
        self.assertEqual(
            self.trace('sess-composition-subagent')[0]['reason'],
            'состав события: agent_id, agent_type',
        )
        self.fire_raw('sess-composition-message', 'model-message')
        self.assertEqual(
            self.trace('sess-composition-message')[0]['reason'],
            'состав события: message_text',
        )

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
        self.assertEqual(args[1:], ['--harness', 'claude', '--event', 'prompt'])


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
                [sys.executable, str(module_dir / 'hooks' / 'module.py'),
                 '--harness', 'claude', '--event', 'prompt'],
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
                    [sys.executable, str(root / 'modules' / slug / 'hooks' / 'module.py'),
                     '--harness', 'claude', '--event', 'prompt'],
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


class HarnessFromTheLineTest(unittest.TestCase):
    """Обёртку называет строка хука, а не точка входа модуля."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.home = cls.root / 'home'
        cls.home.mkdir()
        cls.settings_root = cls.root / 'settings'
        source = cls.root / 'source'
        (source / 'teller').mkdir(parents=True)
        (source / 'teller' / 'module.toml').write_text(
            'slug = "teller"\nevents = ["session-start"]\n', encoding='utf-8'
        )
        hooks = source / 'teller' / 'hooks'
        hooks.mkdir()
        (hooks / 'module.py').write_text(
            'import sys\n'
            'from pathlib import Path\n'
            "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))\n"
            'import jarvis\n'
            'from jarvis import wrappers\n'
            'class Module(jarvis.Module):\n'
            '    def handle(self, event, runtime):\n'
            "        return jarvis.Context('МАРКЕР')\n"
            "if __name__ == '__main__':\n"
            '    sys.exit(wrappers.run_hook(__file__, Module))\n',
            encoding='utf-8',
        )
        subprocess.run(
            [sys.executable, str(INSTALLER),
             '--settings-dir', str(cls.settings_root),
             '--state-dir', str(cls.home / '.local' / 'state' / 'jarvis'),
             '--modules', str(source),
             '--core', str(CORE_SOURCE)],
            check=True, capture_output=True,
        )
        cls.entry = cls.settings_root / 'jarvis' / 'modules' / 'teller' / 'hooks' / 'module.py'

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def fire(self, session: str, *argv: str):
        payload = json.dumps({
            'hook_event_name': 'SessionStart', 'session_id': session,
            'cwd': '/work', 'source': 'startup',
        })
        return subprocess.run(
            [sys.executable, str(self.entry), *argv, '--event', 'session-start'],
            input=payload, text=True, capture_output=True,
            env={'PATH': '/usr/bin:/bin', 'HOME': str(self.home)},
        )

    def trace(self, session: str) -> list[dict]:
        return Trace(Storage(self.home / '.local' / 'state' / 'jarvis', session)).read()

    def test_the_line_of_claude_answers_in_the_form_of_claude(self) -> None:
        result = self.fire('sess-claude', '--harness', 'claude')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {'hookSpecificOutput': {'hookEventName': 'SessionStart',
                                    'additionalContext': 'МАРКЕР'}},
        )

    def test_the_line_of_codex_answers_in_the_form_of_codex(self) -> None:
        # Тот же модуль, та же точка входа: форма ответа другая только потому,
        # что строка хука назвала другой харнес.
        result = self.fire('sess-codex', '--harness', 'codex')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'МАРКЕР')

    def test_a_line_without_a_harness_is_an_error_and_not_a_silent_claude(self) -> None:
        result = self.fire('sess-none')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertIn('--harness', result.stderr)
        self.assertIn('харнес не назван', result.stderr)

    def test_an_unknown_harness_is_named_in_the_error(self) -> None:
        result = self.fire('sess-strange', '--harness', 'aisuite')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertIn('aisuite', result.stderr)

    def test_the_refusal_reaches_the_trace(self) -> None:
        self.fire('sess-trace-refusal')
        line = self.trace('sess-trace-refusal')[0]
        self.assertEqual(line['module'], 'teller')
        self.assertEqual(line['module_class'], 'Module')
        self.assertEqual(line['event'], 'SessionStart')
        self.assertEqual(line['response'], 'error')
        self.assertFalse(line['delivered'])
        self.assertIn('--harness', line['reason'])
        self.assertIn('обёртка не выбрана', line['not_delivered_reason'])


class SiblingEventTest(unittest.TestCase):
    """Одно имя события харнеса — несколько единых: чужая строка молчит."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.home = cls.root / 'home'
        cls.home.mkdir()
        cls.settings_root = cls.root / 'settings'
        source = cls.root / 'source'
        (source / 'watcher').mkdir(parents=True)
        (source / 'watcher' / 'module.toml').write_text(
            'slug = "watcher"\nevents = ["session-start", "after-compact"]\n', encoding='utf-8'
        )
        hooks = source / 'watcher' / 'hooks'
        hooks.mkdir()
        (hooks / 'module.py').write_text(
            'import sys\n'
            'from pathlib import Path\n'
            "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))\n"
            'import jarvis\n'
            'from jarvis import wrappers\n'
            'class Module(jarvis.Module):\n'
            '    def handle(self, event, runtime):\n'
            "        return jarvis.Context(f'пришло {event.event}')\n"
            "if __name__ == '__main__':\n"
            '    sys.exit(wrappers.run_hook(__file__, Module))\n',
            encoding='utf-8',
        )
        subprocess.run(
            [sys.executable, str(INSTALLER),
             '--settings-dir', str(cls.settings_root),
             '--state-dir', str(cls.home / '.local' / 'state' / 'jarvis'),
             '--modules', str(source),
             '--core', str(CORE_SOURCE)],
            check=True, capture_output=True,
        )
        cls.entry = cls.settings_root / 'jarvis' / 'modules' / 'watcher' / 'hooks' / 'module.py'

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def fire(self, source: str, *events: str):
        payload = json.dumps({
            'hook_event_name': 'SessionStart', 'session_id': 'sess-sibling',
            'cwd': '/work', 'source': source,
        })
        argv = [sys.executable, str(self.entry), '--harness', 'claude']
        for name in events:
            argv += ['--event', name]
        return subprocess.run(argv, input=payload, text=True, capture_output=True,
                              env={'PATH': '/usr/bin:/bin', 'HOME': str(self.home)})

    def test_a_line_of_the_sibling_event_stays_silent(self) -> None:
        result = self.fire('startup', 'after-compact')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')

    def test_a_line_that_names_both_answers_either(self) -> None:
        for source, expected in (('startup', 'session-start'), ('compact', 'after-compact')):
            result = self.fire(source, 'session-start', 'after-compact')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f'пришло {expected}', result.stdout)

    def test_a_line_of_a_different_harness_event_is_a_defect_and_says_so(self) -> None:
        result = self.fire('startup', 'prompt')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('разошлись с установкой', result.stderr)
