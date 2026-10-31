"""Замок craft-type-lock: путь от сырого события Claude до ответа и следа."""

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jarvis.storage import Storage
from jarvis.trace import Trace

from . import CORE_SOURCE, MODULES_DIR

SLUG = 'craft-type-lock'
# Скиллы типов строят другие модули; замку нужны только их шапки в каталоге.
TYPE_SKILLS = ('craft-task', 'craft-stage', 'craft-section', 'craft-note')
CRAFT_WRITE = 'mcp__Craft__craft_write'
TASK_ADD = ('blocks add --siblingId X --position after --json '
            '\'{"type":"text","markdown":"- [ ] Купить молоко #задача/5м"}\'')


def prompt(session: str) -> dict:
    return {'hook_event_name': 'UserPromptSubmit', 'session_id': session,
            'cwd': '/work', 'prompt': 'дальше'}


def write(session: str, command: str, tool: str = CRAFT_WRITE) -> dict:
    return {'hook_event_name': 'PreToolUse', 'session_id': session, 'cwd': '/work',
            'tool_name': tool, 'tool_input': {'command': command}}


def skill(session: str, name: str) -> dict:
    return {'hook_event_name': 'PostToolUse', 'session_id': session, 'cwd': '/work',
            'tool_name': 'Skill', 'tool_input': {'skill': name},
            'tool_response': {'success': True}}


def compact(session: str) -> dict:
    return {'hook_event_name': 'SessionStart', 'session_id': session,
            'cwd': '/work', 'source': 'compact'}


EVENT_OF = {'UserPromptSubmit': 'prompt', 'PreToolUse': 'pre-tool',
            'PostToolUse': 'post-tool', 'SessionStart': 'after-compact'}


class CraftTypeLockTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        modules = cls.root / 'modules'
        ignore = shutil.ignore_patterns('__pycache__')
        module_dir = modules / SLUG
        shutil.copytree(MODULES_DIR / SLUG, module_dir, ignore=ignore)
        shutil.copytree(CORE_SOURCE, module_dir / '_core', ignore=ignore)
        for slug in TYPE_SKILLS:
            (modules / slug).mkdir()
            (modules / slug / 'module.toml').write_text(f'slug = "{slug}"\n', encoding='utf-8')
        cls.entry = module_dir / 'hooks' / 'module.py'

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def run_raw(self, raw: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.entry), '--harness', 'claude',
             '--event', EVENT_OF[raw['hook_event_name']]],
            input=json.dumps(raw, ensure_ascii=False), text=True, capture_output=True,
            env={'PATH': '/usr/bin:/bin', 'HOME': str(self.root)},
        )

    def fire(self, raw: dict) -> subprocess.CompletedProcess:
        result = self.run_raw(raw)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def assert_denied(self, result, *needles: str) -> None:
        output = json.loads(result.stdout)['hookSpecificOutput']
        self.assertEqual(output['hookEventName'], 'PreToolUse')
        self.assertEqual(output['permissionDecision'], 'deny')
        for needle in needles:
            self.assertIn(needle, output['permissionDecisionReason'])

    def assert_silent(self, result) -> None:
        self.assertEqual(result.stdout, '')

    def storage(self, session: str) -> Storage:
        return Storage(self.root / '.local' / 'state' / 'jarvis', session)

    def trace(self, session: str) -> list[dict]:
        return Trace(self.storage(session)).read()

    def last_reason(self, session: str) -> str:
        return self.trace(session)[-1]['reason']

    def test_tagged_write_without_the_skill_is_denied_in_the_claude_form(self) -> None:
        self.fire(prompt('s-deny'))
        result = self.fire(write('s-deny', TASK_ADD))
        self.assert_denied(result, '#задача', 'craft-task', 'повтори запись')

    def test_the_skill_of_this_turn_lets_the_write_through(self) -> None:
        self.fire(prompt('s-pass'))
        self.assert_silent(self.fire(skill('s-pass', 'craft-task')))
        self.assert_silent(self.fire(write('s-pass', TASK_ADD)))
        self.assertIn('скиллы вызваны', self.trace('s-pass')[-1]['reason'])

    def test_a_plugin_prefix_is_cut_off(self) -> None:
        self.fire(prompt('s-plugin'))
        self.fire(skill('s-plugin', 'jarvis:craft-task'))
        self.assert_silent(self.fire(write('s-plugin', TASK_ADD)))
        self.assertIn('скиллы вызваны', self.last_reason('s-plugin'))

    def test_a_new_prompt_needs_the_skill_again(self) -> None:
        self.fire(prompt('s-turn'))
        self.fire(skill('s-turn', 'craft-task'))
        self.fire(prompt('s-turn'))
        self.assert_denied(self.fire(write('s-turn', TASK_ADD)), 'craft-task')

    def test_after_compact_the_skill_counts_as_not_called(self) -> None:
        self.fire(prompt('s-compact'))
        self.fire(skill('s-compact', 'craft-task'))
        self.assert_silent(self.fire(compact('s-compact')))
        self.assert_denied(self.fire(write('s-compact', TASK_ADD)), 'craft-task')

    def test_a_write_without_a_type_tag_stays_silent(self) -> None:
        self.fire(prompt('s-plain'))
        command = 'blocks update --id X --json \'{"markdown":"Просто текст #покупки"}\''
        self.assert_silent(self.fire(write('s-plain', command)))
        self.assertEqual(self.trace('s-plain')[-1]['reason'], 'тега типа нет')

    def test_a_note_tag_needs_craft_note_not_another_skill(self) -> None:
        self.fire(prompt('s-note'))
        self.fire(skill('s-note', 'craft-task'))
        command = 'blocks add --id X --markdown "Мысль про отпуск #заметка"'
        self.assert_denied(self.fire(write('s-note', command)), '#заметка', 'craft-note')
        self.fire(skill('s-note', 'craft-note'))
        self.assert_silent(self.fire(write('s-note', command)))

    def test_a_closing_tail_on_update_is_still_the_tag(self) -> None:
        self.fire(prompt('s-done'))
        command = ('blocks update --id X --json '
                   '\'{"markdown":"- [x] Купить молоко #задача/готово","indentationLevel":1}\'')
        self.assert_denied(self.fire(write('s-done', command)), '#задача', 'craft-task')

    def test_two_types_need_both_skills(self) -> None:
        self.fire(prompt('s-two'))
        self.fire(skill('s-two', 'craft-stage'))
        command = 'blocks add --id X --markdown "- [ ] ### Сборка #этап"; ' + TASK_ADD
        result = self.fire(write('s-two', command))
        self.assert_denied(result, 'craft-task')
        reason = json.loads(result.stdout)['hookSpecificOutput']['permissionDecisionReason']
        self.assertNotIn('не вызван скилл: craft-stage', reason)

    def test_a_word_that_only_starts_like_a_tag_is_not_a_tag(self) -> None:
        self.fire(prompt('s-word'))
        command = 'blocks add --id X --markdown "Список #задачами и #темами"'
        self.assert_silent(self.fire(write('s-word', command)))
        self.assertEqual(self.last_reason('s-word'), 'тега типа нет')

    def test_escaped_cyrillic_in_json_is_still_caught(self) -> None:
        self.fire(prompt('s-escaped'))
        escaped = json.dumps({'markdown': '- [ ] Дело #задача'})
        command = f"blocks add --id X --json '{escaped}'"
        self.assertIn('\\u0437', command)
        self.assert_denied(self.fire(write('s-escaped', command)), 'craft-task')

    def test_help_without_a_tag_stays_silent(self) -> None:
        self.fire(prompt('s-help'))
        self.assert_silent(self.fire(write('s-help', 'tasks add --help')))
        self.assertEqual(self.last_reason('s-help'), 'тега типа нет')

    def test_help_in_the_payload_does_not_open_the_lock(self) -> None:
        self.fire(prompt('s-help-payload'))
        command = 'blocks add --id X --markdown "- [ ] Учить --help #задача"'
        self.assert_denied(self.fire(write('s-help-payload', command)), 'craft-task')

    def test_a_tag_inside_link_text_does_not_count(self) -> None:
        self.fire(prompt('s-link'))
        self.fire(skill('s-link', 'craft-task'))
        command = ('blocks add --id X --json \'{"type":"text","markdown":'
                   '"- [ ] Собрать стенд #задача/1ч [Сборка #этап](block://AB-1) '
                   '[Раздел #раздел](craftdocs://open?blockId=CD)"}\'')
        self.assert_silent(self.fire(write('s-link', command)))
        self.assertEqual(self.last_reason('s-link'), 'теги #задача: скиллы вызваны в этом ходе')

    def test_a_link_alone_is_not_a_typed_write(self) -> None:
        self.fire(prompt('s-link-only'))
        command = 'blocks add --id X --markdown "[Сборка #этап](block://AB-1)"'
        self.assert_silent(self.fire(write('s-link-only', command)))
        self.assertEqual(self.last_reason('s-link-only'), 'тега типа нет')

    def test_a_capitalized_tag_is_still_the_tag(self) -> None:
        self.fire(prompt('s-case'))
        command = 'blocks add --id X --markdown "- [ ] Дело #Задача и #ЭТАП"'
        result = self.fire(write('s-case', command))
        self.assert_denied(result, '#задача → craft-task', '#этап → craft-stage')

    def test_an_empty_command_stays_silent(self) -> None:
        self.fire(prompt('s-empty'))
        self.assert_silent(self.fire(write('s-empty', '   ')))
        self.assertEqual(self.last_reason('s-empty'), 'в вызове нет команды')

    def test_another_server_name_is_locked_too(self) -> None:
        self.fire(prompt('s-server'))
        result = self.fire(write('s-server', TASK_ADD, tool='mcp__claude_ai_Craft__craft_write'))
        self.assert_denied(result, 'craft-task')

    def test_a_broken_state_file_fails_loud_with_the_way_out(self) -> None:
        for session, body in (('s-broken-json', '{"turn": 1,'), ('s-broken-list', '[1, 2]')):
            path = self.storage(session).path('craft-type-lock.json')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding='utf-8')
            result = self.run_raw(prompt(session))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(str(path), result.stderr)
            self.assertIn('Удали его', result.stderr)

    def test_two_skills_called_at_once_both_count(self) -> None:
        for attempt in range(5):
            session = f's-parallel-{attempt}'
            self.fire(prompt(session))
            runs = [subprocess.Popen(
                [sys.executable, str(self.entry), '--harness', 'claude', '--event', 'post-tool'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env={'PATH': '/usr/bin:/bin', 'HOME': str(self.root)},
            ) for _ in TYPE_SKILLS[:2]]
            for run, name in zip(runs, TYPE_SKILLS[:2]):
                run.stdin.write(json.dumps(skill(session, name)))
                run.stdin.close()
            for run in runs:
                self.assertEqual(run.wait(), 0, run.stderr.read())
                run.stdout.close()
                run.stderr.close()
            skills = self.storage(session).read_json('craft-type-lock.json')['skills']
            self.assertEqual(set(skills), set(TYPE_SKILLS[:2]))

    def test_another_tool_stays_silent(self) -> None:
        self.fire(prompt('s-other'))
        self.assert_silent(self.fire(write('s-other', TASK_ADD, tool='Bash')))
        self.assert_silent(self.fire(write('s-other', TASK_ADD, tool='mcp__Craft__craft_read')))

    def test_write_and_show_is_locked_too(self) -> None:
        self.fire(prompt('s-show'))
        result = self.fire(write('s-show', TASK_ADD, tool='mcp__Craft__craft_write_and_show'))
        self.assert_denied(result, 'craft-task')

    def test_a_foreign_skill_does_not_count(self) -> None:
        self.fire(prompt('s-foreign'))
        self.fire(skill('s-foreign', 'craft-call'))
        self.assert_denied(self.fire(write('s-foreign', TASK_ADD)), 'craft-task')

    def test_autonomous_run_is_locked_the_same(self) -> None:
        raw = prompt('s-auto')
        subprocess.run(
            [sys.executable, str(self.entry), '--harness', 'claude', '--event', 'prompt'],
            input=json.dumps(raw), text=True, capture_output=True, check=True,
            env={'PATH': '/usr/bin:/bin', 'HOME': str(self.root), 'JARVIS_AUTONOMOUS': '1'},
        )
        result = subprocess.run(
            [sys.executable, str(self.entry), '--harness', 'claude', '--event', 'pre-tool'],
            input=json.dumps(write('s-auto', TASK_ADD)), text=True, capture_output=True,
            env={'PATH': '/usr/bin:/bin', 'HOME': str(self.root), 'JARVIS_AUTONOMOUS': '1'},
        )
        self.assert_denied(result, 'craft-task')
        self.assertTrue(self.trace('s-auto')[-1]['autonomous'])

    def test_the_trace_has_a_line_for_every_event(self) -> None:
        session = 's-trace'
        raws = [prompt(session), skill(session, 'craft-task'), write(session, TASK_ADD),
                compact(session), write(session, TASK_ADD)]
        for raw in raws:
            self.fire(raw)
        lines = self.trace(session)
        self.assertEqual([line['event'] for line in lines],
                         ['prompt', 'post-tool', 'pre-tool', 'after-compact', 'pre-tool'])
        self.assertEqual([line['response'] for line in lines],
                         ['silence', 'silence', 'silence', 'silence', 'deny'])
        self.assertTrue(all(line['module'] == SLUG and line['reason'] for line in lines))


def load_lock_module():
    """Модуль замка как библиотека: ядро уже на пути у пакета тестов."""
    spec = importlib.util.spec_from_file_location(
        'craft_type_lock_hook', MODULES_DIR / SLUG / 'hooks' / 'module.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TypesFileTest(unittest.TestCase):
    """Испорченный справочник — ошибка с именем файла, а не тихий пропуск."""

    def test_the_real_types_file_maps_every_tag_to_a_skill(self) -> None:
        tag_skills = load_lock_module().load_tag_skills()
        self.assertEqual(set(tag_skills.values()), set(TYPE_SKILLS))

    def test_a_broken_types_file_is_an_error(self) -> None:
        lock = load_lock_module()
        bodies = ('{"types":', '[]', '{"types": {}}', '{"types": {"#задача": {}}}',
                  '{"types": {"задача": {"skill": "craft-task"}}}')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'types.json'
            for body in bodies:
                path.write_text(body, encoding='utf-8')
                with self.subTest(body=body), self.assertRaises(ValueError) as caught:
                    lock.load_tag_skills(path)
                self.assertIn(str(path), str(caught.exception))


class MissingSkillsTest(unittest.TestCase):
    """Без модулей-скиллов замок не запрещает, а говорит, чего нет."""

    def test_without_type_skills_the_lock_steps_aside(self) -> None:
        ignore = shutil.ignore_patterns('__pycache__')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module_dir = root / 'modules' / SLUG
            shutil.copytree(MODULES_DIR / SLUG, module_dir, ignore=ignore)
            shutil.copytree(CORE_SOURCE, module_dir / '_core', ignore=ignore)
            result = subprocess.run(
                [sys.executable, str(module_dir / 'hooks' / 'module.py'),
                 '--harness', 'claude', '--event', 'pre-tool'],
                input=json.dumps(write('s-missing', TASK_ADD)), text=True,
                capture_output=True, env={'PATH': '/usr/bin:/bin', 'HOME': str(root)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)['hookSpecificOutput']
            self.assertNotIn('permissionDecision', output)
            self.assertIn('craft-task', output['additionalContext'])


if __name__ == '__main__':
    unittest.main()
