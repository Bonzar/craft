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
        self.assertNotIn('craft-decision', tag_skills.values())

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
PROJECT = 'bbbbbbbb-0000-0000-0000-000000000001'
STAGE = 'bbbbbbbb-0000-0000-0000-000000000010'
STAGE_TOGGLE = 'bbbbbbbb-0000-0000-0000-000000000011'
SD1 = 'bbbbbbbb-0000-0000-0000-000000000012'
REQ = 'aaaaaaaa-0000-0000-0000-000000000005'
DEEP_PAGE = 'cccccccc-0000-0000-0000-000000000001'
DEEP_D1 = 'cccccccc-0000-0000-0000-000000000011'
TASK = 'bbbbbbbb-0000-0000-0000-000000000020'
STAGE_LINK = 'bbbbbbbb-0000-0000-0000-000000000030'


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
        block(STAGE_LINK, f'[Этап #этап](block://{STAGE})'),
        {'id': STAGE, 'type': 'page', 'markdown': '- [ ] ### Этап #этап/готово', 'indentationLevel': 1,
         'listStyle': 'task', 'content': [
             block(STAGE_TOGGLE, '+ ### Решения', textStyle='h3', listStyle='toggle'),
             block(SD1, '  - Решение этапа', indentationLevel=1, listStyle='bullet'),
             DEEP_TREE,
         ]},
    ]},
}
TREES[DEEP_PAGE] = DEEP_TREE
# Проект с тегом в заголовке: сущность со своим модулем, «Решения проекта» — его.
TAGGED_PROJECT = 'eeeeeeee-0000-0000-0000-000000000001'
PROJECT_D1 = 'eeeeeeee-0000-0000-0000-000000000011'
TREES[TAGGED_PROJECT] = {'id': TAGGED_PROJECT, 'type': 'page', 'markdown': 'Свадьба #проект', 'content': [
    block('eeeeeeee-0000-0000-0000-000000000010', '+ # Решения проекта', listStyle='toggle'),
    block(PROJECT_D1, '  - Решение проекта', indentationLevel=1, listStyle='bullet'),
]}
UPPER_PAGE = 'DDDDDDDD-0000-0000-0000-000000000001'
UPPER_D1 = 'DDDDDDDD-0000-0000-0000-000000000011'
TREES[UPPER_PAGE] = {'id': UPPER_PAGE, 'type': 'page', 'markdown': 'Старый раздел #раздел', 'content': [
    block('DDDDDDDD-0000-0000-0000-000000000010', '+ ### Решения', listStyle='toggle'),
    block(UPPER_D1, '  - Решение', indentationLevel=1, listStyle='bullet'),
]}


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
        # Как настоящий connect-API: регистр id различается.
        page = query.get('id', [''])[0]
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


BULLET_JSON = '\'{"type":"text","indentationLevel":1,"listStyle":"bullet","markdown":"Новое решение"}\''
SECTION = 'Запись ложится в сущность «Раздел #раздел»'
FIRST = 'aaaaaaaa-0000-0000-0000-000000000003'


class PageTypeLockTest(LockHarness):

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

    def reason(self, result) -> str:
        return json.loads(result.stdout)['hookSpecificOutput']['permissionDecisionReason']

    def test_a_write_into_the_decisions_of_a_section_needs_craft_section(self) -> None:
        self.start('p-next', PAGE)
        result = self.fire(write('p-next', f'blocks add --siblingId {D2} --position after --json {BULLET_JSON}'))
        self.assert_denied(result, SECTION, '#раздел → craft-section', 'не вызван скилл: craft-section',
                           'повтори запись')
        self.assertNotIn('craft-decision', self.reason(result))

    def test_craft_section_this_turn_opens_the_write(self) -> None:
        self.start('p-open', PAGE)
        self.prepare('p-open', 'craft-section')
        self.assert_silent(self.fire(write('p-open', f'blocks add --siblingId {D2} --position after --json {BULLET_JSON}')))
        self.assertIn('шаблоны прочитаны в этом ходе, цель aaaaaaaa в сущности aaaaaaaa (#раздел)',
                      self.last_reason('p-open'))

    def test_a_decision_skill_is_not_an_entity_skill(self) -> None:
        self.start('p-decision', PAGE)
        self.assert_silent(self.fire(skill('p-decision', 'craft-decision')))
        self.assertIn('не скилл типа', self.last_reason('p-decision'))
        result = self.fire(write('p-decision', f'blocks update --id {D1} --json {BULLET_JSON}'))
        self.assert_denied(result, 'craft-section')

    def test_every_place_of_a_section_needs_craft_section(self) -> None:
        self.start('p-anywhere', PAGE)
        for command in (f'blocks add --siblingId {TOGGLE} --position after --json {BULLET_JSON}',
                        f'blocks add --siblingId {LINE} --position before --json {BULLET_JSON}',
                        f'blocks add --siblingId {LINE} --position after --json {BULLET_JSON}',
                        f'blocks add --siblingId {FIRST} --position before --json {BULLET_JSON}',
                        f'blocks add --id {PAGE} --position end --json {BULLET_JSON}',
                        f'blocks update --id {REQ} --json {BULLET_JSON}',
                        f'blocks delete --id {NOTES}'):
            with self.subTest(command=command):
                self.assert_denied(self.fire(write('p-anywhere', command)), SECTION, 'craft-section')

    def test_stage_decisions_need_craft_stage_from_the_project_page(self) -> None:
        self.start('p-stage', PROJECT)
        result = self.fire(write('p-stage', f'blocks add --siblingId {SD1} --position after --json {BULLET_JSON}'))
        self.assert_denied(result, 'Запись ложится в сущность «- [ ] ### Этап #этап/готово»',
                           '#этап → craft-stage')
        self.assertNotIn('craft-section', self.reason(result))

    def test_a_page_without_a_type_tag_needs_no_skill(self) -> None:
        self.start('p-project', PROJECT)
        command = f'blocks add --siblingId {STAGE} --position after --json {BULLET_JSON}'
        self.assert_silent(self.fire(write('p-project', command)))
        self.assertIn('тега типа нет, цель bbbbbbbb на странице без типа', self.last_reason('p-project'))

    def test_project_decisions_need_craft_project(self) -> None:
        self.start('p-tagged', TAGGED_PROJECT)
        command = f'blocks add --siblingId {PROJECT_D1} --position after --json {BULLET_JSON}'
        result = self.fire(write('p-tagged', command))
        self.assert_denied(result, 'Запись ложится в сущность «Свадьба #проект»',
                           '#проект → craft-project', 'не вызван скилл: craft-project')
        self.prepare('p-tagged', 'craft-project')
        self.assert_silent(self.fire(write('p-tagged', command)))

    def test_a_link_with_a_tag_does_not_type_the_block(self) -> None:
        self.start('p-link', PROJECT)
        self.assert_silent(self.fire(write('p-link', f'blocks update --id {STAGE_LINK} --json {BULLET_JSON}')))

    def test_the_body_and_the_state_of_a_task_need_craft_task(self) -> None:
        self.start('p-task', PROJECT)
        for command in (f'blocks add --id {TASK} --position end --json {BULLET_JSON}',
                        f'tasks update --id {TASK} --state done'):
            with self.subTest(command=command):
                self.assert_denied(self.fire(write('p-task', command)),
                                   'Запись ложится в сущность «- [ ] Задача #задача/1ч»', 'craft-task')
        # Сосед задачи ложится в её родителя, а не в неё саму.
        self.assert_silent(self.fire(write(
            'p-task', f'blocks add --siblingId {TASK} --position after --json {BULLET_JSON}')))

    def test_a_tag_in_the_text_and_the_page_need_both_skills(self) -> None:
        self.start('p-both', PAGE)
        payload = BULLET_JSON.replace('Новое решение', '- [ ] Дело #задача')
        result = self.fire(write('p-both', f'blocks add --siblingId {D1} --position after --json {payload}'))
        self.assert_denied(result, '#задача → craft-task', SECTION, 'не вызван скилл: craft-section, craft-task')

    def test_without_reads_the_write_is_into_the_unread(self) -> None:
        self.fire(prompt('p-noread'))
        result = self.fire(write('p-noread', f'blocks add --siblingId {D1} --position after --json {BULLET_JSON}'))
        self.assert_denied(result, 'Запись в непрочитанное', 'нет чтений', 'blocks get', 'тип сущности')

    def test_a_target_outside_the_read_pages_is_into_the_unread(self) -> None:
        self.start('p-elsewhere', PROJECT)
        result = self.fire(write('p-elsewhere', f'blocks add --siblingId {D1} --position after --json {BULLET_JSON}'))
        self.assert_denied(result, 'Запись в непрочитанное', 'не найдена среди страниц')

    def test_a_target_deeper_than_two_levels_counts_as_unread(self) -> None:
        self.start('p-deep', PROJECT)
        result = self.fire(write('p-deep', f'blocks update --id {DEEP_D1} --json {BULLET_JSON}'))
        self.assert_denied(result, 'Запись в непрочитанное')

    def test_a_subpage_read_by_itself_is_judged_by_its_own_title(self) -> None:
        # Граница схемы: сущность выше корня прочитанной страницы замок не видит.
        self.start('p-subpage', PROJECT, DEEP_PAGE)
        self.assert_silent(self.fire(write('p-subpage', f'blocks update --id {DEEP_D1} --json {BULLET_JSON}')))
        self.assertIn('на странице без типа', self.last_reason('p-subpage'))

    def test_moving_a_task_into_a_section_needs_both_skills(self) -> None:
        self.start('p-move', PROJECT, PAGE)
        result = self.fire(write('p-move', f'blocks move --id {TASK} --targetSiblingId {D2} --position after'))
        self.assert_denied(result, 'не вызван скилл: craft-section, craft-task')

    def test_a_target_on_a_later_read_page_is_still_found(self) -> None:
        # Страницы идут свежие первыми: PROJECT свежее, задача найдётся там,
        # а цель переноса D2 — только на PAGE, прочитанной раньше.
        self.start('p-later', PAGE, PROJECT)
        self.prepare('p-later', 'craft-task')
        result = self.fire(write('p-later', f'blocks move --id {TASK} --targetSiblingId {D2} --position after'))
        self.assert_denied(result, 'не вызван скилл: craft-section')

    def test_moving_into_a_page_is_judged_by_that_page(self) -> None:
        self.start('p-move-end', PROJECT, PAGE, DEEP_PAGE)
        self.prepare('p-move-end', 'craft-task')
        result = self.fire(write('p-move-end', f'blocks move --id {TASK} --targetId {PAGE} --position end'))
        self.assert_denied(result, 'не вызван скилл: craft-section')
        result = self.fire(write('p-move-end', f'blocks move --id {TASK} --targetId {DEEP_PAGE} --position end'))
        self.assert_silent(result)

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
        self.api_base = 'http://127.0.0.1:9/api/v1'
        try:
            first = self.fire(write('p-warn', f'blocks update --id {D1} --json {BULLET_JSON}'))
            self.fire(prompt('p-warn'))
            second = self.fire(write('p-warn', f'blocks update --id {D2} --json {BULLET_JSON}'))
        finally:
            self.api_base = type(self).api_base
        text = json.loads(first.stdout)['hookSpecificOutput']['additionalContext']
        self.assertIn('место записи в Craft не проверяется', text)
        self.assertIn('Тип страницы цели не проверен', text)
        self.assert_silent(second)

    def test_a_write_before_the_first_child_of_an_untyped_container_passes(self) -> None:
        self.start('p-container-first', PROJECT, DEEP_PAGE)
        command = f'blocks add --id {DEEP_PAGE} --position before --json {BULLET_JSON}'
        self.assert_silent(self.fire(write('p-container-first', command)))

    def test_a_lower_case_read_of_an_upper_case_page_is_served_by_the_answer(self) -> None:
        self.fire(prompt('p-answer-case'))
        raw = read('p-answer-case', f'blocks get {UPPER_PAGE.lower()} --depth -1')
        raw['tool_response'] = f'<page id="{UPPER_PAGE}">\n</page>'
        self.assert_silent(self.fire(raw))
        result = self.fire(write('p-answer-case', f'blocks update --id {UPPER_D1} --json {BULLET_JSON}'))
        self.assert_denied(result, 'craft-section')

    def test_a_404_is_retried_in_the_other_case(self) -> None:
        self.start('p-retry-case', UPPER_PAGE.lower())
        result = self.fire(write('p-retry-case', f'blocks update --id {UPPER_D1} --json {BULLET_JSON}'))
        self.assert_denied(result, 'craft-section')

    def test_an_upper_case_id_is_requested_as_is(self) -> None:
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
        self.api_base = 'http://127.0.0.1:9/api/v1'
        try:
            result = self.fire(write('p-api', f'blocks update --id {D1} --json {BULLET_JSON}'))
        finally:
            self.api_base = type(self).api_base
        output = json.loads(result.stdout)['hookSpecificOutput']
        self.assertNotIn('permissionDecision', output)
        self.assertIn('API Craft не ответил', output['additionalContext'])

    def test_a_write_without_a_target_id_skips_the_api(self) -> None:
        self.fire(prompt('p-noid'))
        self.assert_silent(self.fire(write('p-noid', 'tasks add --help')))
        self.assertEqual(self.last_reason('p-noid'), 'тега типа нет')


if __name__ == '__main__':
    unittest.main()
