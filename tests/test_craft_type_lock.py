"""Замок craft-type-lock: путь от сырого события Claude до ответа и следа."""

import http.server
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path

from jarvis.storage import Storage
from jarvis.trace import Trace

from . import CORE_SOURCE, MODULES_DIR

SLUG = 'craft-type-lock'
# Скиллы типов и скилл решения строят другие модули; замку нужны только их
# шапки в каталоге.
LOCK_SKILLS = ('craft-task', 'craft-stage', 'craft-section', 'craft-note', 'craft-decision')
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


class LockHarness(unittest.TestCase):
    api_base = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        modules = cls.root / 'modules'
        ignore = shutil.ignore_patterns('__pycache__')
        module_dir = modules / SLUG
        shutil.copytree(MODULES_DIR / SLUG, module_dir, ignore=ignore)
        shutil.copytree(CORE_SOURCE, module_dir / '_core', ignore=ignore)
        for slug in LOCK_SKILLS:
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
            env={'PATH': '/usr/bin:/bin', 'HOME': str(self.root),
                 **({'CRAFT_API_BASE': self.api_base} if self.api_base else {})},
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


class CraftTypeLockTest(LockHarness):

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
        self.assertTrue(self.trace('s-plain')[-1]['reason'].startswith('тега типа нет'))

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
        self.assertTrue(self.last_reason('s-word').startswith('тега типа нет'))

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
        self.assertTrue(self.last_reason('s-link').startswith('теги #задача: скиллы вызваны в этом ходе'))

    def test_a_link_alone_is_not_a_typed_write(self) -> None:
        self.fire(prompt('s-link-only'))
        command = 'blocks add --id X --markdown "[Сборка #этап](block://AB-1)"'
        self.assert_silent(self.fire(write('s-link-only', command)))
        self.assertTrue(self.last_reason('s-link-only').startswith('тега типа нет'))

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
            ) for _ in LOCK_SKILLS[:2]]
            for run, name in zip(runs, LOCK_SKILLS[:2]):
                run.stdin.write(json.dumps(skill(session, name)))
                run.stdin.close()
            for run in runs:
                self.assertEqual(run.wait(), 0, run.stderr.read())
                run.stdout.close()
                run.stderr.close()
            skills = self.storage(session).read_json('craft-type-lock.json')['skills']
            self.assertEqual(set(skills), set(LOCK_SKILLS[:2]))

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
        lock = load_lock_module()
        tag_skills = lock.load_tag_skills()
        # Решение тега не несёт: его скилл замок требует по месту, не по справочнику.
        self.assertEqual(set(tag_skills.values()) | {lock.DECISION_SKILL}, set(LOCK_SKILLS))

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



# --- замок на решения: место записи через connect-API -----------------------

PAGE = 'aaaaaaaa-0000-0000-0000-000000000001'
TOGGLE = 'aaaaaaaa-0000-0000-0000-000000000010'
D1 = 'aaaaaaaa-0000-0000-0000-000000000011'
D2 = 'aaaaaaaa-0000-0000-0000-000000000012'
LINE = 'aaaaaaaa-0000-0000-0000-000000000020'
NOTES = 'aaaaaaaa-0000-0000-0000-000000000021'
PROJECT = 'bbbbbbbb-0000-0000-0000-000000000001'
STAGE = 'bbbbbbbb-0000-0000-0000-000000000010'
STAGE_TOGGLE = 'bbbbbbbb-0000-0000-0000-000000000011'
SD1 = 'bbbbbbbb-0000-0000-0000-000000000012'
REQ = 'aaaaaaaa-0000-0000-0000-000000000005'
DEEP_PAGE = 'cccccccc-0000-0000-0000-000000000001'
DEEP_D1 = 'cccccccc-0000-0000-0000-000000000011'
TASK = 'bbbbbbbb-0000-0000-0000-000000000020'


def block(block_id: str, markdown: str, **fields) -> dict:
    return {'id': block_id, 'type': 'text', 'markdown': markdown, **fields}


DEEP_TREE = {'id': DEEP_PAGE, 'type': 'page', 'markdown': 'Вложенная', 'content': [
    block('cccccccc-0000-0000-0000-000000000010', '+ ### Решения', listStyle='toggle'),
    block(DEEP_D1, '  - Глубокое решение', indentationLevel=1, listStyle='bullet'),
]}


TREES = {
    PAGE: {'id': PAGE, 'type': 'page', 'markdown': 'Раздел #раздел', 'content': [
        block('aaaaaaaa-0000-0000-0000-000000000003', '### Требования', textStyle='h3'),
        block(REQ, '- Требование', listStyle='bullet'),
        block(TOGGLE, '+ ### Решения', textStyle='h3', listStyle='toggle'),
        block(D1, '  - Первое решение', indentationLevel=1, listStyle='bullet'),
        block(D2, '  - Второе решение', indentationLevel=1, listStyle='bullet'),
        {'id': LINE, 'type': 'line', 'lineStyle': 'pageBreak', 'markdown': '*******'},
        block(NOTES, '# Заметки', textStyle='h1'),
        block('aaaaaaaa-0000-0000-0000-000000000022', 'Справочное.'),
    ]},
    PROJECT: {'id': PROJECT, 'type': 'page', 'markdown': 'Проект', 'content': [
        block('bbbbbbbb-0000-0000-0000-000000000002', '+ ### Этапы проекта', listStyle='toggle'),
        block(TASK, '- [ ] Задача #задача/1ч', indentationLevel=2, listStyle='task'),
        {'id': STAGE, 'type': 'page', 'markdown': '- [ ] ### Этап #этап', 'indentationLevel': 1,
         'listStyle': 'task', 'content': [
             block(STAGE_TOGGLE, '+ ### Решения', textStyle='h3', listStyle='toggle'),
             block(SD1, '  - Решение этапа', indentationLevel=1, listStyle='bullet'),
             DEEP_TREE,
         ]},
    ]},
}
TREES[DEEP_PAGE] = DEEP_TREE


def prune(tree, depth: int):
    """Дерево не глубже depth, как отдаёт connect-API по maxDepth."""
    if not isinstance(tree, dict):
        return tree
    if depth == 0:
        return {key: value for key, value in tree.items() if key != 'content'}
    children = tree.get('content')
    if not isinstance(children, list):
        return tree
    return {**tree, 'content': [prune(child, depth - 1) for child in children]}


class FakeCraftApi(http.server.BaseHTTPRequestHandler):

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        page = query.get('id', [''])[0].lower()
        depth = int(query.get('maxDepth', ['-1'])[0])
        tree = prune(TREES.get(page), depth)
        body = json.dumps(tree if tree else {'error': 'Block not found'}).encode('utf-8')
        self.send_response(200 if tree else 404)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def read(session: str, command: str) -> dict:
    return {'hook_event_name': 'PostToolUse', 'session_id': session, 'cwd': '/work',
            'tool_name': 'mcp__Craft__craft_read', 'tool_input': {'command': command},
            'tool_response': {'ok': True}}


DECISION_JSON = '\'{"type":"text","indentationLevel":1,"listStyle":"bullet","markdown":"Новое решение"}\''


class DecisionLockTest(LockHarness):

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), FakeCraftApi)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.api_base = f'http://127.0.0.1:{cls.server.server_address[1]}/api/v1'

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        super().tearDownClass()

    def start(self, session: str, *pages: str) -> None:
        self.fire(prompt(session))
        for page in pages:
            self.assert_silent(self.fire(read(session, f'blocks get {page} --depth -1')))

    def test_a_write_next_to_a_decision_needs_craft_decision(self) -> None:
        self.start('d-next', PAGE)
        result = self.fire(write('d-next', f'blocks add --siblingId {D2} --position after --json {DECISION_JSON}'))
        self.assert_denied(result, 'под тоггл «Решения»', 'craft-decision', 'повтори запись')

    def test_craft_decision_this_turn_opens_the_write(self) -> None:
        self.start('d-open', PAGE)
        self.assert_silent(self.fire(skill('d-open', 'craft-decision')))
        self.assert_silent(self.fire(write('d-open', f'blocks add --siblingId {D2} --position after --json {DECISION_JSON}')))
        self.assertIn('скиллы вызваны в этом ходе, цель', self.last_reason('d-open'))

    def test_right_after_the_toggle_is_the_decision_zone(self) -> None:
        self.start('d-toggle', PAGE)
        result = self.fire(write('d-toggle', f'blocks add --siblingId {TOGGLE} --position after --json {DECISION_JSON}'))
        self.assert_denied(result, 'craft-decision')

    def test_before_the_line_after_decisions_is_still_the_zone(self) -> None:
        self.start('d-line', PAGE)
        result = self.fire(write('d-line', f'blocks add --siblingId {LINE} --position before --json {DECISION_JSON}'))
        self.assert_denied(result, 'craft-decision')

    def test_after_the_line_is_outside_the_zone(self) -> None:
        self.start('d-after-line', PAGE)
        self.assert_silent(self.fire(write('d-after-line', f'blocks add --siblingId {LINE} --position after --json {DECISION_JSON}')))
        self.assertIn('не под «Решениями»', self.last_reason('d-after-line'))

    def test_appending_to_the_page_end_is_not_a_decision(self) -> None:
        self.start('d-end', PAGE)
        self.assert_silent(self.fire(write('d-end', f'blocks add --id {PAGE} --position end --json {DECISION_JSON}')))
        self.assertIn('не под «Решениями»', self.last_reason('d-end'))

    def test_editing_a_decision_needs_the_skill(self) -> None:
        self.start('d-edit', PAGE)
        result = self.fire(write('d-edit', f'blocks update --id {D1} --json {DECISION_JSON}'))
        self.assert_denied(result, 'craft-decision')

    def test_a_requirement_bullet_is_not_a_decision(self) -> None:
        self.start('d-req', PAGE)
        self.assert_silent(self.fire(write('d-req', f'blocks update --id {REQ} --json {DECISION_JSON}')))

    def test_stage_decisions_are_found_from_the_project_page(self) -> None:
        self.start('d-stage', PROJECT)
        result = self.fire(write('d-stage', f'blocks add --siblingId {SD1} --position after --json {DECISION_JSON}'))
        self.assert_denied(result, 'craft-decision')

    def test_a_tagged_write_under_decisions_names_both_skills(self) -> None:
        self.start('d-both', PAGE)
        payload = DECISION_JSON.replace('Новое решение', 'Задевает [[x]] #раздел')
        result = self.fire(write('d-both', f'blocks add --siblingId {D1} --position after --json {payload}'))
        self.assert_denied(result, '#раздел → craft-section', 'craft-decision, craft-section')

    def test_without_reads_the_write_is_into_the_unread(self) -> None:
        self.fire(prompt('d-noread'))
        result = self.fire(write('d-noread', f'blocks add --siblingId {D1} --position after --json {DECISION_JSON}'))
        self.assert_denied(result, 'Запись в непрочитанное', 'нет чтений', 'blocks get')

    def test_a_target_outside_the_read_pages_is_into_the_unread(self) -> None:
        self.start('d-elsewhere', PROJECT)
        result = self.fire(write('d-elsewhere', f'blocks add --siblingId {D1} --position after --json {DECISION_JSON}'))
        self.assert_denied(result, 'Запись в непрочитанное', 'не найдена среди страниц')

    def test_a_target_deeper_than_two_levels_counts_as_unread(self) -> None:
        self.start('d-deep', PROJECT)
        result = self.fire(write('d-deep', f'blocks update --id {DEEP_D1} --json {DECISION_JSON}'))
        self.assert_denied(result, 'Запись в непрочитанное')
        self.start('d-deep-read', PROJECT, DEEP_PAGE)
        result = self.fire(write('d-deep-read', f'blocks update --id {DEEP_D1} --json {DECISION_JSON}'))
        self.assert_denied(result, 'craft-decision')

    def test_moving_a_task_under_decisions_is_caught_by_the_target(self) -> None:
        self.start('d-move', PROJECT, PAGE)
        result = self.fire(write('d-move', f'blocks move --id {TASK} --targetSiblingId {D2} --position after'))
        self.assert_denied(result, 'craft-decision')

    def test_before_the_first_block_is_outside_any_zone(self) -> None:
        self.start('d-first', PAGE)
        first = 'aaaaaaaa-0000-0000-0000-000000000003'
        self.assert_silent(self.fire(write('d-first', f'blocks add --siblingId {first} --position before --json {DECISION_JSON}')))
        self.assertIn('не под «Решениями»', self.last_reason('d-first'))

    def test_a_quoted_id_is_still_found(self) -> None:
        self.start('d-quoted', PAGE)
        result = self.fire(write('d-quoted', f'blocks update --id "{D1}" --json {DECISION_JSON}'))
        self.assert_denied(result, 'craft-decision')

    def test_an_unparsed_id_is_not_judged(self) -> None:
        self.start('d-broken', PAGE)
        self.assert_silent(self.fire(write('d-broken', f'blocks update --id ЗАДАЧА --json {DECISION_JSON}')))
        self.assertIn('место записи не проверено: цель в команде не разобрана', self.last_reason('d-broken'))

    def test_a_page_read_by_date_is_remembered_from_the_answer(self) -> None:
        self.fire(prompt('d-date'))
        raw = read('d-date', 'blocks get --date today')
        raw['tool_response'] = f'<page id="{PAGE}">\n  <pageTitle>Сегодня</pageTitle>\n</page>'
        self.assert_silent(self.fire(raw))
        result = self.fire(write('d-date', f'blocks update --id {D1} --json {DECISION_JSON}'))
        self.assert_denied(result, 'craft-decision')

    def test_a_decision_target_on_a_later_read_page_is_still_caught(self) -> None:
        # Страницы идут свежие первыми: PROJECT свежее, задача найдётся там,
        # а цель переноса D2 — только на PAGE, прочитанной раньше.
        self.start('d-later', PAGE, PROJECT)
        result = self.fire(write('d-later', f'blocks move --id {TASK} --targetSiblingId {D2} --position after'))
        self.assert_denied(result, 'craft-decision')

    def test_an_untagged_write_to_an_unread_target_is_denied(self) -> None:
        self.start('d-untagged', PAGE)
        result = self.fire(write('d-untagged', f'blocks update --id {TASK} --json {DECISION_JSON}'))
        self.assert_denied(result, 'Запись в непрочитанное', 'bbbbbbbb')

    def test_a_flag_after_the_payload_is_still_seen(self) -> None:
        self.start('d-tail', PAGE)
        result = self.fire(write('d-tail', f'blocks add --json {DECISION_JSON} --siblingId={D2} --position=after'))
        self.assert_denied(result, 'craft-decision')

    def test_moving_into_the_end_of_a_page_lands_after_its_last_block(self) -> None:
        self.start('d-move-end', PROJECT, PAGE)
        result = self.fire(write('d-move-end', f'blocks move --id {TASK} --targetId {PAGE} --position end'))
        self.assert_silent(result)
        self.assertIn('не под «Решениями»', self.last_reason('d-move-end'))
        self.start('d-move-deep', PROJECT, DEEP_PAGE)
        result = self.fire(write('d-move-deep', f'blocks move --id {TASK} --targetId {DEEP_PAGE} --position end'))
        self.assert_denied(result, 'craft-decision')

    def test_a_valid_target_is_judged_next_to_a_broken_one(self) -> None:
        self.start('d-mixed', PAGE)
        result = self.fire(write('d-mixed', f'blocks move --id {D1} --targetSiblingId ЗАДАЧА'))
        self.assert_denied(result, 'craft-decision')

    def test_a_targeted_command_without_a_target_is_not_judged(self) -> None:
        self.fire(prompt('d-notarget'))
        self.assert_silent(self.fire(write('d-notarget', 'blocks delete')))
        self.assertIn('флага цели нет', self.last_reason('d-notarget'))

    def test_an_unknown_place_is_announced_once_per_session(self) -> None:
        self.start('d-warn', PAGE)
        self.api_base = 'http://127.0.0.1:9/api/v1'
        try:
            first = self.fire(write('d-warn', f'blocks update --id {D1} --json {DECISION_JSON}'))
            self.fire(prompt('d-warn'))
            second = self.fire(write('d-warn', f'blocks update --id {D2} --json {DECISION_JSON}'))
        finally:
            self.api_base = type(self).api_base
        self.assertIn('место записи в Craft не проверяется', first.stderr)
        self.assertEqual(second.stderr, '')

    def test_a_write_before_the_first_child_of_a_container_is_outside(self) -> None:
        self.start('d-container-first', PROJECT, DEEP_PAGE)
        self.assert_silent(self.fire(write('d-container-first', f'blocks add --id {DEEP_PAGE} --position before --json {DECISION_JSON}')))

    def test_a_corrupted_reads_list_fails_loud(self) -> None:
        self.fire(prompt('d-corrupt'))
        path = self.storage('d-corrupt').path('craft-type-lock.json')
        path.write_text('{"turn": 1, "skills": {}, "reads": [1]}', encoding='utf-8')
        result = self.run_raw(write('d-corrupt', 'tasks add --help'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('испорчен', result.stderr)

    def test_reads_survive_compaction(self) -> None:
        self.start('d-compact', PAGE)
        self.fire(compact('d-compact'))
        self.fire(prompt('d-compact'))
        result = self.fire(write('d-compact', f'blocks update --id {D1} --json {DECISION_JSON}'))
        self.assert_denied(result, 'craft-decision')

    def test_an_unreachable_api_keeps_the_lock_silent_with_a_reason(self) -> None:
        self.start('d-api', PAGE)
        self.api_base = 'http://127.0.0.1:9/api/v1'
        try:
            self.assert_silent(self.fire(write('d-api', f'blocks update --id {D1} --json {DECISION_JSON}')))
        finally:
            self.api_base = type(self).api_base
        self.assertIn('API Craft не ответил', self.last_reason('d-api'))

    def test_a_write_without_a_target_id_skips_the_api(self) -> None:
        self.fire(prompt('d-noid'))
        self.assert_silent(self.fire(write('d-noid', 'tasks add --help')))
        self.assertEqual(self.last_reason('d-noid'), 'тега типа нет')


if __name__ == '__main__':
    unittest.main()
