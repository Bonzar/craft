"""Вход обёртки Claude: путь от события харнеса до следа, без самого харнеса."""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jarvis.storage import Storage
from jarvis.trace import Trace

from . import INSTALLER, REPO_ROOT

HOOK_ENTRY = 'jarvis_claude_hook.py'


class HookEntryTest(unittest.TestCase):
    """Проба ставится установщиком и проходит путь целиком."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.settings_root = cls.root / 'settings'
        cls.state_dir = cls.root / 'state'
        subprocess.run(
            [
                sys.executable, str(INSTALLER),
                '--settings-dir', str(cls.settings_root),
                '--modules', str(REPO_ROOT / 'modules'),
                '--lib', str(REPO_ROOT / 'lib'),
                '--state-dir', str(cls.state_dir),
                '--personal-config', str(cls.root / 'personal.json'),
            ],
            check=True,
            capture_output=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def fire(self, session_id: str, raw: dict, env: dict | None = None) -> subprocess.CompletedProcess:
        hook = self.settings_root / 'jarvis' / 'lib' / HOOK_ENTRY
        return subprocess.run(
            [
                sys.executable, str(hook),
                '--module', 'probe',
                '--event', 'prompt',
                '--install-root', str(self.settings_root),
            ],
            input=json.dumps({'session_id': session_id, **raw}),
            text=True,
            capture_output=True,
            env={'PATH': '/usr/bin:/bin', **(env or {})},
        )

    def trace(self, session_id: str) -> list[dict]:
        return Trace(Storage(self.state_dir, session_id)).read()

    def prompt_event(self, text: str = 'привет') -> dict:
        return {'hook_event_name': 'UserPromptSubmit', 'cwd': '/work', 'prompt': text}

    def test_probe_answers_with_silence_and_prints_nothing(self) -> None:
        result = self.fire('sess-silence', self.prompt_event())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')

    def test_trace_lands_in_the_storage(self) -> None:
        self.fire('sess-trace', self.prompt_event())
        lines = self.trace('sess-trace')
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['module'], 'probe')
        self.assertEqual(lines[0]['event'], 'prompt')
        self.assertEqual(lines[0]['response'], 'silence')

    def test_autonomy_flag_reaches_the_trace(self) -> None:
        self.fire('sess-auto', self.prompt_event(), env={'JARVIS_AUTONOMOUS': '1'})
        self.assertTrue(self.trace('sess-auto')[0]['autonomous'])
        self.fire('sess-human', self.prompt_event())
        self.assertFalse(self.trace('sess-human')[0]['autonomous'])

    def test_mode_source_reaches_the_trace(self) -> None:
        storage = Storage(self.state_dir, 'sess-off')
        storage.write_json('modes.json', {'probe': False})
        self.fire('sess-off', self.prompt_event())
        line = self.trace('sess-off')[0]
        self.assertFalse(line['mode_enabled'])
        self.assertEqual(line['mode_source'], 'session')

    def test_line_registered_on_another_event_is_a_defect_not_a_silent_pass(self) -> None:
        hook = self.settings_root / 'jarvis' / 'lib' / HOOK_ENTRY
        result = subprocess.run(
            [
                sys.executable, str(hook),
                '--module', 'probe',
                '--event', 'pre-tool',
                '--install-root', str(self.settings_root),
            ],
            input=json.dumps({'session_id': 'sess-mismatch', **self.prompt_event()}),
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('разошлись с установкой', result.stderr)


class HookEntryInProcessTest(unittest.TestCase):
    """Тот же вход, вызванный напрямую: проверяем печать ответа в форме Claude."""

    def test_answer_is_printed_in_the_claude_form(self) -> None:
        sys.path.insert(0, str(REPO_ROOT / 'lib'))
        import jarvis_claude_hook as entry

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings_root, state_dir = root / 'settings', root / 'state'
            modules = root / 'modules' / 'talker'
            (modules / 'hooks').mkdir(parents=True)
            (modules / 'module.toml').write_text('slug = "talker"\nevents = ["stop"]\n', encoding='utf-8')
            (modules / 'hooks' / 'module.py').write_text(
                'import jarvis\n\n\nclass Module(jarvis.Module):\n'
                '    def handle(self, event, runtime):\n'
                '        return jarvis.Block("доделай")\n',
                encoding='utf-8',
            )
            subprocess.run(
                [
                    sys.executable, str(INSTALLER),
                    '--settings-dir', str(settings_root),
                    '--modules', str(root / 'modules'),
                    '--lib', str(REPO_ROOT / 'lib'),
                    '--state-dir', str(state_dir),
                ],
                check=True,
                capture_output=True,
            )
            out, err = io.StringIO(), io.StringIO()
            code = entry.main(
                ['--module', 'talker', '--event', 'stop', '--install-root', str(settings_root)],
                stdin=io.StringIO(json.dumps({'hook_event_name': 'Stop', 'session_id': 'sess-1', 'cwd': '/w'})),
                stdout=out,
                stderr=err,
            )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue()), {'decision': 'block', 'reason': 'доделай'})


if __name__ == '__main__':
    unittest.main()
