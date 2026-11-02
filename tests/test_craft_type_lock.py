"""Замок craft-type-lock: путь от сырого события Claude до ответа и следа."""

import http.server
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path

from jarvis import registry
from jarvis.storage import Storage
from jarvis.trace import Trace

from . import CORE_SOURCE, MODULES_DIR

SLUG = 'craft-type-lock'
# Модули сущностей строят другие; замку нужны их шапки с `for` и теги в
# data/entity.json. Набор берётся из настоящего каталога: кто объявил себя
# для замка, тот и сущность.
LOCK_SKILLS = tuple(manifest.slug for manifest in registry.adapters(MODULES_DIR / SLUG, SLUG))
ENTITY_MANIFEST = 'slug = "{slug}"\nfor = ["craft-type-lock"]\n'
# Шаблон тега: запись в сущность проходит, когда в этом ходе прочитан любой
# из шаблонов её тега. Ид берутся из настоящих data/entity.json.
ENTITY_DATA = {slug: json.loads((MODULES_DIR / slug / 'data' / 'entity.json').read_text(encoding='utf-8'))
               for slug in LOCK_SKILLS}
TEMPLATE = {slug: next(iter(data['templates'].values()))[0] for slug, data in ENTITY_DATA.items()}
TASK_TEMPLATES = ENTITY_DATA['craft-task']['templates']['#задача']
# Команду Craft замок разбирает библиотекой соседа.
PARSER = 'command-tree-craft'
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
        shutil.copytree(MODULES_DIR / PARSER, modules / PARSER, ignore=ignore)
        # Соседи-сущности: шапка и теги в данных, как у настоящих модулей.
        for slug in LOCK_SKILLS:
            (modules / slug).mkdir()
            (modules / slug / 'module.toml').write_text(ENTITY_MANIFEST.format(slug=slug),
                                                        encoding='utf-8')
            (modules / slug / 'data').mkdir()
            shutil.copy(MODULES_DIR / slug / 'data' / 'entity.json',
                        modules / slug / 'data' / 'entity.json')
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

    def prepare(self, session: str, slug: str, name: str | None = None) -> None:
        """Скилл сущности вызван и её шаблон прочитан в этом ходе."""
        self.assert_silent(self.fire(skill(session, name or slug)))
        self.assert_silent(self.fire(read(session, f'blocks get {TEMPLATE[slug]} --depth -1')))

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
        self.prepare('s-pass', 'craft-task')
        self.assert_silent(self.fire(write('s-pass', TASK_ADD)))
        self.assertIn('скиллы вызваны и шаблоны прочитаны', self.trace('s-pass')[-1]['reason'])

    def test_a_plugin_prefix_is_cut_off(self) -> None:
        self.fire(prompt('s-plugin'))
        self.prepare('s-plugin', 'craft-task', 'jarvis:craft-task')
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
        self.prepare('s-note', 'craft-task')
        command = 'blocks add --id X --markdown "Мысль про отпуск #заметка"'
        self.assert_denied(self.fire(write('s-note', command)), '#заметка', 'craft-note')
        self.prepare('s-note', 'craft-note')
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
        self.prepare('s-link', 'craft-task')
        command = ('blocks add --id X --json \'{"type":"text","markdown":'
                   '"- [ ] Собрать стенд #задача/1ч [Сборка #этап](block://AB-1) '
                   '[Раздел #раздел](craftdocs://open?blockId=CD)"}\'')
        self.assert_silent(self.fire(write('s-link', command)))
        self.assertTrue(self.last_reason('s-link').startswith('теги #задача: скиллы вызваны'))

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
        raws = [prompt(session), skill(session, 'craft-task'),
                read(session, f'blocks get {TEMPLATE["craft-task"]}'), write(session, TASK_ADD),
                compact(session), write(session, TASK_ADD)]
        for raw in raws:
            self.fire(raw)
        lines = self.trace(session)
        self.assertEqual([line['event'] for line in lines],
                         ['prompt', 'post-tool', 'post-tool', 'pre-tool', 'after-compact', 'pre-tool'])
        self.assertEqual([line['response'] for line in lines],
                         ['silence', 'silence', 'silence', 'silence', 'silence', 'deny'])
        self.assertTrue(all(line['module'] == SLUG and line['reason'] for line in lines))




class TemplateLockTest(LockHarness):
    """Запись в сущность — только после чтения её шаблона в этом ходе."""

    def test_the_skill_without_the_template_is_denied_with_the_template_named(self) -> None:
        self.fire(prompt('t-skill-only'))
        self.fire(skill('t-skill-only', 'craft-task'))
        result = self.fire(write('t-skill-only', TASK_ADD))
        self.assert_denied(result, 'не прочитан шаблон #задача', f'blocks get {TASK_TEMPLATES[0]} --depth -1',
                           TASK_TEMPLATES[1], 'повтори запись')
        reason = json.loads(result.stdout)['hookSpecificOutput']['permissionDecisionReason']
        self.assertNotIn('не вызван скилл', reason)

    def test_the_template_without_the_skill_is_denied(self) -> None:
        self.fire(prompt('t-read-only'))
        self.fire(read('t-read-only', f'blocks get {TASK_TEMPLATES[0]} --depth -1'))
        result = self.fire(write('t-read-only', TASK_ADD))
        self.assert_denied(result, 'не вызван скилл: craft-task')
        reason = json.loads(result.stdout)['hookSpecificOutput']['permissionDecisionReason']
        self.assertNotIn('не прочитан шаблон', reason)

    def test_any_template_of_the_tag_in_any_case_will_do(self) -> None:
        for number, template in enumerate((TASK_TEMPLATES[1].upper(), TASK_TEMPLATES[0].lower())):
            session = f't-any-{number}'
            self.fire(prompt(session))
            self.fire(skill(session, 'craft-task'))
            self.fire(read(session, f'blocks get {template} --depth -1'))
            self.assert_silent(self.fire(write(session, TASK_ADD)))

    def test_a_template_of_the_last_turn_does_not_count(self) -> None:
        self.fire(prompt('t-last-turn'))
        self.fire(read('t-last-turn', f'blocks get {TASK_TEMPLATES[0]}'))
        self.fire(prompt('t-last-turn'))
        self.fire(skill('t-last-turn', 'craft-task'))
        self.assert_denied(self.fire(write('t-last-turn', TASK_ADD)), 'не прочитан шаблон #задача')

    def test_compaction_forgets_the_template(self) -> None:
        self.fire(prompt('t-compact'))
        self.prepare('t-compact', 'craft-task')
        self.fire(compact('t-compact'))
        self.fire(skill('t-compact', 'craft-task'))
        self.assert_denied(self.fire(write('t-compact', TASK_ADD)), 'не прочитан шаблон #задача')

    def test_a_template_seen_only_in_an_answer_is_not_read(self) -> None:
        self.fire(prompt('t-answer'))
        self.fire(skill('t-answer', 'craft-task'))
        raw = read('t-answer', 'search "Задача"')
        raw['tool_response'] = f'<page id="{TASK_TEMPLATES[0]}">\n</page>'
        self.fire(raw)
        self.assert_denied(self.fire(write('t-answer', TASK_ADD)), 'не прочитан шаблон #задача')

    def test_each_tag_needs_its_own_template(self) -> None:
        self.fire(prompt('t-two'))
        self.prepare('t-two', 'craft-task')
        self.fire(skill('t-two', 'craft-stage'))
        command = 'blocks add --id X --markdown "- [ ] ### Сборка #этап"; ' + TASK_ADD
        result = self.fire(write('t-two', command))
        self.assert_denied(result, 'не прочитан шаблон #этап')
        reason = json.loads(result.stdout)['hookSpecificOutput']['permissionDecisionReason']
        self.assertNotIn('#задача:', reason)
        self.assertNotIn('#веха', reason)


def load_lock_module():
    """Модуль замка как библиотека: ядро уже на пути у пакета тестов."""
    spec = importlib.util.spec_from_file_location(
        'craft_type_lock_hook', MODULES_DIR / SLUG / 'hooks' / 'module.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def entity_body(tag: str) -> str:
    return json.dumps({'tags': [tag], 'templates': {tag: [TASK_TEMPLATES[0]]}}, ensure_ascii=False)


class EntityFileTest(unittest.TestCase):
    """Модули сущностей находятся по `for`; испорченный data/entity.json — ошибка с путём."""

    def add_module(self, root: Path, slug: str, entity: str | None, serves: bool = True) -> Path:
        folder = root / slug
        (folder / 'data').mkdir(parents=True)
        manifest = ENTITY_MANIFEST.format(slug=slug) if serves else f'slug = "{slug}"\n'
        (folder / 'module.toml').write_text(manifest, encoding='utf-8')
        path = folder / 'data' / 'entity.json'
        if entity is not None:
            path.write_text(entity, encoding='utf-8')
        return path

    def test_the_real_entity_modules_map_every_tag_to_a_module(self) -> None:
        lock = load_lock_module()
        tag_skills = lock.load_tag_skills(MODULES_DIR / SLUG)
        self.assertLessEqual({'craft-task', 'craft-stage', 'craft-section', 'craft-note',
                              'craft-project', 'craft-sphere'},
                             set(tag_skills.values()))
        self.assertEqual(tag_skills['#этап'], 'craft-stage')
        self.assertEqual(tag_skills['#проект'], 'craft-project')
        self.assertEqual(tag_skills['#сфера'], 'craft-sphere')
        entries = lock.load_entities(MODULES_DIR / SLUG)
        self.assertEqual(set(entries), set(tag_skills))
        self.assertTrue(all(entry.templates for entry in entries.values()))
        # Решение не сущность: его модуль замку не адаптер.
        self.assertNotIn('craft-decisions', tag_skills.values())

    def test_a_broken_entity_file_is_an_error(self) -> None:
        lock = load_lock_module()
        template = TASK_TEMPLATES[0]
        bodies = ('{"tags":', '[]', '{"tags": []}', '{"tags": ["задача"]}', '{"tags": [1]}',
                  '{"tags": ["#задача"]}', '{"tags": ["#задача"], "templates": {}}',
                  '{"tags": ["#задача"], "templates": {"#задача": []}}',
                  '{"tags": ["#задача"], "templates": {"#задача": ["шаблон"]}}',
                  json.dumps({'tags': ['#задача'], 'templates': {'#задача': [template], '#веха': [template]}}))
        with tempfile.TemporaryDirectory() as tmp:
            path = self.add_module(Path(tmp), 'craft-x', None)
            for body in bodies:
                path.write_text(body, encoding='utf-8')
                with self.subTest(body=body), self.assertRaises(ValueError) as caught:
                    lock.load_tag_skills(Path(tmp) / SLUG)
                self.assertIn(str(path), str(caught.exception))
            # Один тег у двух модулей — ошибка, а не молчаливый выбор.
            path.write_text(entity_body('#задача'), encoding='utf-8')
            self.add_module(Path(tmp), 'craft-y', entity_body('#Задача'))
            with self.assertRaises(ValueError):
                lock.load_tag_skills(Path(tmp) / SLUG)

    def test_an_adapter_without_an_entity_file_is_an_error(self) -> None:
        lock = load_lock_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = self.add_module(Path(tmp), 'craft-x', None)
            with self.assertRaises(ValueError) as caught:
                lock.load_tag_skills(Path(tmp) / SLUG)
            self.assertIn(str(path), str(caught.exception))

    def test_an_entity_file_without_for_is_not_the_locks(self) -> None:
        lock = load_lock_module()
        with tempfile.TemporaryDirectory() as tmp:
            self.add_module(Path(tmp), 'craft-task', entity_body('#задача'))
            self.add_module(Path(tmp), 'craft-other', entity_body('#задача'), serves=False)
            self.assertEqual(lock.load_tag_skills(Path(tmp) / SLUG), {'#задача': 'craft-task'})


class BrokenEntityFileTest(LockHarness):
    """Справочник не собирается — запись в Craft не проходит, в причине путь к файлу."""

    def break_entity(self, slug: str, body: str) -> Path:
        path = self.root / 'modules' / slug / 'data' / 'entity.json'
        original = path.read_text(encoding='utf-8')
        self.addCleanup(path.write_text, original, encoding='utf-8')
        path.write_text(body, encoding='utf-8')
        return path

    def test_a_broken_entity_file_denies_the_write_with_its_path(self) -> None:
        path = self.break_entity('craft-note', '{"tags":')
        self.fire(prompt('s-broken-entity'))
        # Вызов скилла без справочника — ошибка хука с путём, скилл не засчитан.
        called = self.run_raw(skill('s-broken-entity', 'craft-task'))
        self.assertNotEqual(called.returncode, 0)
        self.assertIn(str(path), called.stderr)
        result = self.fire(write('s-broken-entity', TASK_ADD))
        self.assert_denied(result, str(path), 'entity.json')

    def test_a_repeated_tag_denies_even_a_write_without_tags(self) -> None:
        tag = json.loads((MODULES_DIR / 'craft-note' / 'data' / 'entity.json')
                         .read_text(encoding='utf-8'))['tags'][0]
        path = self.break_entity('craft-task', entity_body(tag))
        self.fire(prompt('s-repeated-tag'))
        result = self.fire(write('s-repeated-tag', 'blocks add --help'))
        self.assert_denied(result, str(path), 'craft-note')


class MissingNeighboursTest(unittest.TestCase):
    """Без разбора команд замок в стороне; без модулей сущностей требовать нечего."""

    def run_alone(self, root: Path) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [sys.executable, str(root / 'modules' / SLUG / 'hooks' / 'module.py'),
             '--harness', 'claude', '--event', 'pre-tool'],
            input=json.dumps(write('s-missing', TASK_ADD)), text=True,
            capture_output=True, env={'PATH': '/usr/bin:/bin', 'HOME': str(root)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def lay_out(self, root: Path, *neighbours: str) -> None:
        ignore = shutil.ignore_patterns('__pycache__')
        module_dir = root / 'modules' / SLUG
        shutil.copytree(MODULES_DIR / SLUG, module_dir, ignore=ignore)
        shutil.copytree(CORE_SOURCE, module_dir / '_core', ignore=ignore)
        for slug in neighbours:
            shutil.copytree(MODULES_DIR / slug, root / 'modules' / slug, ignore=ignore)

    def test_without_the_parser_the_lock_steps_aside(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.lay_out(Path(tmp))
            output = json.loads(self.run_alone(Path(tmp)).stdout)['hookSpecificOutput']
            self.assertNotIn('permissionDecision', output)
            self.assertIn(PARSER, output['additionalContext'])

    def test_without_entity_modules_a_tagged_write_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.lay_out(Path(tmp), PARSER)
            self.assertEqual(self.run_alone(Path(tmp)).stdout, '')


# --- тип страницы цели: место записи через connect-API ----------------------

PAGE = 'aaaaaaaa-0000-0000-0000-000000000001'
TOGGLE = 'aaaaaaaa-0000-0000-0000-000000000010'
D1 = 'aaaaaaaa-0000-0000-0000-000000000011'
D2 = 'aaaaaaaa-0000-0000-0000-000000000012'
LINE = 'aaaaaaaa-0000-0000-0000-000000000020'
NOTES = 'aaaaaaaa-0000-0000-0000-000000000021'
FRESH = 'aaaaaaaa-0000-0000-0000-000000000030'
FRESH_PAGE = 'aaaaaaaa-0000-0000-0000-000000000040'
PROJECT = 'bbbbbbbb-0000-0000-0000-000000000001'
STAGE = 'bbbbbbbb-0000-0000-0000-000000000010'
STAGE_TOGGLE = 'bbbbbbbb-0000-0000-0000-000000000011'
SD1 = 'bbbbbbbb-0000-0000-0000-000000000012'
REQ = 'aaaaaaaa-0000-0000-0000-000000000005'
DEEP_PAGE = 'cccccccc-0000-0000-0000-000000000001'
DEEP_D1 = 'cccccccc-0000-0000-0000-000000000011'
TASK = 'bbbbbbbb-0000-0000-0000-000000000020'
TASK_BODY = 'bbbbbbbb-0000-0000-0000-000000000021'
BARE_TASK = 'bbbbbbbb-0000-0000-0000-000000000025'
STAGE_LINK = 'bbbbbbbb-0000-0000-0000-000000000030'
STAGE2 = 'bbbbbbbb-0000-0000-0000-000000000040'
TASK2 = 'bbbbbbbb-0000-0000-0000-000000000041'
MILESTONE = 'bbbbbbbb-0000-0000-0000-000000000050'
M_STAGE = 'bbbbbbbb-0000-0000-0000-000000000051'
M_TASK = 'bbbbbbbb-0000-0000-0000-000000000052'
SPHERE = 'eeeeeeee-1000-0000-0000-000000000001'
ARCHIVE = 'eeeeeeee-1000-0000-0000-000000000010'
ARCHIVE_LINE = 'eeeeeeee-1000-0000-0000-000000000011'
REPAIR = 'eeeeeeee-1000-0000-0000-000000000020'
REPAIR_STAGE = 'eeeeeeee-1000-0000-0000-000000000022'
NOTE = 'eeeeeeee-1000-0000-0000-000000000030'
MENU = 'eeeeeeee-1000-0000-0000-000000000031'
MENU_LINE = 'eeeeeeee-1000-0000-0000-000000000032'
DAY = 'ffffffff-0000-0000-0000-000000000001'
DAY_LINE = 'ffffffff-0000-0000-0000-000000000002'


def block(block_id: str, markdown: str, **fields) -> dict:
    return {'id': block_id, 'type': 'text', 'markdown': markdown, **fields}


def page(block_id: str, markdown: str, *content, **fields) -> dict:
    return {'id': block_id, 'type': 'page', 'markdown': markdown, **fields, 'content': list(content)}


DEEP_TREE = page(DEEP_PAGE, 'Вложенная',
                 block('cccccccc-0000-0000-0000-000000000010', '+ ### Решения', listStyle='toggle'),
                 block(DEEP_D1, '  - Глубокое решение', indentationLevel=1, listStyle='bullet'))
# Корни документов. Поиск, как у настоящего API, ищет по документу блока.
# Страница проекта устроена, как в Craft: этапы на +1 под переключателем,
# задачи на +1 под своим этапом, этапы вехи на +1 под вехой.
DOCUMENTS = (
    page(PAGE, 'Раздел #раздел',
         block('aaaaaaaa-0000-0000-0000-000000000003', '### Требования', textStyle='h3'),
         block(REQ, '- Требование', listStyle='bullet'),
         block(TOGGLE, '+ ### Решения', textStyle='h3', listStyle='toggle'),
         block(D1, '  - Первое решение', indentationLevel=1, listStyle='bullet'),
         block(D2, '  - Второе решение', indentationLevel=1, listStyle='bullet'),
         block(FRESH, '  - Только что записанное решение', indentationLevel=1, listStyle='bullet'),
         page(FRESH_PAGE, 'Свежая подстраница', block('aaaaaaaa-0000-0000-0000-000000000041', 'Текст')),
         {'id': LINE, 'type': 'line', 'lineStyle': 'pageBreak', 'markdown': '*******'},
         block(NOTES, '# Заметки', textStyle='h1'),
         block('aaaaaaaa-0000-0000-0000-000000000022', 'Справочное.')),
    page(PROJECT, 'Проект #проект',
         block('bbbbbbbb-0000-0000-0000-000000000002', '+ ### Этапы проекта', listStyle='toggle'),
         page(STAGE, '  - [ ] ### Этап #этап',
              block(STAGE_TOGGLE, '+ ### Решения', textStyle='h3', listStyle='toggle'),
              block(SD1, '  - Решение этапа', indentationLevel=1, listStyle='bullet'),
              DEEP_TREE, indentationLevel=1, listStyle='task'),
         page(TASK, '    - [ ] Задача #задача/1ч', block(TASK_BODY, '### Описание', textStyle='h3'),
              indentationLevel=2, listStyle='task'),
         block(BARE_TASK, '    - [ ] Задача без тела #задача', indentationLevel=2, listStyle='task'),
         block(STAGE2, '  - [ ] ### Второй этап #этап', indentationLevel=1, listStyle='task'),
         block(TASK2, '    - [ ] Задача второго этапа #задача', indentationLevel=2, listStyle='task'),
         block(MILESTONE, '  - [ ] ## Веха #веха', indentationLevel=1, listStyle='task'),
         block(M_STAGE, '    - [ ] ### Этап вехи #этап', indentationLevel=2, listStyle='task'),
         block(M_TASK, '      - [ ] Задача этапа вехи #задача', indentationLevel=3, listStyle='task'),
         block(STAGE_LINK, f'[Этап #этап](block://{STAGE})')),
    # Сфера: подстраница «Архив» без тега и заметка с подстраницей без тега.
    page(SPHERE, 'Дом #сфера',
         page(ARCHIVE, 'Архив', block(ARCHIVE_LINE, 'Строка архива')),
         block('eeeeeeee-1000-0000-0000-000000000002', '# Активное', textStyle='h1'),
         page(REPAIR, 'Ремонт #проект',
              block('eeeeeeee-1000-0000-0000-000000000021', '+ ### Этапы проекта', listStyle='toggle'),
              block(REPAIR_STAGE, '  - [ ] ### Этап ремонта #этап', indentationLevel=1,
                    listStyle='task')),
         page(NOTE, 'Банкет #заметка', page(MENU, 'Меню', block(MENU_LINE, 'Салат с крабом')))),
    # Документ без тега: заметка дня.
    page(DAY, 'Сегодня', block(DAY_LINE, 'Купить хлеб')),
)
UPPER_PAGE = 'DDDDDDDD-0000-0000-0000-000000000001'
UPPER_D1 = 'DDDDDDDD-0000-0000-0000-000000000011'
DOCUMENTS += (page(UPPER_PAGE, 'Старый раздел #раздел',
                   block('DDDDDDDD-0000-0000-0000-000000000010', '+ ### Решения', listStyle='toggle'),
                   block(UPPER_D1, '  - Решение', indentationLevel=1, listStyle='bullet')),)
# Проект с «Решениями проекта» под переключателем: решения — его.
TAGGED_PROJECT = 'eeeeeeee-0000-0000-0000-000000000001'
PROJECT_D1 = 'eeeeeeee-0000-0000-0000-000000000011'
DOCUMENTS += (page(TAGGED_PROJECT, 'Свадьба #проект',
                   block('eeeeeeee-0000-0000-0000-000000000010', '+ # Решения проекта', listStyle='toggle'),
                   block(PROJECT_D1, '  - Решение проекта', indentationLevel=1, listStyle='bullet')),)
# Блоки, которых поиск ещё не видит: только что записанное.
NOT_INDEXED = {FRESH, FRESH_PAGE}
LINK_TEXT = re.compile(r'\[([^\]]*)\]\([^)]*\)')
MARKUP_PREFIX = re.compile(r'^\s*(?:[-+]\s+)?(?:\[[ x]\]\s+)?(?:#+\s+)?')


def plain(markdown: str) -> str:
    """Текст блока, как его видит поиск API: без адресов ссылок и разметки строки."""
    return MARKUP_PREFIX.sub('', LINK_TEXT.sub(r'\1', markdown or ''))


def index(tree: dict, document: str, pages: list, found: dict) -> dict:
    """id → (блок, документ, страницы от корня до той, где он лежит)."""
    found[tree['id']] = (tree, document, pages)
    inner = [*pages, tree] if tree.get('type') == 'page' else pages
    for child in tree.get('content', []):
        index(child, document, inner, found)
    return found


BLOCKS = {}
for document in DOCUMENTS:
    index(document, document['id'], [], BLOCKS)


def prune(tree, depth: int):
    """Дерево не глубже depth, как отдаёт connect-API по maxDepth; -1 — целиком."""
    if not isinstance(tree, dict) or depth < 0:
        return tree
    if depth == 0:
        return {key: value for key, value in tree.items() if key != 'content'}
    children = tree.get('content')
    if not isinstance(children, list):
        return tree
    return {**tree, 'content': [prune(child, depth - 1) for child in children]}


def search_answer(block_id: str, pattern: str) -> dict:
    document = BLOCKS[block_id][1]
    items = []
    for found_id, (found, owner, pages) in BLOCKS.items():
        if owner != document or found_id in NOT_INDEXED:
            continue
        if re.search(pattern, plain(found.get('markdown')), re.IGNORECASE):
            items.append({'blockId': found_id, 'markdown': plain(found.get('markdown')),
                          'pageBlockPath': [{'id': item['id'], 'content': plain(item['markdown'])}
                                            for item in pages]})
    return {'items': items}


class FakeCraftApi(http.server.BaseHTTPRequestHandler):
    # Запросы замка по порядку: путь и параметры. Цена проверки места видна по ним.
    requests: list = []

    def answer(self, code: int, body) -> None:
        data = json.dumps(body).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        query = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items()}
        endpoint = parsed.path.rsplit('/api/v1/', 1)[-1]
        type(self).requests.append((endpoint, query))
        # Как настоящий connect-API: регистр id различается.
        key = query.get('blockId' if endpoint == 'blocks/search' else 'id', '')
        if key not in BLOCKS:
            self.answer(404, {'error': 'Block not found'})
        elif endpoint == 'blocks/search':
            self.answer(200, search_answer(key, query['pattern']))
        else:
            self.answer(200, prune(BLOCKS[key][0], int(query.get('maxDepth', '-1'))))

    def log_message(self, *args) -> None:
        pass


def read(session: str, command: str) -> dict:
    return {'hook_event_name': 'PostToolUse', 'session_id': session, 'cwd': '/work',
            'tool_name': 'mcp__Craft__craft_read', 'tool_input': {'command': command},
            'tool_response': {'ok': True}}


BULLET_JSON = '\'{"type":"text","indentationLevel":1,"listStyle":"bullet","markdown":"Новое решение"}\''
SECTION = 'Запись задевает сущность «Раздел #раздел»'
# Новая задача на +1 под этапом и строка без отступа.
TASK_JSON = '\'{"type":"text","indentationLevel":2,"listStyle":"task","markdown":"- [ ] Новая задача #задача"}\''
LEVEL0_JSON = '\'{"type":"text","markdown":"Строка"}\''
FIRST = 'aaaaaaaa-0000-0000-0000-000000000003'
DOWN = 'http://127.0.0.1:9/api/v1'


class ApiLockHarness(LockHarness):

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
        for page_id in pages:
            self.assert_silent(self.fire(read(session, f'blocks get {page_id} --depth -1')))

    def reason(self, result) -> str:
        return json.loads(result.stdout)['hookSpecificOutput']['permissionDecisionReason']

    def with_api_down(self, raw: dict):
        self.api_base = DOWN
        try:
            return self.fire(raw)
        finally:
            self.api_base = type(self).api_base


class PageTypeLockTest(ApiLockHarness):

    def test_a_write_into_the_decisions_of_a_section_needs_craft_section(self) -> None:
        self.start('p-next', PAGE)
        result = self.fire(write('p-next', f'blocks add --siblingId {D2} --position after --json {BULLET_JSON}'))
        self.assert_denied(result, SECTION, '#раздел → craft-section', 'не вызван скилл: craft-section.',
                           'повтори запись')
        self.assertNotIn('craft-decisions', self.reason(result))

    def test_craft_section_this_turn_opens_the_write(self) -> None:
        self.start('p-open', PAGE)
        self.prepare('p-open', 'craft-section')
        self.assert_silent(self.fire(write('p-open', f'blocks add --siblingId {D2} --position after --json {BULLET_JSON}')))
        self.assertIn('шаблоны прочитаны в этом ходе, цель aaaaaaaa у владельца aaaaaaaa (#раздел)',
                      self.last_reason('p-open'))

    def test_a_decision_skill_is_not_an_entity_skill(self) -> None:
        self.start('p-decision', PAGE)
        self.assert_silent(self.fire(skill('p-decision', 'craft-decisions')))
        self.assertIn('не скилл типа', self.last_reason('p-decision'))
        result = self.fire(write('p-decision', f'blocks update --id {D1} --json {BULLET_JSON}'))
        self.assert_denied(result, 'craft-section')

    def test_every_place_of_a_section_needs_craft_section(self) -> None:
        # Разделитель слов не несёт, а свежий блок поиск ещё не видит: их место
        # ищется в дереве прочитанной страницы.
        self.start('p-anywhere', PAGE)
        for command in (f'blocks add --siblingId {TOGGLE} --position after --json {BULLET_JSON}',
                        f'blocks add --siblingId {LINE} --position before --json {BULLET_JSON}',
                        f'blocks add --siblingId {LINE} --position after --json {BULLET_JSON}',
                        f'blocks add --siblingId {FRESH} --position after --json {BULLET_JSON}',
                        f'blocks add --siblingId {FIRST} --position before --json {BULLET_JSON}',
                        f'blocks add --id {PAGE} --position end --json {BULLET_JSON}',
                        f'blocks update --id {REQ} --json {BULLET_JSON}',
                        f'blocks delete --id {NOTES}'):
            with self.subTest(command=command):
                self.assert_denied(self.fire(write('p-anywhere', command)), SECTION, 'craft-section')

    def test_stage_decisions_need_craft_stage_from_the_project_page(self) -> None:
        self.start('p-stage', PROJECT)
        result = self.fire(write('p-stage', f'blocks add --siblingId {SD1} --position after --json {BULLET_JSON}'))
        self.assert_denied(result, 'Запись задевает сущность «- [ ] ### Этап #этап»', '#этап → craft-stage')
        self.assertNotIn('craft-section', self.reason(result))
        self.assertNotIn('craft-project', self.reason(result))

    def test_a_document_without_a_type_tag_needs_no_skill(self) -> None:
        self.start('p-untyped', DAY)
        command = f'blocks add --siblingId {DAY_LINE} --position after --json {BULLET_JSON}'
        self.assert_silent(self.fire(write('p-untyped', command)))
        self.assertIn('тега типа нет, цель ffffffff без владельца с типом', self.last_reason('p-untyped'))

    def test_project_decisions_need_craft_project(self) -> None:
        self.start('p-tagged', TAGGED_PROJECT)
        command = f'blocks add --siblingId {PROJECT_D1} --position after --json {BULLET_JSON}'
        result = self.fire(write('p-tagged', command))
        self.assert_denied(result, 'Запись задевает сущность «Свадьба #проект»',
                           '#проект → craft-project', 'не вызван скилл: craft-project')
        self.prepare('p-tagged', 'craft-project')
        self.assert_silent(self.fire(write('p-tagged', command)))

    def test_a_link_with_a_tag_does_not_type_the_block(self) -> None:
        self.start('p-link', PROJECT)
        result = self.fire(write('p-link', f'blocks update --id {STAGE_LINK} --json {BULLET_JSON}'))
        self.assert_denied(result, 'не вызван скилл: craft-project.')
        self.assertNotIn('craft-stage', self.reason(result))

    def test_a_new_line_at_the_level_of_stages_lands_in_the_project(self) -> None:
        self.start('p-new-stage', PROJECT)
        payload = BULLET_JSON.replace('Новое решение', '- [ ] ### Новый этап #этап')
        command = f'blocks add --siblingId {TASK} --position after --json {payload}'
        result = self.fire(write('p-new-stage', command))
        self.assert_denied(result, '#этап → craft-stage', '«Проект #проект»: #проект → craft-project',
                           'не вызван скилл: craft-project, craft-stage')
        self.prepare('p-new-stage', 'craft-stage')
        self.assert_denied(self.fire(write('p-new-stage', command)), 'не вызван скилл: craft-project.')
        self.prepare('p-new-stage', 'craft-project')
        self.assert_silent(self.fire(write('p-new-stage', command)))

    def test_a_new_task_indented_under_a_stage_lands_in_the_stage(self) -> None:
        self.start('p-new-task', PROJECT)
        payload = TASK_JSON.replace('Новая задача', 'Дело')
        for command in (f'blocks add --siblingId {STAGE} --position after --json {payload}',
                        f'blocks add --siblingId {TASK} --position after --json {payload}',
                        f'blocks add --siblingId {STAGE2} --position before --json {payload}'):
            with self.subTest(command=command):
                result = self.fire(write('p-new-task', command))
                self.assert_denied(result, '«- [ ] ### Этап #этап»', 'не вызван скилл: craft-stage, craft-task')
                self.assertNotIn('craft-project', self.reason(result))

    def test_the_body_of_a_task_needs_craft_task(self) -> None:
        self.start('p-task', PROJECT)
        for command in (f'blocks add --id {TASK} --position end --json {BULLET_JSON}',
                        f'blocks update --id {TASK_BODY} --json {BULLET_JSON}',
                        f'blocks add --id {BARE_TASK} --position end --json {LEVEL0_JSON}'):
            with self.subTest(command=command):
                self.assert_denied(self.fire(write('p-task', command)), 'не вызван скилл: craft-task.')
        # Сосед задачи на её же отступе ложится к её этапу, а не в неё саму.
        result = self.fire(write('p-task', f'blocks add --siblingId {TASK} --position after --json {TASK_JSON}'))
        self.assert_denied(result, 'не вызван скилл: craft-stage, craft-task.')

    def test_closing_a_task_touches_its_stage(self) -> None:
        self.start('p-close', PROJECT)
        for command in (f'tasks update --id {TASK} --state done',
                        f'tasks update --id {BARE_TASK} --state canceled',
                        f'blocks update --id {TASK} --json \'{{"taskInfo":{{"state":"done"}},"indentationLevel":2}}\'',
                        (f'blocks update --id {BARE_TASK} --json '
                         '\'{"markdown":"- [x] Задача без тела #задача/готово","indentationLevel":2}\'')):
            with self.subTest(command=command):
                result = self.fire(write('p-close', command))
                self.assert_denied(result, 'Запись правит сущность', 'не вызван скилл: craft-stage, craft-task')
                self.assertNotIn('craft-project', self.reason(result))

    def test_retitling_or_rescheduling_a_task_touches_the_task_only(self) -> None:
        self.start('p-retitle', PROJECT)
        self.prepare('p-retitle', 'craft-task')
        for command in ((f'blocks update --id {TASK} --json '
                         '\'{"markdown":"- [ ] Новое имя #задача/1ч","indentationLevel":2}\''),
                        f'tasks update --id {TASK} --scheduleDate 2026-11-05',
                        f'blocks update --id {BARE_TASK} --markdown "- [ ] Другое имя #задача"'):
            with self.subTest(command=command):
                self.assert_silent(self.fire(write('p-retitle', command)))
                self.assertIn('без владельца с типом, правит сущность (#задача)', self.last_reason('p-retitle'))

    def test_closing_a_stage_touches_its_project(self) -> None:
        self.start('p-stage-close', PROJECT)
        command = (f'blocks update --id {STAGE2} --json '
                   '\'{"markdown":"- [x] ### Второй этап #этап/готово","indentationLevel":1}\'')
        result = self.fire(write('p-stage-close', command))
        self.assert_denied(result, 'не вызван скилл: craft-project, craft-stage')

    def test_closing_a_stage_inside_a_milestone_needs_the_stage_skill_only(self) -> None:
        # Веха — сущность того же модуля, что этап: её правила несёт тот же скилл.
        self.start('p-milestone', PROJECT)
        self.prepare('p-milestone', 'craft-stage')
        self.assert_silent(self.fire(write('p-milestone', f'tasks update --id {M_STAGE} --state done')))
        result = self.fire(write('p-milestone', f'tasks update --id {M_TASK} --state done'))
        self.assert_denied(result, '«- [ ] ### Этап вехи #этап»', 'не вызван скилл: craft-task.')

    def test_closing_a_project_touches_its_sphere(self) -> None:
        self.start('p-project-close', SPHERE)
        command = f'blocks update --id {REPAIR} --json \'{{"markdown":"Ремонт #проект/готово"}}\''
        result = self.fire(write('p-project-close', command))
        self.assert_denied(result, '«Дом #сфера»', 'не вызван скилл: craft-project, craft-sphere')

    def test_an_untagged_subpage_belongs_to_the_typed_entity_it_lies_in(self) -> None:
        self.start('p-subpages', SPHERE, PROJECT)
        cases = ((f'blocks update --id {ARCHIVE_LINE} --json {LEVEL0_JSON}', 'craft-sphere.'),
                 (f'blocks add --id {ARCHIVE} --position end --json {LEVEL0_JSON}', 'craft-sphere.'),
                 (f'blocks update --id {MENU_LINE} --json {LEVEL0_JSON}', 'craft-note.'),
                 (f'blocks add --siblingId {MENU_LINE} --position after --json {LEVEL0_JSON}', 'craft-note.'),
                 (f'blocks update --id {DEEP_D1} --json {BULLET_JSON}', 'craft-stage.'),
                 (f'blocks add --id {DEEP_PAGE} --position start --json {LEVEL0_JSON}', 'craft-stage.'))
        for command, needed in cases:
            with self.subTest(command=command):
                self.assert_denied(self.fire(write('p-subpages', command)), f'не вызван скилл: {needed}')

    def test_a_subpage_read_by_itself_still_finds_the_owner_above(self) -> None:
        self.start('p-subpage', DEEP_PAGE)
        result = self.fire(write('p-subpage', f'blocks update --id {DEEP_D1} --json {BULLET_JSON}'))
        self.assert_denied(result, 'не вызван скилл: craft-stage.')

    def test_a_sibling_of_a_read_subpage_lands_in_the_page_above(self) -> None:
        self.start('p-above', DEEP_PAGE)
        result = self.fire(write('p-above', f'blocks add --siblingId {DEEP_PAGE} --position after --json {BULLET_JSON}'))
        self.assert_denied(result, '#этап → craft-stage')

    def test_a_tag_in_the_text_and_the_page_need_both_skills(self) -> None:
        self.start('p-both', PAGE)
        payload = BULLET_JSON.replace('Новое решение', '- [ ] Дело #задача')
        result = self.fire(write('p-both', f'blocks add --siblingId {D1} --position after --json {payload}'))
        self.assert_denied(result, '#задача → craft-task', SECTION, 'не вызван скилл: craft-section, craft-task')

    def test_an_unparsed_level_requires_every_candidate_owner(self) -> None:
        self.start('p-guess', PROJECT)
        result = self.fire(write('p-guess', f'blocks add --siblingId {TASK} --position after --json \'{{broken\''))
        self.assert_denied(result, 'не вызван скилл: craft-project, craft-stage, craft-task.')

    def test_without_reads_the_write_is_into_the_unread(self) -> None:
        self.fire(prompt('p-noread'))
        result = self.fire(write('p-noread', f'blocks add --siblingId {D1} --position after --json {BULLET_JSON}'))
        self.assert_denied(result, 'Запись в непрочитанное', 'нет чтений', 'blocks get', 'тип сущности')

    def test_a_target_outside_the_read_pages_is_into_the_unread(self) -> None:
        self.start('p-elsewhere', PROJECT)
        for target in (D1, LINE):
            with self.subTest(target=target):
                result = self.fire(write('p-elsewhere', f'blocks add --siblingId {target} --position after --json {BULLET_JSON}'))
                self.assert_denied(result, 'Запись в непрочитанное', 'не найдена среди страниц')

    def test_moving_a_task_between_stages_touches_both_stages(self) -> None:
        self.start('p-between', PROJECT)
        self.prepare('p-between', 'craft-task')
        command = f'blocks move --id {TASK2} --targetSiblingId {BARE_TASK} --position after'
        result = self.fire(write('p-between', command))
        self.assert_denied(result, '«- [ ] ### Второй этап #этап»', '«- [ ] ### Этап #этап»',
                           'не вызван скилл: craft-stage.')
        self.assertNotIn('craft-project', self.reason(result))

    def test_moving_a_task_into_a_section_needs_all_three_skills(self) -> None:
        self.start('p-move', PROJECT, PAGE)
        result = self.fire(write('p-move', f'blocks move --id {TASK} --targetSiblingId {D2} --position after'))
        self.assert_denied(result, 'не вызван скилл: craft-section, craft-stage, craft-task')

    def test_a_target_on_an_earlier_read_page_is_still_found(self) -> None:
        self.start('p-later', PAGE, PROJECT)
        self.prepare('p-later', 'craft-task')
        self.prepare('p-later', 'craft-stage')
        result = self.fire(write('p-later', f'blocks move --id {TASK} --targetSiblingId {D2} --position after'))
        self.assert_denied(result, 'не вызван скилл: craft-section.')

    def test_moving_into_a_page_is_judged_by_that_page(self) -> None:
        self.start('p-move-end', PROJECT, PAGE, DAY)
        self.prepare('p-move-end', 'craft-task')
        self.prepare('p-move-end', 'craft-stage')
        result = self.fire(write('p-move-end', f'blocks move --id {TASK} --targetId {PAGE} --position end'))
        self.assert_denied(result, 'не вызван скилл: craft-section.')
        result = self.fire(write('p-move-end', f'blocks move --id {TASK} --targetId {DAY} --position end'))
        self.assert_silent(result)

    def test_deleting_a_task_touches_its_stage(self) -> None:
        self.start('p-delete', PROJECT)
        result = self.fire(write('p-delete', f'blocks delete --id {TASK2}'))
        self.assert_denied(result, 'не вызван скилл: craft-stage, craft-task')

    def test_a_quoted_id_is_still_found(self) -> None:
        self.start('p-quoted', PAGE)
        result = self.fire(write('p-quoted', f'blocks update --id "{D1}" --json {BULLET_JSON}'))
        self.assert_denied(result, 'craft-section')

    def test_an_unparsed_id_is_not_judged(self) -> None:
        self.start('p-broken', PAGE)
        self.assert_silent(self.fire(write('p-broken', f'blocks update --id ЗАДАЧА --json {BULLET_JSON}')))
        self.assertIn('место записи не проверено: цель в команде не разобрана', self.last_reason('p-broken'))

    def test_a_valid_target_is_judged_next_to_a_broken_one(self) -> None:
        self.start('p-mixed', PAGE)
        result = self.fire(write('p-mixed', f'blocks move --id {D1} --targetSiblingId ЗАДАЧА'))
        self.assert_denied(result, 'craft-section')

    def test_a_page_read_by_date_is_remembered_from_the_answer(self) -> None:
        self.fire(prompt('p-date'))
        raw = read('p-date', 'blocks get --date today')
        raw['tool_response'] = f'<page id="{PAGE}">\n  <pageTitle>Сегодня</pageTitle>\n</page>'
        self.assert_silent(self.fire(raw))
        result = self.fire(write('p-date', f'blocks update --id {D1} --json {BULLET_JSON}'))
        self.assert_denied(result, 'craft-section')

    def test_an_untagged_write_to_an_unread_target_is_denied(self) -> None:
        self.start('p-untagged', PAGE)
        result = self.fire(write('p-untagged', f'blocks update --id {TASK} --json {BULLET_JSON}'))
        self.assert_denied(result, 'Запись в непрочитанное', 'bbbbbbbb')

    def test_a_flag_after_the_payload_is_still_seen(self) -> None:
        self.start('p-tail', PAGE)
        result = self.fire(write('p-tail', f'blocks add --json {BULLET_JSON} --siblingId={D2} --position=after'))
        self.assert_denied(result, 'craft-section')

    def test_a_targeted_command_without_a_target_is_not_judged(self) -> None:
        self.fire(prompt('p-notarget'))
        self.assert_silent(self.fire(write('p-notarget', 'blocks delete')))
        self.assertIn('флага цели нет', self.last_reason('p-notarget'))

    def test_an_unknown_place_is_announced_once_per_session(self) -> None:
        self.start('p-warn', PAGE)
        first = self.with_api_down(write('p-warn', f'blocks update --id {D1} --json {BULLET_JSON}'))
        self.fire(prompt('p-warn'))
        second = self.with_api_down(write('p-warn', f'blocks update --id {D2} --json {BULLET_JSON}'))
        text = json.loads(first.stdout)['hookSpecificOutput']['additionalContext']
        self.assertIn('место записи в Craft не проверяется', text)
        self.assertIn('Тип страницы цели не проверен', text)
        self.assert_silent(second)

    def test_a_sibling_of_a_read_root_the_search_does_not_see_is_unknown(self) -> None:
        # Поиск свежую подстраницу не видит, а в её собственном дереве страницы
        # над ней нет: место не определено, и это не вина хода.
        self.start('p-rootless', FRESH_PAGE)
        result = self.fire(write('p-rootless', f'blocks add --siblingId {FRESH_PAGE} --position after --json {BULLET_JSON}'))
        text = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
        self.assertIn('страница над целью aaaaaaaa не видна', text)

    def test_a_write_before_the_first_child_of_an_untyped_document_passes(self) -> None:
        self.start('p-container-first', DAY)
        command = f'blocks add --id {DAY} --position start --json {BULLET_JSON}'
        self.assert_silent(self.fire(write('p-container-first', command)))

    def test_a_lower_case_read_of_an_upper_case_page_is_served_by_the_answer(self) -> None:
        self.fire(prompt('p-answer-case'))
        raw = read('p-answer-case', f'blocks get {UPPER_PAGE.lower()} --depth -1')
        raw['tool_response'] = f'<page id="{UPPER_PAGE}">\n</page>'
        self.assert_silent(self.fire(raw))
        result = self.fire(write('p-answer-case', f'blocks update --id {UPPER_D1} --json {BULLET_JSON}'))
        self.assert_denied(result, 'craft-section')

    def test_a_read_in_the_other_case_still_counts(self) -> None:
        self.start('p-retry-case', UPPER_PAGE.lower())
        result = self.fire(write('p-retry-case', f'blocks update --id {UPPER_D1} --json {BULLET_JSON}'))
        self.assert_denied(result, 'craft-section')

    def test_a_404_target_is_retried_in_the_other_case(self) -> None:
        self.start('p-upper', UPPER_PAGE)
        result = self.fire(write('p-upper', f'blocks update --id {UPPER_D1.lower()} --json {BULLET_JSON}'))
        self.assert_denied(result, 'craft-section')

    def test_a_corrupted_reads_list_fails_loud(self) -> None:
        self.fire(prompt('p-corrupt'))
        path = self.storage('p-corrupt').path('craft-type-lock.json')
        path.write_text('{"turn": 1, "skills": {}, "reads": [1]}', encoding='utf-8')
        result = self.run_raw(write('p-corrupt', 'tasks add --help'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('испорчен', result.stderr)

    def test_reads_survive_compaction(self) -> None:
        self.start('p-compact', PAGE)
        self.fire(compact('p-compact'))
        self.fire(prompt('p-compact'))
        result = self.fire(write('p-compact', f'blocks update --id {D1} --json {BULLET_JSON}'))
        self.assert_denied(result, 'craft-section')

    def test_an_unreachable_api_lets_the_write_through_with_a_warning(self) -> None:
        self.start('p-api', PAGE)
        result = self.with_api_down(write('p-api', f'blocks update --id {D1} --json {BULLET_JSON}'))
        output = json.loads(result.stdout)['hookSpecificOutput']
        self.assertNotIn('permissionDecision', output)
        self.assertIn('место цели не определено', output['additionalContext'])

    def test_a_write_without_a_target_id_skips_the_api(self) -> None:
        self.fire(prompt('p-noid'))
        self.assert_silent(self.fire(write('p-noid', 'tasks add --help')))
        self.assertEqual(self.last_reason('p-noid'), 'тега типа нет')


class LookupCostTest(ApiLockHarness):
    """Цена проверки места: запросы к API на одну цель."""

    def requests_of(self, session: str, command: str) -> list[str]:
        FakeCraftApi.requests.clear()
        self.fire(write(session, command))
        return [endpoint for endpoint, _ in FakeCraftApi.requests]

    def test_a_read_typed_container_costs_one_request(self) -> None:
        self.start('c-task', TASK)
        self.assertEqual(
            self.requests_of('c-task', f'blocks add --id {TASK} --position end --json {LEVEL0_JSON}'),
            ['blocks'])

    def test_a_typed_target_edited_in_place_costs_the_block_and_the_search(self) -> None:
        self.start('c-retitle', PROJECT)
        command = f'blocks update --id {TASK} --json \'{{"markdown":"- [ ] Новое имя #задача","indentationLevel":2}}\''
        self.assertEqual(self.requests_of('c-retitle', command), ['blocks', 'blocks/search'])

    def test_an_indented_block_costs_the_block_the_search_and_the_children(self) -> None:
        self.start('c-decision', PAGE)
        self.assertEqual(
            self.requests_of('c-decision', f'blocks add --siblingId {D2} --position after --json {BULLET_JSON}'),
            ['blocks', 'blocks/search', 'blocks'])
        query = dict(FakeCraftApi.requests)['blocks/search']
        self.assertEqual(query['pattern'], r'Второе\W+решение')
        self.assertEqual((query['beforeBlockCount'], query['afterBlockCount']), ('0', '0'))
        self.assertEqual(FakeCraftApi.requests[-1][1]['maxDepth'], '1')

    def test_a_block_without_words_falls_back_to_the_read_page(self) -> None:
        # Дети страницы приходят деревом прочитанной страницы: второго запроса нет.
        self.start('c-line', PAGE)
        self.assertEqual(
            self.requests_of('c-line', f'blocks add --siblingId {LINE} --position after --json {BULLET_JSON}'),
            ['blocks', 'blocks'])
        self.assertEqual(FakeCraftApi.requests[-1][1]['maxDepth'], '-1')


class PayloadTest(unittest.TestCase):
    """Разбор нагрузки: отступ новых блоков и смена состояния."""

    @classmethod
    def setUpClass(cls) -> None:
        load_lock_module()
        cls.payload = sys.modules['payload']

    def write_of(self, command: str):
        return self.payload.write_of(command, lambda text: [text], lambda text: ' '.join(text.split()[:2]))

    def test_levels_come_from_the_json_and_markdown_payload(self) -> None:
        cases = ((f'blocks add --siblingId X --json {TASK_JSON}', frozenset({2})),
                 ('blocks add --siblingId X --json \'{"type":"text","markdown":"Строка"}\'', frozenset({0})),
                 ('blocks add --siblingId X --markdown "Строка"', frozenset({0})),
                 ('blocks add --siblingId X --json \'[{"indentationLevel":1},{"indentationLevel":2}]\'',
                  frozenset({1, 2})),
                 ('blocks add --siblingId X --json \'{broken\'', None),
                 ('blocks add --siblingId X', None))
        for command, levels in cases:
            with self.subTest(command=command):
                self.assertEqual(self.write_of(command).levels, levels)

    def test_a_state_change_is_seen_in_the_flag_the_task_info_and_the_text(self) -> None:
        current = '    - [ ] Задача #задача/1ч'
        changed = ('tasks update --id X --state done',
                   'blocks update --id X --json \'{"taskInfo":{"state":"canceled"}}\'',
                   'blocks update --id X --json \'{"markdown":"- [x] Задача #задача/1ч"}\'',
                   'blocks update --id X --json \'{"markdown":"- [ ] Задача #задача/отменено"}\'')
        for command in changed:
            with self.subTest(command=command):
                self.assertTrue(self.payload.changes_state(self.write_of(command), current))
        kept = ('tasks update --id X --scheduleDate 2026-11-05',
                'blocks update --id X --json \'{"markdown":"- [ ] Новое имя #задача/1ч"}\'',
                'blocks update --id X --json \'{"taskInfo":{"scheduleDate":"2026-11-05"}}\'')
        for command in kept:
            with self.subTest(command=command):
                self.assertFalse(self.payload.changes_state(self.write_of(command), current))

    def test_reopening_is_a_state_change_too(self) -> None:
        write_ = self.write_of('blocks update --id X --json \'{"markdown":"- [ ] ### Этап #этап"}\'')
        self.assertTrue(self.payload.changes_state(write_, '  - [x] ### Этап #этап/готово'))


class ReviewRowTest(ApiLockHarness):
    """Строка `entity` в объёме ревью: тип места записи, без решения замка."""

    ROW_KEYS = {'kind', 'prompt_id', 'command', 'targets', 'place_types', 'content_types',
                'determined', 'note'}

    def rows(self, session: str) -> list[dict]:
        lines = self.storage(session).read_lines('turn-review-volume.jsonl')
        return [json.loads(line) for line in lines]

    def test_a_denied_and_a_passed_write_leave_the_same_row(self) -> None:
        self.start('r-same', PAGE)
        command = f'blocks add --siblingId {D2} --position after --json {BULLET_JSON}'
        self.assert_denied(self.fire({**write('r-same', command), 'prompt_id': 'p-1'}), 'craft-section')
        self.prepare('r-same', 'craft-section')
        self.assert_silent(self.fire({**write('r-same', command), 'prompt_id': 'p-1'}))
        denied, passed = self.rows('r-same')
        self.assertEqual(denied, passed)
        self.assertEqual(set(denied), self.ROW_KEYS)
        self.assertEqual(denied['kind'], 'entity')
        self.assertEqual((denied['prompt_id'], denied['command'], denied['targets'],
                          denied['place_types'], denied['content_types'], denied['determined']),
                         ('p-1', command, [D2], ['#раздел'], [], True))

    def test_owners_and_content_go_to_their_own_lists(self) -> None:
        self.start('r-split', PROJECT)
        commands = (
            f'blocks add --siblingId {TASK} --position after --json {TASK_JSON}',
            f'tasks update --id {TASK} --state done',
            f'tasks update --id {TASK} --scheduleDate 2026-11-05',
            f'blocks move --id {TASK2} --targetSiblingId {BARE_TASK} --position after',
            f'tasks update --id {M_STAGE} --state done',
        )
        for command in commands:
            self.fire(write('r-split', command))
        rows = [(row['place_types'], row['content_types'], row['determined']) for row in self.rows('r-split')]
        self.assertEqual(rows, [(['#этап'], ['#задача'], True),
                                (['#этап'], ['#задача'], True),
                                ([], ['#задача'], True),
                                (['#этап'], ['#задача'], True),
                                ([], ['#этап'], True)])

    def test_a_move_between_stages_names_both_owners(self) -> None:
        self.start('r-move', PROJECT)
        self.fire(write('r-move', f'blocks move --id {TASK2} --targetSiblingId {BARE_TASK} --position after'))
        (row,) = self.rows('r-move')
        self.assertIn(STAGE2[:8], row['note'])
        self.assertIn(f'у владельца {STAGE[:8]}', row['note'])

    def test_a_document_without_a_type_is_an_explicit_empty_type(self) -> None:
        self.start('r-none', DAY)
        self.fire(write('r-none', f'blocks update --id {DAY_LINE} --json {BULLET_JSON}'))
        (row,) = self.rows('r-none')
        self.assertEqual((row['place_types'], row['content_types'], row['determined']), ([], [], True))
        self.assertNotIn('prompt_id', row)

    def test_an_unknown_place_is_an_undetermined_row(self) -> None:
        self.start('r-down', PAGE)
        self.with_api_down(write('r-down', f'blocks update --id {D1} --json {BULLET_JSON}'))
        self.fire(write('r-down', f'blocks update --id ЗАДАЧА --json {BULLET_JSON}'))
        self.fire(write('r-down', f'blocks update --id {TASK} --json {BULLET_JSON}'))
        down, broken, unread = self.rows('r-down')
        self.assertEqual((down['determined'], broken['determined'], unread['determined']),
                         (False, False, False))
        self.assertIn('место цели не определено', down['note'])
        self.assertIn('не найдена среди страниц', unread['note'])

    def test_an_unparsed_level_is_an_undetermined_row_with_every_candidate(self) -> None:
        self.start('r-guess', PROJECT)
        self.fire(write('r-guess', f'blocks add --siblingId {TASK} --position after --json \'{{broken\''))
        (row,) = self.rows('r-guess')
        self.assertEqual((row['place_types'], row['determined']), (['#задача', '#проект', '#этап'], False))
        self.assertIn('отступ записи не разобран', row['note'])

    def test_a_write_of_a_subagent_leaves_no_row(self) -> None:
        self.start('r-agent', PAGE)
        self.fire({**write('r-agent', f'blocks update --id {D1} --json {BULLET_JSON}'), 'agent_id': 'a-1'})
        self.assertEqual(self.rows('r-agent'), [])


if __name__ == '__main__':
    unittest.main()
