"""Ревью хода turn-review: путь от сырого события Claude до ответа и следа."""

import itertools
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest import mock

from jarvis import confirm, registry
from jarvis.storage import Storage
from jarvis.trace import Trace

from . import CORE_SOURCE, MODULES_DIR

# lib грузится пакетом через реестр ядра, как у хуков: отпечаток и проверки в
# тестах — те же функции. Загрузку по пути без ядра проверяет скрипт отпечатка.
review_lib = registry.LibraryModule('turn-review', MODULES_DIR / 'turn-review' / 'lib').load()
craft_lib = registry.LibraryModule(
    'command-tree-craft', MODULES_DIR / 'command-tree-craft' / 'lib').load()
# Описания видов записей: свой файл ревью, без него поручение не собрать.
DESCRIPTIONS = (MODULES_DIR / 'turn-review' / 'data' / 'review-records.md').read_text(
    encoding='utf-8')

SLUG = 'turn-review'
# Разбор команд Craft: поставщику review-craft-calls и тегам ревью.
CRAFT_PARSER = 'command-tree-craft'
# Поставщики, якорь и модули с критериями: без них объём пуст, а поручение
# без критериев. В тестах набор как у настоящей установки, без модуля
# сверки: он переезжает в другой модуль, и тесты от него не зависят.
PROVIDERS = ('review-craft-calls', 'review-prompts', 'craft-session-anchor')
CRITERIA_MODULES = ('craft-task', 'craft-stage', 'craft-section', 'craft-note', 'craft-call',
                    'craft-reconcile')
# Критерии к ходу с записью задачи: по slug поставщиков, как их находит ревью.
TASK_CRITERIA = ('craft-base', 'consistency', 'anchor', 'task')
TASK_CRITERIA_TEXT = ', '.join(TASK_CRITERIA)
CRAFT_WRITE = 'mcp__Craft__craft_write'
CRAFT_READ = 'mcp__Craft__craft_read'
BLOCK = 'a6784801-9d92-875c-f146-50159368745b'
PARENT = '9a9b271a-3656-53e3-b933-72500de42db3'
TASK_ADD = ('blocks add --siblingId X --position after --json \'{"type":"text","markdown":'
            '"- [ ] Купить молоко #задача/5м [Сборка #этап](block://AB-1)"}\'')
QUESTION = 'Ревью хода не состоялось после трёх поручений. Провести ревью или закончить без него?'

EVENT_OF = {'UserPromptSubmit': 'prompt', 'PostToolUse': 'post-tool', 'Stop': 'stop',
            'SessionStart': 'after-compact', 'MessageDisplay': 'model-message'}


def write_record(command: str = 'blocks add #задача', prompt_id: str | None = None):
    """Запись write объёма, как её читает ревью из строки поставщика."""
    row = {'kind': 'write', 'tool': CRAFT_WRITE, 'command': command, 'result': None}
    return review_lib.parse_record(row if prompt_id is None else {**row, 'prompt_id': prompt_id})


def read_record(command: str = 'blocks get', ids=()):
    return review_lib.parse_record({'kind': 'read', 'command': command, 'ids': list(ids)})


def skill_record(name: str):
    return review_lib.parse_record({'kind': 'skill', 'skill': name})


# Id текущей реплики сессии: вызовы хода несут id своей реплики, как у Claude
# (замер 02.11.2026: PostToolUse и Stop несут prompt_id реплики).
PROMPT_IDS: dict[str, str] = {}
PROMPT_COUNTER = itertools.count(1)
# Реплика без id: так приходит событие старого харнеса.
NO_ID = ''


def prompt(session: str, text: str = 'дальше', prompt_id: str | None = None) -> dict:
    """Реплика с новым id, если он не задан; NO_ID — реплика без id."""
    raw = {'hook_event_name': 'UserPromptSubmit', 'session_id': session, 'cwd': '/work',
           'prompt': text}
    if prompt_id is None:
        prompt_id = f'{session}-p{next(PROMPT_COUNTER)}'
    if prompt_id == NO_ID:
        PROMPT_IDS.pop(session, None)
        return raw
    PROMPT_IDS[session] = prompt_id
    return {**raw, 'prompt_id': prompt_id}


def tool(session: str, name: str, tool_input: dict, response) -> dict:
    raw = {'hook_event_name': 'PostToolUse', 'session_id': session, 'cwd': '/work',
           'tool_name': name, 'tool_input': tool_input, 'tool_response': response}
    prompt_id = PROMPT_IDS.get(session)
    return raw if prompt_id is None else {**raw, 'prompt_id': prompt_id}


def sources(modules: Path, providers=None):
    """Откуда ревью берёт файлы: поставщики по умолчанию — все папки каталога."""
    if providers is None:
        providers = tuple(review_lib.Provider(child.name, child, False)
                          for child in sorted(modules.iterdir()) if child.is_dir())
    return review_lib.Sources(modules, providers)


# Ключ события теста, не харнеса: строка замка craft-type-lock, которую тест
# кладёт в объём перед записью. Замок в этом наборе не стоит (ему нужен
# connect-API), поэтому его строку тест пишет за него, ровно по контракту:
# содержимое — теги типа в тексте команды вне ссылок, как их ищет замок.
LOCK_ROW = 'test_lock_row'
ENTITY_TAGS = ('#задача', '#этап', '#веха', '#раздел', '#заметка', '#тема')


def entity_row(command: str, prompt_id: str | None, place=(), content=None,
               determined: bool = True, note: str = 'цель на странице без типа') -> dict:
    """Строка `entity` замка для записи с этой командой."""
    if content is None:
        content = craft_lib.find_tags(command, ENTITY_TAGS)
    targets = [anchor.block_id for anchor in craft_lib.write_targets(command).anchors]
    row = {'command': command, 'targets': targets, 'place_types': list(place),
           'content_types': list(content), 'determined': determined, 'note': note}
    head = {'kind': 'entity'} if prompt_id is None else {'kind': 'entity', 'prompt_id': prompt_id}
    return {**head, **row}


def write(session: str, command: str = TASK_ADD, place=(), lock: bool = True) -> dict:
    """Запись в Craft; place — типы места из строки замка, lock=False — строки замка нет."""
    raw = tool(session, CRAFT_WRITE, {'command': command}, {'id': BLOCK, 'ok': True})
    if not lock:
        return raw
    return {**raw, LOCK_ROW: entity_row(command, raw.get('prompt_id'), place)}


def read(session: str, command: str) -> dict:
    return tool(session, CRAFT_READ, {'command': command}, '# Страница')


def skill(session: str, name: str) -> dict:
    return tool(session, 'Skill', {'skill': name}, {'success': True})


def ask(session: str, question: str, answer: str,
        labels: tuple[str, ...] | None = None) -> dict:
    options = [{'label': label} for label in labels or (answer,)]
    tool_input = {'questions': [{'question': question, 'header': 'Ревью',
                                 'options': options, 'multiSelect': False}]}
    return tool(session, 'AskUserQuestion', tool_input, {'answers': {question: answer}})


def remark(text: str = 'нет ссылки на этап') -> dict:
    return {'criterion': 'craft-base', 'where': BLOCK, 'text': text}


def stop(session: str, active: bool = True) -> dict:
    return {'hook_event_name': 'Stop', 'session_id': session, 'cwd': '/work',
            'stop_hook_active': active}


def message(session: str, delta: str, final: bool = True) -> dict:
    return {'hook_event_name': 'MessageDisplay', 'session_id': session, 'cwd': '/work',
            'turn_id': 't-1', 'message_id': 'm-1', 'index': 0, 'final': final,
            'delta': delta}


def compact(session: str) -> dict:
    return {'hook_event_name': 'SessionStart', 'session_id': session, 'cwd': '/work',
            'source': 'compact'}


class TurnReviewCase(unittest.TestCase):
    """Набор модулей во временном каталоге и вход обёртки. Тестов здесь нет."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        ignore = shutil.ignore_patterns('__pycache__')
        for slug in (SLUG, CRAFT_PARSER, *PROVIDERS, *CRITERIA_MODULES):
            module_dir = cls.root / 'modules' / slug
            shutil.copytree(MODULES_DIR / slug, module_dir, ignore=ignore)
            shutil.copytree(CORE_SOURCE, module_dir / '_core', ignore=ignore)
        cls.module_dir = cls.root / 'modules' / SLUG
        cls.entry = cls.module_dir / 'hooks' / 'module.py'
        # Кто из набора стоит на каком событии: тест запускает их все, как харнес.
        cls.hooks = {}
        for slug in (SLUG, *PROVIDERS):
            text = (cls.root / 'modules' / slug / 'module.toml').read_text(encoding='utf-8')
            line = next(line for line in text.splitlines() if line.startswith('events'))
            for name in json.loads(line.split('=', 1)[1].strip()):
                cls.hooks.setdefault(name, []).append(cls.root / 'modules' / slug / 'hooks' / 'module.py')
        # Отладочный вывод в источнике может быть включён: тесты ревью идут
        # без него, его тесты включают его сами.
        cls.debug_file = cls.module_dir / 'data' / 'debug.json'
        cls.debug_file.unlink(missing_ok=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def command(self, raw: dict, entry: Path | None = None) -> list[str]:
        return [sys.executable, str(entry or self.entry), '--harness', 'claude',
                '--event', EVENT_OF[raw['hook_event_name']]]

    def env(self, autonomous: bool = False) -> dict:
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.root)}
        if autonomous:
            env['JARVIS_AUTONOMOUS'] = '1'
        return env

    def fire(self, raw: dict, autonomous: bool = False) -> subprocess.CompletedProcess:
        """Событие уходит всем модулям набора на нём; возвращается ответ ревью.

        Строка замка, если она есть у события, ложится в объём раньше хуков:
        замок пишет её на pre-tool, до вызова записи.
        """
        if LOCK_ROW in raw:
            raw = dict(raw)
            row = raw.pop(LOCK_ROW)
            self.storage(raw['session_id']).append_line(
                'turn-review-volume.jsonl', json.dumps(row, ensure_ascii=False))
        event = EVENT_OF[raw['hook_event_name']]
        result = None
        for entry in self.hooks.get(event, [self.entry]):
            run = subprocess.run(
                self.command(raw, entry), input=json.dumps(raw, ensure_ascii=False), text=True,
                capture_output=True, env=self.env(autonomous), check=False,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            if entry == self.entry:
                result = run
        return result

    def storage(self, session: str) -> Storage:
        return Storage(self.root / '.local' / 'state' / 'jarvis', session)

    def state(self, session: str) -> dict:
        return self.storage(session).read_json('turn-review-state.json')

    def volume(self, session: str, every: bool = False) -> list[dict]:
        # Реплики prompt и строки замка entity лежат в объёме рядом с записями
        # хода и без every убираются из выборки; ответы answer остаются.
        lines = self.storage(session).read_lines('turn-review-volume.jsonl')
        rows = [json.loads(line) for line in lines]
        return rows if every else [row for row in rows if row['kind'] not in ('prompt', 'entity')]

    def last_reason(self, session: str) -> str:
        return Trace(self.storage(session)).read()[-1]['reason']

    def write_verdict(self, session: str, volume_id: str, remarks=(), fingerprint: str | None = None,
                      checked=None) -> None:
        if fingerprint is None:
            fingerprint = review_lib.fingerprint(
                [review_lib.parse_record(row) for row in self.volume(session)])
        if checked is None:
            # Критерии последнего поручения; поручения не было — как у записи задачи.
            checked = self.state(session).get('criteria') or list(TASK_CRITERIA)
        self.storage(session).write_json('turn-review-verdict.json', {
            'volume_id': volume_id, 'clean': not remarks, 'remarks': list(remarks),
            'checked': list(checked), 'fingerprint': fingerprint})

    def assert_silent(self, result) -> None:
        self.assertEqual(result.stdout, '')

    def blocked(self, result) -> str:
        payload = json.loads(result.stdout)
        self.assertEqual(payload['decision'], 'block')
        return payload['reason']

    def start_with_write(self, session: str) -> None:
        self.fire(prompt(session))
        self.assert_silent(self.fire(write(session)))

    def nudge_three_times(self, session: str, autonomous: bool = False) -> None:
        for _ in range(3):
            reason = self.blocked(self.fire(stop(session), autonomous))
            self.assertIn('ревью хода не сделано', reason)

    def run_entry(self, raw: dict, entry: Path) -> subprocess.CompletedProcess:
        return subprocess.run(self.command(raw, entry), input=json.dumps(raw, ensure_ascii=False),
                              text=True, capture_output=True, env=self.env(), check=False)

    def shown(self, result) -> str:
        return json.loads(result.stdout)['hookSpecificOutput']['displayContent']

    def assert_question(self, result) -> None:
        reason = self.blocked(result)
        for needle in ('AskUserQuestion', f'«{QUESTION}»', '«Провести ревью»',
                       '«Закончить без ревью»', 'дословно', '«закончить без ревью»'):
            self.assertIn(needle, reason)


class TurnReviewTest(TurnReviewCase):

    def test_stop_without_writes_is_silent(self) -> None:
        self.fire(prompt('s-empty'))
        self.fire(read('s-empty', f'blocks get {BLOCK} --depth -1'))
        self.assert_silent(self.fire(stop('s-empty', active=False)))
        self.assertEqual(self.last_reason('s-empty'), 'записей в Craft не было')

    def test_a_write_blocks_the_stop_with_the_instruction_and_paths(self) -> None:
        self.start_with_write('s-nudge')
        reason = self.blocked(self.fire(stop('s-nudge', active=False)))
        storage = self.storage('s-nudge')
        state = self.state('s-nudge')
        for needle in ('Agent', 'general-purpose', 'не в фоне', 'записей write в объёме сейчас 1',
                       str(storage.path('turn-review-brief.md').resolve()),
                       str(storage.path('turn-review-volume.jsonl').resolve()),
                       str(storage.path('turn-review-verdict.json').resolve()),
                       state['volume_id'], f'критерии {TASK_CRITERIA_TEXT}'):
            self.assertIn(needle, reason)
        self.assertEqual(state['nudges'], 1)
        self.assertEqual(state['criteria'], list(TASK_CRITERIA))
        # Поручение собрано из каркаса, описаний видов записей объёма и
        # критериев модулей; тег задачи ревью нашло в команде само.
        brief = storage.path('turn-review-brief.md').read_text(encoding='utf-8')
        for needle in ('`write` — вызов записи в Craft', '`prompt` — реплика Влада',
                       'Критерий «task»', 'Критерий «craft-base»', 'Критерий «anchor»',
                       '"craft-base", "consistency", "anchor", "task"'):
            self.assertIn(needle, brief)
        for absent in ('Критерий «stage»', 'Критерий «section»', 'Критерий «note»', '{{CRITERIA',
                       '{{RECORDS', '`read` — вызов чтения', '`anchor` — выбор якоря'):
            self.assertNotIn(absent, brief)

    def test_a_write_without_the_lock_row_is_reviewed_by_every_entity_criterion(self) -> None:
        session = 's-untyped'
        self.fire(prompt(session))
        self.assert_silent(self.fire(write(session, lock=False)))
        reason = self.blocked(self.fire(stop(session, active=False)))
        self.assertEqual(sorted(set(self.state(session)['criteria']) & set(ENTITY_CRITERIA)),
                         list(ENTITY_CRITERIA))
        self.assertIn('ревью хода не сделано', reason)
        brief = self.storage(session).path('turn-review-brief.md').read_text(encoding='utf-8')
        for needle in ('## Тип записей', 'Тип записи не известен', 'Это не довод'):
            self.assertIn(needle, brief)
        self.assertNotIn('{{ENTITY_TYPES', brief)

    def test_the_lock_row_brings_the_section_criterion_to_an_untagged_write(self) -> None:
        session = 's-typed'
        command = f'blocks update --id {SECTION_BLOCK} --json \'{{"markdown":"ещё"}}\''
        self.fire(prompt(session))
        self.assert_silent(self.fire(write(session, command, place=['#раздел'])))
        self.blocked(self.fire(stop(session, active=False)))
        self.assertEqual(self.state(session)['criteria'],
                         ['craft-base', 'consistency', 'section', 'anchor'])

    def test_a_clean_verdict_releases_the_turn_and_clears_the_volume(self) -> None:
        self.start_with_write('s-clean')
        self.blocked(self.fire(stop('s-clean')))
        volume_id = self.state('s-clean')['volume_id']
        self.write_verdict('s-clean', volume_id)
        self.assert_silent(self.fire(stop('s-clean')))
        self.assertEqual(self.volume('s-clean'), [])
        self.assertFalse(self.storage('s-clean').path('turn-review-verdict.json').exists())
        state = self.state('s-clean')
        self.assertNotEqual(state['volume_id'], volume_id)
        self.assertEqual(state['nudges'], 0)
        self.assert_silent(self.fire(stop('s-clean')))

    def test_a_verdict_with_a_remark_blocks_with_its_text(self) -> None:
        self.start_with_write('s-remark')
        self.write_verdict('s-remark', self.state('s-remark')['volume_id'], [remark()])
        reason = self.blocked(self.fire(stop('s-remark')))
        self.assertIn(f'[craft-base] {BLOCK}: нет ссылки на этап', reason)
        self.assertIn('Почини', reason)
        self.assertFalse(self.storage('s-remark').path('turn-review-verdict.json').exists())
        self.assertEqual(len(self.volume('s-remark')), 1)
        self.assertEqual(self.state('s-remark')['nudges'], 0)

    def test_a_foreign_volume_id_is_ignored(self) -> None:
        self.start_with_write('s-foreign')
        self.write_verdict('s-foreign', 'старый-объём')
        self.assertIn('ревью хода не сделано', self.blocked(self.fire(stop('s-foreign'))))
        self.assertEqual(len(self.volume('s-foreign')), 1)

    def test_a_malformed_verdict_is_named_in_the_instruction(self) -> None:
        self.start_with_write('s-malformed')
        volume_id = self.state('s-malformed')['volume_id']
        self.storage('s-malformed').write_json('turn-review-verdict.json', {
            'volume_id': volume_id, 'clean': True,
            'remarks': [{'criterion': 'craft-base', 'where': BLOCK, 'text': 'x'}], 'checked': []})
        reason = self.blocked(self.fire(stop('s-malformed')))
        self.assertIn('clean=true при замечаниях', reason)
        self.assertIn('ревью хода не сделано', reason)

    def test_three_nudges_hold_the_turn_with_the_question_and_register_the_phrase(self) -> None:
        # Вопрос идёт блоком: форма «вопрос» на остановке ушла бы контекстом,
        # и Claude отпустил бы ход без вердикта.
        self.start_with_write('s-ask')
        self.nudge_three_times('s-ask')
        self.assert_question(self.fire(stop('s-ask')))
        pending = confirm.pending(self.storage('s-ask'), slug=SLUG)
        self.assertEqual([(item['action'], item['phrase']) for item in pending],
                         [('release', 'закончить без ревью')])
        self.assert_question(self.fire(stop('s-ask')))
        self.assertEqual(self.state('s-ask')['nudges'], 4)

    def test_the_phrase_by_prompt_releases_the_next_stop(self) -> None:
        self.start_with_write('s-phrase')
        self.nudge_three_times('s-phrase')
        self.fire(stop('s-phrase'))
        self.fire(prompt('s-phrase', 'закончить без ревью'))
        self.assert_silent(self.fire(stop('s-phrase')))
        self.assertEqual(self.last_reason('s-phrase'), 'ревью не было по решению человека')
        self.assertEqual(self.volume('s-phrase'), [])

    def test_the_release_button_releases_the_next_stop(self) -> None:
        self.start_with_write('s-button')
        self.nudge_three_times('s-button')
        self.fire(stop('s-button'))
        self.fire(ask('s-button', QUESTION, 'Закончить без ревью'))
        self.assertTrue(self.state('s-button')['released'])
        self.assert_silent(self.fire(stop('s-button')))
        self.assertEqual(self.volume('s-button'), [])
        self.assertFalse(self.state('s-button')['released'])

    def test_the_review_button_starts_the_nudges_again(self) -> None:
        self.start_with_write('s-again')
        self.nudge_three_times('s-again')
        self.fire(stop('s-again'))
        self.fire(ask('s-again', QUESTION, 'Провести ревью'))
        self.assertIn('ревью хода не сделано', self.blocked(self.fire(stop('s-again'))))
        self.assertEqual(self.state('s-again')['nudges'], 1)

    def test_an_autonomous_run_waits_for_the_answer_too(self) -> None:
        # Застрявшее ревью в автономном прогоне ждёт ответа, как в сессии с
        # человеком: ход без ревью не отпускается.
        self.fire(prompt('s-auto'), autonomous=True)
        self.fire(write('s-auto'), autonomous=True)
        self.nudge_three_times('s-auto', autonomous=True)
        self.assert_question(self.fire(stop('s-auto'), autonomous=True))
        self.assertEqual(len(self.volume('s-auto')), 1)
        self.assertEqual(len(confirm.pending(self.storage('s-auto'), slug=SLUG)), 1)

    def test_providers_write_rows_with_their_fields(self) -> None:
        session = 's-rows'
        self.fire(prompt(session))
        self.fire(write(session))
        self.fire(read(session, f'blocks get {BLOCK} --depth -1; blocks get {PARENT}; '
                                f'blocks get {BLOCK}'))
        self.fire(skill(session, 'jarvis:craft-task'))
        self.fire(ask(session, 'Какой якорь у сессии?', 'Задача про ревью'))
        self.fire(tool(session, 'Bash', {'command': 'ls'}, 'ok'))
        rows = self.volume(session)
        prompt_id = PROMPT_IDS[session]
        # Вопрос о якоре записан дважды: как сырой ответ поставщиком реплик и как якорь.
        self.assertEqual([row['kind'] for row in rows], ['write', 'read', 'skill', 'answer', 'anchor'])
        # Команда лежит как есть, без тегов: теги в ней ищет ревью.
        self.assertEqual(rows[0], {'kind': 'write', 'prompt_id': prompt_id, 'tool': CRAFT_WRITE,
                                   'command': TASK_ADD, 'result': {'id': BLOCK, 'ok': True}})
        self.assertEqual(rows[1]['ids'], [BLOCK, PARENT])
        self.assertEqual(rows[2], {'kind': 'skill', 'prompt_id': prompt_id, 'skill': 'craft-task'})
        self.assertEqual(rows[4], {'kind': 'anchor', 'prompt_id': prompt_id,
                                   'answer': {'answers': {'Какой якорь у сессии?':
                                                          'Задача про ревью'}},
                                   'question': 'Какой якорь у сессии?'})
        # Номер хода по id реплики знает ревью.
        self.assertEqual(self.state(session)['prompt_turns'], {prompt_id: 1})

    def test_a_turn_without_writes_is_released_and_keeps_the_volume(self) -> None:
        session = 's-quiet'
        self.start_with_write(session)
        self.blocked(self.fire(stop(session)))
        self.fire(prompt(session, 'А почему так?'))
        self.assert_silent(self.fire(stop(session)))
        self.assertEqual(self.last_reason(session), 'в этом ходе записей не было, объём ждёт хода с записями')
        self.assertEqual([row['kind'] for row in self.volume(session)], ['write'])
        self.assertEqual(self.state(session)['nudges'], 1)
        # Ход с записью снова под ревью, объём общий.
        self.fire(write(session))
        self.assertIn('записей write в объёме сейчас 2', self.blocked(self.fire(stop(session))))

    def test_a_turn_without_writes_does_not_ask_the_stuck_question(self) -> None:
        session = 's-quiet-stuck'
        self.start_with_write(session)
        self.nudge_three_times(session)
        self.assert_question(self.fire(stop(session)))
        self.fire(prompt(session, 'Расскажи подробнее'))
        self.assert_silent(self.fire(stop(session)))
        self.assertEqual(len(self.volume(session)), 1)

    def test_a_damaged_volume_on_a_quiet_turn_is_named(self) -> None:
        session = 's-quiet-damage'
        self.start_with_write(session)
        self.fire(prompt(session, 'А что это?'))
        self.storage(session).append_line('turn-review-volume.jsonl', '{не json')
        reason = self.blocked(self.fire(stop(session)))
        self.assertIn('был испорчен', reason)
        self.assertIn('Скажи об этом человеку', reason)
        self.assertEqual([row['kind'] for row in self.volume(session)], ['write'])

    def test_a_lost_state_does_not_release_earlier_writes(self) -> None:
        session = 's-quiet-lost'
        self.start_with_write(session)
        self.storage(session).path('turn-review-state.json').unlink()
        self.assertIn('ревью хода не сделано', self.blocked(self.fire(stop(session))))
        # Номер хода записи пропал с состоянием: она не из прошлого хода и
        # идёт на ревью и в разговоре, пока чистый вердикт не очистит объём.
        self.fire(prompt(session, 'А что это?'))
        self.assertEqual(self.state(session)['turn'], 1)
        self.assertIn('ревью хода не сделано', self.blocked(self.fire(stop(session))))
        self.write_verdict(session, self.state(session)['volume_id'])
        self.assert_silent(self.fire(stop(session)))

    def test_a_write_without_a_prompt_id_goes_to_review_in_a_quiet_turn(self) -> None:
        # Старый харнес не даёт id: ход записи неизвестен, молча она не отпускается.
        session = 's-quiet-no-id'
        self.fire(prompt(session, prompt_id=NO_ID))
        self.fire(write(session))
        self.assertNotIn('prompt_id', self.volume(session)[0])
        self.blocked(self.fire(stop(session)))
        self.fire(prompt(session, 'А что это?'))
        self.assertIn('ревью хода не сделано', self.blocked(self.fire(stop(session))))

    def test_a_verdict_with_remarks_still_returns_a_turn_without_writes(self) -> None:
        session = 's-quiet-remark'
        self.start_with_write(session)
        self.fire(prompt(session, 'Ок'))
        self.write_verdict(session, self.state(session)['volume_id'], [remark()])
        self.assertIn('нет ссылки на этап', self.blocked(self.fire(stop(session))))

    def test_a_clean_verdict_settles_a_turn_without_writes(self) -> None:
        session = 's-quiet-clean'
        self.start_with_write(session)
        self.fire(prompt(session, 'Ок'))
        self.write_verdict(session, self.state(session)['volume_id'])
        self.assert_silent(self.fire(stop(session)))
        self.assertEqual([row['kind'] for row in self.volume(session)], [])

    def test_the_human_word_goes_to_the_volume(self) -> None:
        session = 's-word'
        self.fire(prompt(session, 'Это не противоречие, оставь как есть'))
        self.fire(ask(session, 'Переносить задачу?', 'Нет'))
        self.fire(prompt(session, '   '))
        rows = self.volume(session, every=True)
        turns = self.state(session)['prompt_turns']
        self.assertEqual((rows[0]['kind'], turns[rows[0]['prompt_id']], rows[0]['text'],
                          rows[0]['author']),
                         ('prompt', 1, 'Это не противоречие, оставь как есть', 'Влад'))
        self.assertRegex(rows[0]['at'], r'^\d{4}-\d{2}-\d{2}T')
        self.assertEqual((rows[1]['kind'], turns[rows[1]['prompt_id']], rows[1]['question']),
                         ('answer', 1, 'Переносить задачу?'))
        self.assertEqual(rows[1]['answer'], {'answers': {'Переносить задачу?': 'Нет'}})
        # Пустая реплика в объём не идёт, ход при этом начался.
        self.assertEqual(len(rows), 2)
        self.assertEqual(self.state(session)['turn'], 2)

    def test_the_human_word_is_cleared_with_the_volume(self) -> None:
        session = 's-word-clear'
        self.fire(prompt(session, 'Беру задачу «Ревью хода»'))
        self.fire(prompt(session, 'Так и оставь'))
        self.fire(write(session))
        self.write_verdict(session, self.state(session)['volume_id'])
        self.assert_silent(self.fire(stop(session)))
        self.assertEqual([row['kind'] for row in self.volume(session, every=True)], ['anchor'])

    def test_an_anchor_named_by_prompt_goes_to_the_volume(self) -> None:
        self.fire(prompt('s-anchor'))
        self.fire(prompt('s-anchor', 'Беру задачу «Собрать ревью хода»'))
        prompt_id = PROMPT_IDS['s-anchor']
        self.assertEqual(self.volume('s-anchor'),
                         [{'kind': 'anchor', 'prompt_id': prompt_id,
                           'answer': 'Беру задачу «Собрать ревью хода»'}])
        self.assertEqual(self.state('s-anchor')['prompt_turns'][prompt_id], 2)

    def test_after_compact_resets_nudges_and_keeps_the_volume(self) -> None:
        self.start_with_write('s-compact')
        self.blocked(self.fire(stop('s-compact')))
        self.blocked(self.fire(stop('s-compact')))
        volume_id = self.state('s-compact')['volume_id']
        self.assert_silent(self.fire(compact('s-compact')))
        state = self.state('s-compact')
        self.assertEqual((state['nudges'], state['volume_id']), (0, volume_id))
        self.assertEqual(len(self.volume('s-compact')), 1)

    def test_remarks_on_every_round_still_reach_the_question(self) -> None:
        for session, autonomous in (('s-loop', False), ('s-loop-auto', True)):
            self.fire(prompt(session), autonomous)
            self.fire(write(session), autonomous)
            for _ in range(3):
                self.assertIn('ревью хода не сделано',
                              self.blocked(self.fire(stop(session), autonomous)))
                self.write_verdict(session, self.state(session)['volume_id'], [remark()])
                reason = self.blocked(self.fire(stop(session), autonomous))
                self.assertIn('нет ссылки на этап', reason)
            self.assertEqual(self.state(session)['nudges'], 3)
            self.assert_question(self.fire(stop(session), autonomous))

    def test_the_stuck_question_carries_the_last_remarks_without_debug(self) -> None:
        # Замечания идут в вопрос человеку всегда: отладочный вывод в тестах
        # ревью выключен, и прежде они ложились в состояние только с ним.
        session = 's-stuck-remarks'
        self.assertFalse(self.debug_file.exists())
        self.start_with_write(session)
        for number in range(3):
            self.assertIn('ревью хода не сделано', self.blocked(self.fire(stop(session))))
            self.write_verdict(session, self.state(session)['volume_id'],
                               [remark(f'замечание раунда {number}')])
            self.assertIn(f'замечание раунда {number}', self.blocked(self.fire(stop(session))))
        result = self.fire(stop(session))
        self.assert_question(result)
        reason = self.blocked(result)
        self.assertIn('Замечания последнего вердикта', reason)
        self.assertIn(f'- [craft-base] {BLOCK}: замечание раунда 2', reason)
        self.assertNotIn('замечание раунда 1', reason)

    def test_the_anchor_survives_a_clean_verdict(self) -> None:
        session = 's-keep-anchor'
        self.fire(prompt(session, 'Беру задачу «Ревью хода»'))
        self.fire(write(session))
        self.write_verdict(session, self.state(session)['volume_id'])
        self.assert_silent(self.fire(stop(session)))
        self.fire(write(session))
        self.assertEqual([row['kind'] for row in self.volume(session)], ['anchor', 'write'])

    def test_a_free_answer_to_the_question_changes_nothing_but_the_raw_answer(self) -> None:
        # Поставщик не знает, что это вопрос ревью: ответ ложится сырьём, а
        # описание записей answer велит подагенту по нему не судить.
        self.start_with_write('s-free')
        self.nudge_three_times('s-free')
        self.fire(stop('s-free'))
        before = self.state('s-free')
        volume = self.volume('s-free', every=True)
        self.fire(ask('s-free', QUESTION, 'Подожди, сам посмотрю'))
        self.assertEqual(self.state('s-free'), before)
        after = self.volume('s-free', every=True)
        self.assertEqual(after[:-1], volume)
        self.assertEqual((after[-1]['kind'], after[-1]['question']), ('answer', QUESTION))
        self.assertIn(QUESTION, self.blocked(self.fire(stop('s-free'))))

    def test_a_question_naming_both_options_is_not_released_by_the_review_answer(self) -> None:
        self.start_with_write('s-both')
        self.nudge_three_times('s-both')
        self.fire(stop('s-both'))
        both = QUESTION + ' Варианты: Провести ревью или Закончить без ревью?'
        self.fire(ask('s-both', both, 'Провести ревью'))
        state = self.state('s-both')
        self.assertEqual((state['released'], state['nudges']), (False, 0))

    def test_compact_after_the_question_and_the_phrase_still_releases(self) -> None:
        self.start_with_write('s-late')
        self.nudge_three_times('s-late')
        self.fire(stop('s-late'))
        self.fire(prompt('s-late', 'закончить без ревью'))
        self.fire(compact('s-late'))
        self.assertEqual(self.state('s-late')['nudges'], 0)
        self.assert_silent(self.fire(stop('s-late')))
        self.assertEqual(self.last_reason('s-late'), 'ревью не было по решению человека')

    def test_the_verdict_survives_a_compact(self) -> None:
        self.start_with_write('s-keep-verdict')
        self.write_verdict('s-keep-verdict', self.state('s-keep-verdict')['volume_id'])
        self.fire(compact('s-keep-verdict'))
        self.assert_silent(self.fire(stop('s-keep-verdict')))
        self.assertEqual(self.last_reason('s-keep-verdict'), 'ревью чистое, ход отпущен')

    def test_parallel_calls_all_land_in_the_volume(self) -> None:
        session = 's-parallel'
        self.fire(prompt(session))
        raws = [read(session, f'blocks get {BLOCK} --n {number}') for number in range(8)]
        provider = self.root / 'modules' / 'review-craft-calls' / 'hooks' / 'module.py'
        workers = [subprocess.Popen(self.command(raw, provider), stdin=subprocess.PIPE, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env())
                   for raw in raws]
        for worker, raw in zip(workers, raws):
            _, err = worker.communicate(json.dumps(raw, ensure_ascii=False))
            self.assertEqual(worker.returncode, 0, err)
        self.assertEqual(sorted(row['command'] for row in self.volume(session)),
                         sorted(raw['tool_input']['command'] for raw in raws))

    def test_a_broken_volume_line_is_dropped_and_named_and_the_review_goes_on(self) -> None:
        session = 's-broken-volume'
        self.start_with_write(session)
        volume_id = self.state(session)['volume_id']
        path = self.storage(session).path('turn-review-volume.jsonl')
        self.write_verdict(session, volume_id)
        self.storage(session).append_line('turn-review-volume.jsonl', '{не json')
        reason = self.blocked(self.fire(stop(session)))
        # Строки: реплика, строка замка, запись, обрыв.
        for needle in (str(path), 'строка 4', 'их ревью не было', 'ревью хода не сделано'):
            self.assertIn(needle, reason)
        self.assertNotEqual(self.state(session)['volume_id'], volume_id)
        self.assertEqual([row['kind'] for row in self.volume(session)], ['write'])

    def test_a_row_that_is_not_a_record_is_dropped_and_named(self) -> None:
        # JSON-объект, но не запись: прежде он шёл в объём словарём и мимо
        # потребителей, теперь чтение отвергает его так же, как обрыв строки.
        session = 's-not-a-record'
        self.start_with_write(session)
        self.storage(session).append_line('turn-review-volume.jsonl',
                                          '{"kind": "write", "command": "blocks add"}')
        reason = self.blocked(self.fire(stop(session)))
        for needle in ('строка 4', 'запись kind write', 'их ревью не было'):
            self.assertIn(needle, reason)
        self.assertEqual([row['kind'] for row in self.volume(session)], ['write'])

    def test_a_volume_broken_to_the_end_is_named_once_and_not_called_empty(self) -> None:
        session = 's-broken-all'
        self.fire(prompt(session))
        self.storage(session).append_line('turn-review-volume.jsonl', '{"kind": "write", обрыв')
        reason = self.blocked(self.fire(stop(session)))
        self.assertIn('их ревью не было', reason)
        self.assertIn('Скажи об этом человеку', reason)
        self.assert_silent(self.fire(stop(session)))

    def test_a_broken_state_is_made_anew_and_the_review_is_asked(self) -> None:
        session = 's-broken-state'
        self.start_with_write(session)
        self.write_verdict(session, self.state(session)['volume_id'])
        self.storage(session).append_line('turn-review-state.json', '{не json')
        reason = self.blocked(self.fire(stop(session)))
        self.assertIn(str(self.storage(session).path('turn-review-state.json')), reason)
        self.assertIn('заведён заново', reason)
        self.assertIn('ревью хода не сделано', reason)
        self.assertEqual(self.state(session)['nudges'], 1)

    def test_a_write_after_a_clean_verdict_needs_a_new_review(self) -> None:
        session = 's-late-write'
        self.start_with_write(session)
        self.blocked(self.fire(stop(session)))
        self.write_verdict(session, self.state(session)['volume_id'])
        self.fire(write(session, 'blocks update --id X --json \'{"markdown":"ещё"}\''))
        reason = self.blocked(self.fire(stop(session)))
        self.assertIn('не сходится с объёмом', reason)
        self.assertIn('ревью хода не сделано', reason)
        self.assertEqual([row['kind'] for row in self.volume(session)], ['write', 'write'])

    def test_a_clean_verdict_without_all_criteria_is_not_taken(self) -> None:
        session = 's-unchecked'
        self.start_with_write(session)
        self.blocked(self.fire(stop(session)))
        self.write_verdict(session, self.state(session)['volume_id'], checked=())
        reason = self.blocked(self.fire(stop(session)))
        self.assertIn(f'критерии не пройдены: {TASK_CRITERIA_TEXT}', reason)
        self.write_verdict(session, self.state(session)['volume_id'],
                           checked=(*TASK_CRITERIA, 'код'))
        self.assertIn(f'критерии не из {TASK_CRITERIA_TEXT}: код',
                      self.blocked(self.fire(stop(session))))
        self.assertEqual(len(self.volume(session)), 1)

    def test_the_release_button_counts_under_a_retold_question(self) -> None:
        session = 's-retold'
        self.start_with_write(session)
        self.nudge_three_times(session)
        self.fire(stop(session))
        self.fire(ask(session, 'Ревью так и не прошло. Что делаем?', 'Закончить без ревью',
                      labels=('Провести ревью', 'Закончить без ревью')))
        self.assertTrue(self.state(session)['released'])
        self.assert_silent(self.fire(stop(session)))
        self.assertEqual(self.last_reason(session), 'ревью не было по решению человека')

    def test_not_taking_a_task_is_not_an_anchor(self) -> None:
        self.fire(prompt('s-no-anchor', 'Нет, не беру задачу про ревью'))
        self.fire(prompt('s-no-anchor', 'Ладно. Якорь: ревью хода'))
        self.assertEqual([row['answer'] for row in self.volume('s-no-anchor')],
                         ['Ладно. Якорь: ревью хода'])

    def test_calls_inside_a_subagent_stay_out_of_the_volume(self) -> None:
        self.fire(prompt('s-agent'))
        raw = {**read('s-agent', f'blocks get {BLOCK}'),
               'agent_id': 'a1', 'agent_type': 'general-purpose'}
        self.assert_silent(self.fire(raw))
        self.assertEqual(self.volume('s-agent'), [])

    def test_a_clean_verdict_without_criteria_keeps_the_answer_hidden(self) -> None:
        # Превью судит по критериям поручения, а не по `checked` самого
        # вердикта: иначе пустой `checked` открыл бы ответ, который stop отвергнет.
        for session, nudged in (('s-hide-nudged', True), ('s-hide-fresh', False)):
            self.start_with_write(session)
            if nudged:
                self.blocked(self.fire(stop(session)))
            self.write_verdict(session, self.state(session)['volume_id'], checked=())
            self.assertEqual(self.shown(self.fire(message(session, 'Готово.'))),
                             'Ревью хода идёт, отчёт после него.')
            self.assertIn('критерии не пройдены', self.blocked(self.fire(stop(session))))

    def test_a_clean_verdict_with_all_criteria_shows_the_answer(self) -> None:
        session = 's-show'
        self.start_with_write(session)
        self.blocked(self.fire(stop(session)))
        self.write_verdict(session, self.state(session)['volume_id'])
        self.assert_silent(self.fire(message(session, 'Готово.')))

    def turns_of(self, session: str) -> list[tuple[str, int]]:
        """Вид и номер хода каждой строки объёма: номер по id реплики из состояния."""
        turns = self.state(session)['prompt_turns']
        return [(row['kind'], turns[row['prompt_id']])
                for row in self.volume(session, every=True)]

    def test_prompt_hooks_give_one_turn_in_any_order(self) -> None:
        # Хуки одного события харнес запускает параллельно: номер хода у
        # записей реплики не зависит от того, кто из них пришёл первым.
        entries = self.hooks['prompt']
        self.assertEqual(entries[0], self.entry)
        for session, order in (('s-order-review', entries), ('s-order-providers', entries[::-1])):
            for text in ('Беру задачу «Ревью»', 'Беру задачу «Ревью»', 'дальше'):
                raw = prompt(session, text)
                for entry in order:
                    run = self.run_entry(raw, entry)
                    self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(sorted(self.turns_of(session)),
                             [('anchor', 1), ('anchor', 2), ('prompt', 1), ('prompt', 2),
                              ('prompt', 3)])
            self.assertEqual(self.state(session)['turn'], 3)

    def test_identical_prompts_with_ids_keep_their_turns_when_a_provider_skips_one(self) -> None:
        # craft-session-anchor пропустил первую реплику (выключен режимом), на второй
        # такой же пришёл первым. По id реплики его запись всё равно в новом ходе.
        session = 's-skip-provider'
        anchor = self.root / 'modules' / 'craft-session-anchor' / 'hooks' / 'module.py'
        text = 'Беру задачу «Ревью»'
        for entry in self.hooks['prompt']:
            if entry != anchor:
                self.assertEqual(self.run_entry(prompt(session, text, 'p-1'), entry).returncode, 0)
        for entry in (anchor, *[item for item in self.hooks['prompt'] if item != anchor]):
            run = self.run_entry(prompt(session, text, 'p-2'), entry)
            self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.turns_of(session), [('prompt', 1), ('anchor', 2), ('prompt', 2)])
        self.assertEqual(self.state(session)['turn'], 2)

    def test_a_damaged_state_does_not_touch_providers(self) -> None:
        # Поставщик состояние ревью не читает: запись ложится, как всегда.
        session = 's-provider-damage'
        self.start_with_write(session)
        self.storage(session).path('turn-review-state.json').write_text('{не json', encoding='utf-8')
        provider = self.root / 'modules' / 'review-craft-calls' / 'hooks' / 'module.py'
        run = self.run_entry(write(session), provider)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual([row['kind'] for row in self.volume(session)], ['write', 'write'])
        reason = self.blocked(self.fire(stop(session)))
        self.assertIn('заведён заново', reason)
        self.assertIn('записей write в объёме сейчас 2', reason)

    def test_no_brief_yet_is_none_and_an_empty_brief_list_is_kept(self) -> None:
        # None — поручения не было, критерии берутся по объёму. Пустой список —
        # поручение без критериев: чистый вердикт по нему не принимается.
        session = 's-empty-criteria'
        self.start_with_write(session)
        self.assertIsNone(self.state(session)['criteria'])
        storage = self.storage(session)
        storage.write_json('turn-review-state.json', {**self.state(session), 'criteria': []})
        self.write_verdict(session, self.state(session)['volume_id'], checked=TASK_CRITERIA)
        self.assertIn('один критерий не подошёл', self.blocked(self.fire(stop(session))))
        self.assertEqual(len(self.volume(session)), 1)

    def test_a_tag_in_a_link_text_does_not_choose_its_criterion(self) -> None:
        # Тег в тексте ссылки на блок — имя цели, а не тип записи.
        session = 's-link-tag'
        self.fire(prompt(session))
        self.fire(write(session, 'blocks update --id X --json \'{"markdown":"[Сборка #этап](block://AB-1)"}\''))
        self.blocked(self.fire(stop(session)))
        self.assertEqual(self.state(session)['criteria'], ['craft-base', 'consistency', 'anchor'])

    def test_an_unwritable_brief_is_a_block_and_not_a_crash(self) -> None:
        session = 's-brief-dir'
        self.start_with_write(session)
        self.storage(session).path('turn-review-brief.md').mkdir(parents=True)
        reason = self.blocked(self.fire(stop(session)))
        self.assertIn('turn-review-brief.md не записать', reason)
        self.assertIn('Скажи об этом человеку', reason)
        self.assertEqual(self.state(session)['nudges'], 1)


class PromptTurnTest(unittest.TestCase):
    """Номер хода реплики: по id реплики харнеса, его ставит только ревью."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name), 's-prompt-turn')

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def claim(self, prompt_id: str) -> int:
        raw = prompt('s-prompt-turn', 'дальше', prompt_id)
        event = SimpleNamespace(prompt_text='дальше', raw=raw)
        turn, problem = review_lib.claim_prompt_turn(self.storage, event)
        self.assertIsNone(problem)
        return turn

    def test_the_same_id_twice_is_one_turn(self) -> None:
        # Хук ревью, запущенный на реплике дважды, ход не делит.
        self.assertEqual([self.claim('p-1'), self.claim('p-1')], [1, 1])
        self.assertEqual(review_lib.load_state(self.storage)[0].prompt_turns, {'p-1': 1})

    def test_identical_prompts_with_their_ids_are_two_turns(self) -> None:
        self.assertEqual([self.claim('p-1'), self.claim('p-2')], [1, 2])
        self.assertEqual(review_lib.load_state(self.storage)[0].prompt_turns,
                         {'p-1': 1, 'p-2': 2})

    def test_without_an_id_every_prompt_is_a_new_turn(self) -> None:
        self.assertEqual([self.claim(NO_ID), self.claim(NO_ID)], [1, 2])
        self.assertEqual(review_lib.load_state(self.storage)[0].prompt_turns, {})

    def test_an_empty_or_foreign_id_is_no_id(self) -> None:
        for bad in ('', 7):
            with self.subTest(prompt_id=bad):
                self.assertIsNone(review_lib.prompt_id_of(
                    SimpleNamespace(prompt_text='т', raw={'prompt_id': bad})))
        self.assertEqual(review_lib.prompt_id_of(
            SimpleNamespace(prompt_text='т', raw={'prompt_id': 'p-1'})), 'p-1')


class RecordTurnTest(unittest.TestCase):
    """Ход записи — по id её реплики; неизвестный ход на ревью, а не в прошлое."""

    TURNS = MappingProxyType({'p-1': 1, 'p-2': 2})

    def test_only_known_earlier_writes_are_before_the_turn(self) -> None:
        cases = (([write_record(prompt_id='p-1')], True),
                 ([write_record(prompt_id='p-1'), write_record(prompt_id='p-2')], False),
                 ([write_record()], False),
                 ([write_record(prompt_id='p-чужой')], False),
                 ([read_record()], False), ([], False))
        for volume, expected in cases:
            with self.subTest(volume=volume):
                self.assertEqual(review_lib.writes_before_turn(volume, 2, self.TURNS), expected)

    def test_a_clear_keeps_the_turns_of_the_current_prompt_only(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            storage = Storage(Path(folder), 's-clear')
            state = review_lib.ReviewState(turn=2, prompt_turns=self.TURNS)
            cleared = review_lib.clear_volume(storage, state)
        self.assertEqual((cleared.turn, dict(cleared.prompt_turns)), (2, {'p-2': 2}))


ENTITY_OK = {'kind': 'entity', 'command': 'c', 'targets': [], 'place_types': [],
             'content_types': [], 'determined': True, 'note': 'n'}


class RecordTest(unittest.TestCase):
    """Строка объёма по контракту: размеченное объединение по kind, схема в lib."""

    ROWS = (
        {'kind': 'write', 'prompt_id': 'p', 'tool': 't', 'command': 'c', 'result': {'id': 1}},
        {'kind': 'write', 'tool': 't', 'command': 'c', 'result': None},
        {'kind': 'read', 'prompt_id': 'p', 'command': 'c', 'ids': ['a']},
        {'kind': 'skill', 'skill': 'craft-task'},
        {'kind': 'prompt', 'prompt_id': 'p', 'text': 'т', 'author': 'Влад', 'at': 't'},
        {'kind': 'answer', 'question': 'в', 'answer': 'о', 'author': 'Влад', 'at': 't'},
        {'kind': 'anchor', 'answer': 'Беру задачу'},
        {'kind': 'anchor', 'prompt_id': 'p', 'answer': {'a': 1}, 'question': 'Якорь?'},
        {'kind': 'entity', 'prompt_id': 'p', 'command': 'c', 'targets': ['a'],
         'place_types': ['#проект'], 'content_types': ['#задача'], 'determined': True,
         'note': 'цель a в сущности b (#проект)'},
        {'kind': 'entity', 'command': 'c', 'targets': [], 'place_types': [], 'content_types': [],
         'determined': False, 'note': 'API'},
    )

    def test_every_kind_reads_and_writes_back_the_same_row(self) -> None:
        self.assertEqual({row['kind'] for row in self.ROWS}, set(review_lib.KINDS))
        for row in self.ROWS:
            with self.subTest(row=row):
                self.assertEqual(review_lib.record_row(review_lib.parse_record(row)), row)

    def test_a_wrong_row_is_refused(self) -> None:
        for row in ({'kind': 'read', 'command': 1, 'ids': []},
                    {'kind': 'read', 'command': 'c', 'ids': 'a'},
                    {'kind': 'write', 'tool': 't', 'command': 'c'},
                    {'kind': 'write', 'tool': 't', 'command': 'c', 'result': None,
                     'types': ['#задача']},
                    {'kind': 'answer', 'question': 'в', 'answer': 1, 'author': 'В', 'at': 't'},
                    {'kind': 'skill', 'skill': 'x', 'turn': 1},
                    {'kind': 'skill', 'skill': 'x', 'prompt_id': 7},
                    {**ENTITY_OK, 'place_types': ['#Раздел']},
                    {**ENTITY_OK, 'content_types': ['раздел']},
                    {**ENTITY_OK, 'determined': 'да'},
                    {key: value for key, value in ENTITY_OK.items() if key != 'note'},
                    {'command': 'x'}, {'kind': None}, 'write'):
            with self.subTest(row=row), self.assertRaises(ValueError):
                review_lib.parse_record(row)

    def test_only_a_record_goes_to_a_row(self) -> None:
        with self.assertRaises(ValueError):
            review_lib.record_row({'kind': 'skill', 'skill': 'x'})


class VolumeReadTest(unittest.TestCase):
    """Объём читается в типы записей; не запись — порча с причиной."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name), 's-read')

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def parse(self, *lines: str):
        path = self.storage.path('turn-review-volume.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(line + '\n' for line in lines), encoding='utf-8')
        return review_lib.parse_volume_file(path)

    def test_rows_appended_by_the_core_read_back_as_records(self) -> None:
        for row in RecordTest.ROWS:
            self.storage.append_line('turn-review-volume.jsonl', json.dumps(row, ensure_ascii=False))
        read, damage = review_lib.parse_volume_file(self.storage.path('turn-review-volume.jsonl'))
        self.assertEqual(damage, [])
        self.assertEqual(read, [review_lib.parse_record(row) for row in RecordTest.ROWS])

    def test_a_row_that_is_not_a_record_is_damage_with_its_reason(self) -> None:
        good = json.dumps(review_lib.record_row(skill_record('x')))
        cases = (('[1]', 'не объект'), ('{"command": "x"}', "kind None"),
                 ('{"kind": "skill"}', 'skill'), ('{"kind": "skill", "skill": "x", "лишнее": 1}',
                                                   'лишние поля лишнее'),
                 ('{"kind": "read", "command": "c", "ids": [1]}', 'ids'),
                 ('{"kind": "skill", "skill": "x", "turn": 1}', 'лишние поля turn'),
                 ('{"kind": "skill", "skill": "x", "prompt_id": true}', 'prompt_id'),
                 ('{"kind": ["write"]}', "kind ['write']"), ('{"kind": {"a": 1}}', "kind {'a': 1}"))
        for line, reason in cases:
            with self.subTest(line=line):
                records, damage = self.parse(good, line)
                self.assertEqual(records, [skill_record('x')])
                self.assertEqual(len(damage), 1)
                self.assertTrue(damage[0].startswith('строка 2: '), damage[0])
                self.assertIn(reason, damage[0])

    def test_load_volume_drops_the_bad_row_and_names_it(self) -> None:
        good = json.dumps(review_lib.record_row(skill_record('x')))
        self.parse(good, '{"kind": "write"}')
        records, problem = review_lib.load_volume(self.storage)
        self.assertEqual(records, [skill_record('x')])
        self.assertIn('строка 2', problem)
        self.assertEqual(self.storage.read_lines('turn-review-volume.jsonl'), [good])


class VolumeLockTest(unittest.TestCase):
    """Очистка объёма и дописывание поставщиков идут под локом журнала ядра."""

    ANCHORS = 40
    CLEARS = 15

    def test_rows_appended_during_clears_are_not_lost(self) -> None:
        # Якорь очистку переживает: если строка, легшая между чтением и
        # заменой файла, пропала бы, якорей в конце было бы меньше.
        with tempfile.TemporaryDirectory() as folder:
            storage = Storage(Path(folder), 's-lock')

            def append_anchors() -> None:
                for number in range(self.ANCHORS):
                    storage.append_line('turn-review-volume.jsonl', json.dumps(
                        {'kind': 'anchor', 'answer': f'якорь {number}'}, ensure_ascii=False))
                    storage.append_line('turn-review-volume.jsonl', json.dumps(
                        {'kind': 'skill', 'skill': 'x'}))

            worker = threading.Thread(target=append_anchors)
            worker.start()
            state = review_lib.ReviewState()
            for _ in range(self.CLEARS):
                state = review_lib.clear_volume(storage, state)
            worker.join(10)
            records, damage = review_lib.parse_volume_file(storage.path('turn-review-volume.jsonl'))
        self.assertEqual(damage, [])
        self.assertEqual(sorted(record.answer for record in records if record.kind == 'anchor'),
                         sorted(f'якорь {number}' for number in range(self.ANCHORS)))


class TagsTest(unittest.TestCase):
    """Критерий по тегам: тип записи известен — по нему, не известен — при любой записи."""

    TASK = review_lib.make_criterion('id: task\napplies: tags #задача\n\nТекст.')

    def test_unknown_types_apply_tag_criteria_to_any_write(self) -> None:
        volume = [write_record('blocks add')]
        self.assertTrue(review_lib.criterion_applies(self.TASK, volume, None))
        self.assertFalse(review_lib.criterion_applies(self.TASK, [read_record()], None))
        self.assertFalse(review_lib.criterion_applies(self.TASK, volume, frozenset({'#раздел'})))
        self.assertIsNone(review_lib.criteria_problem(volume, ('task',), (self.TASK,), ()))

    def test_the_review_does_not_search_tags_in_the_command_itself(self) -> None:
        # Тег в тексте записи без строки замка не выбирает критерий сам: тип
        # не известен, и берутся все критерии сущностей.
        command = '- [ ] Купить #задача'
        volume = [review_lib.parse_record(entity_row(command, None, content=[])),
                  write_record(command)]
        self.assertEqual(review_lib.selection_tags(volume), frozenset())


SECTION_BLOCK = 'aaaaaaaa-0000-0000-0000-000000000011'
OTHER_BLOCK = 'aaaaaaaa-0000-0000-0000-000000000012'
DECISION_ADD = (f'blocks add --siblingId {SECTION_BLOCK} --position after --json '
                '\'{"type":"text","markdown":"Новое решение"}\'')
ENTITY_CRITERIA = ('note', 'section', 'stage', 'task')


class EntityTypeTest(unittest.TestCase):
    """Тип записи — только из строки замка: место ∪ содержимое; без строки — все критерии."""

    PROVIDERS = tuple(review_lib.Provider(slug, MODULES_DIR / slug, False)
                      for slug in CRITERIA_MODULES)

    def taken(self, volume):
        return review_lib.review_criteria(sources(MODULES_DIR, self.PROVIDERS), list(volume), None)

    def entity_ids(self, volume) -> list[str]:
        return sorted(set(self.taken(volume).criteria) & set(ENTITY_CRITERIA))

    def brief(self, volume) -> str:
        return review_lib.assemble_brief(sources(MODULES_DIR, self.PROVIDERS), list(volume),
                                         '{{ENTITY_TYPES}}', DESCRIPTIONS).text

    @staticmethod
    def row(command: str = DECISION_ADD, prompt_id='p', **fields):
        return review_lib.parse_record(entity_row(command, prompt_id, **fields))

    def test_the_place_type_brings_the_criterion_of_a_write_without_a_tag(self) -> None:
        volume = [self.row(place=['#раздел']), write_record(DECISION_ADD, 'p')]
        self.assertEqual(self.entity_ids(volume), ['section'])
        self.assertIsNone(self.taken(volume).problem)

    def test_place_and_content_are_united(self) -> None:
        command = DECISION_ADD.replace('Новое решение', '- [ ] Дело #задача')
        volume = [self.row(command, place=['#раздел']), write_record(command, 'p')]
        self.assertEqual(self.entity_ids(volume), ['section', 'task'])

    def test_a_write_without_a_lock_row_takes_every_entity_criterion(self) -> None:
        # Замка нет, он выключен или упал: строки нет, критерии берутся все.
        volume = [write_record(DECISION_ADD, 'p')]
        self.assertEqual(self.entity_ids(volume), list(ENTITY_CRITERIA))
        # Чистый вердикт по всем критериям принимается: тип выбирает критерии, не вердикт.
        self.assertIsNone(self.taken(volume).problem)
        brief = self.brief(volume)
        for needle in ('Тип записи не известен', 'запись write № 1', 'строки типа от замка',
                       'Определи сам, какие сущности задела'):
            self.assertIn(needle, brief)

    def test_an_undetermined_row_takes_every_entity_criterion_and_names_why(self) -> None:
        volume = [self.row(place=['#раздел'], determined=False,
                           note='место цели не определено: API Craft не ответил'),
                  write_record(DECISION_ADD, 'p')]
        self.assertEqual(self.entity_ids(volume), list(ENTITY_CRITERIA))
        self.assertIn('API Craft не ответил', self.brief(volume))

    def test_a_row_of_another_reply_or_another_command_does_not_type_the_write(self) -> None:
        other = DECISION_ADD.replace(SECTION_BLOCK, OTHER_BLOCK)
        for row in (self.row(prompt_id='другая', place=['#раздел']),
                    self.row(other, place=['#раздел']), self.row(prompt_id=None)):
            with self.subTest(row=row):
                volume = [row, write_record(DECISION_ADD, 'p')]
                self.assertEqual(self.entity_ids(volume), list(ENTITY_CRITERIA))

    def test_a_row_after_the_write_does_not_type_it(self) -> None:
        volume = [write_record(DECISION_ADD, 'p'), self.row()]
        self.assertEqual(self.entity_ids(volume), list(ENTITY_CRITERIA))

    def test_one_row_types_one_write(self) -> None:
        volume = [self.row(), write_record(DECISION_ADD, 'p'), write_record(DECISION_ADD, 'p')]
        self.assertEqual(self.entity_ids(volume), list(ENTITY_CRITERIA))
        self.assertIn('запись write № 2', self.brief(volume))
        self.assertNotIn('запись write № 1', self.brief(volume))

    def test_a_retry_takes_its_own_row_and_not_the_denied_one(self) -> None:
        # Запрещённая попытка оставила строку без записи; повтор кладёт свою.
        volume = [self.row(determined=False, note='запись в непрочитанное'),
                  self.row(place=['#раздел']), write_record(DECISION_ADD, 'p')]
        self.assertEqual(self.entity_ids(volume), ['section'])
        self.assertIn('Тип известен у всех записей', self.brief(volume))

    def test_the_brief_says_the_type_is_only_a_selector(self) -> None:
        volume = [self.row(place=['#раздел']), write_record(DECISION_ADD, 'p')]
        brief = self.brief(volume)
        self.assertIn('Тип выбирает только критерии. Это не довод, что запись верна', brief)
        self.assertIn('записанный блок ты читаешь сам', brief)


class TakenCriteriaTest(unittest.TestCase):
    """Критерии поручения сверяются с найденными: пропавший id называется."""

    TASK = review_lib.make_criterion('id: task\napplies: tags #задача\n\nТекст.')

    def test_a_taken_id_without_a_found_criterion_is_a_problem(self) -> None:
        problem = review_lib.criteria_problem([write_record()], ['task', 'stage'], [self.TASK], [])
        self.assertIn('критерий «stage» из поручения', problem)
        self.assertNotIn('«task»', problem)

    def test_taken_ids_all_found_are_no_problem(self) -> None:
        self.assertIsNone(review_lib.criteria_problem([write_record()], ['task'], [self.TASK], []))


class StateTest(unittest.TestCase):
    """Состояние: поля по имени, поля с диска не теряются, элементы проверяются."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name), 's-state')

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def disk(self) -> dict:
        return self.storage.read_json('turn-review-state.json')

    def test_fields_are_keyword_only(self) -> None:
        with self.assertRaises(TypeError):
            review_lib.ReviewState(1)
        self.assertEqual(review_lib.ReviewState(turn=1, volume_id='объём').volume_id, 'объём')

    def test_no_brief_and_a_brief_without_criteria_differ(self) -> None:
        self.assertIs(review_lib.ReviewState().criteria, review_lib.NO_BRIEF)
        self.assertIsNone(review_lib.ReviewState().to_json()['criteria'])
        self.assertEqual(review_lib.ReviewState(criteria=()).to_json()['criteria'], [])

    def test_load_keeps_every_field_from_the_disk(self) -> None:
        state = review_lib.ReviewState(turn=3, volume_id='v', nudges=2, criteria=('task',),
                                       last_remarks=({'criterion': 'task'},),
                                       prompt_turns=MappingProxyType({'p-1': 3}))
        self.storage.write_json('turn-review-state.json', {**state.to_json(), 'future': 1})
        loaded, problem = review_lib.load_state(self.storage)
        self.assertIsNone(problem)
        self.assertEqual(replace(loaded, unknown={}), state)
        review_lib.save_state(self.storage, loaded)
        self.assertEqual(self.disk(), {**state.to_json(), 'future': 1})

    def test_load_fills_a_missing_field_with_its_default(self) -> None:
        data = review_lib.ReviewState(turn=3, volume_id='v').to_json()
        del data['prompt_turns']
        self.storage.write_json('turn-review-state.json', data)
        loaded, problem = review_lib.load_state(self.storage)
        self.assertIsNone(problem)
        self.assertEqual(loaded.prompt_turns, {})

    def test_wrong_items_are_damage_and_not_a_crash_later(self) -> None:
        for field_name, value in (('criteria', [1]), ('prompt_turns', {'p-1': '1'}),
                                  ('prompt_turns', ['p-1']), ('prompt_turns', {'': 1}),
                                  ('last_remarks', ['нет ссылки']), ('criteria', 'task'),
                                  ('turn', True), ('volume_id', '')):
            self.storage.write_json('turn-review-state.json', {
                **review_lib.ReviewState(turn=2, volume_id='v').to_json(), field_name: value})
            loaded, problem = review_lib.load_state(self.storage)
            self.assertIn('испорчен', problem, field_name)
            self.assertEqual(loaded.turn, 0)

    def test_save_refuses_what_load_would_refuse(self) -> None:
        # Раньше save_state писал что угодно, а load_state потом отвергал файл.
        for state in ({'turn': '1'}, {**review_lib.ReviewState().to_json(), 'turn': '1'}):
            with self.subTest(state=state), self.assertRaises(ValueError):
                review_lib.save_state(self.storage, state)
        self.assertFalse(self.storage.path('turn-review-state.json').exists())

    def test_the_state_checks_itself_on_every_construction(self) -> None:
        state = review_lib.ReviewState(turn=2)
        for change in ({'turn': '1'}, {'nudges': None}, {'released': 1}, {'criteria': ['task']},
                       {'criteria': ('task', 1)}, {'prompt_turns': {'p': True}},
                       {'volume_id': ''},
                       {'last_remarks': ('нет ссылки',)}, {'summary_shown': None}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(state, **change)

    def test_json_round_trip_keeps_the_state(self) -> None:
        state = review_lib.ReviewState(turn=3, nudges=1, criteria=('task',),
                                       last_remarks=({'criterion': 'task'},),
                                       prompt_turns=MappingProxyType({'a': 1, 'b': 2}),
                                       summary='итог')
        data = json.loads(json.dumps(state.to_json()))
        self.assertEqual(review_lib.ReviewState.from_json(data), state)


class CriterionFilesTest(unittest.TestCase):
    """Файлы критериев: что не разобралось, называется модулем и причиной."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.modules = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def criterion(self, slug: str, text: str) -> None:
        path = self.modules / slug / 'data' / 'review-criterion.md'
        path.parent.mkdir(parents=True)
        path.write_text(text, encoding='utf-8')

    def assemble(self, volume=()):
        return review_lib.assemble_brief(sources(self.modules), list(volume), '{{CRITERIA}}',
                                         DESCRIPTIONS)

    def test_an_unknown_applies_rule_is_broken(self) -> None:
        for number, rule in enumerate(('alway', 'craf', 'tags', 'tags задача', '')):
            self.criterion(f'm{number}', f'id: c{number}\napplies: {rule}\n\nТекст.\n')
        self.criterion('ok', 'id: ok\napplies: tags #задача\n\nТекст.\n')
        _, ids, broken = self.assemble()
        self.assertEqual(ids, ())
        self.assertEqual([item.split(' ', 1)[0] for item in broken], ['m0', 'm1', 'm2', 'm3', 'm4'])
        self.assertIn('applies', broken[0])

    def test_a_repeated_id_is_broken_with_both_modules(self) -> None:
        self.criterion('first', 'id: task\napplies: always\n\nПервый.\n')
        self.criterion('second', 'id: task\napplies: always\n\nВторой.\n')
        text, ids, broken = self.assemble()
        self.assertEqual(ids, ('task',))
        self.assertEqual(text, 'Первый.')
        self.assertEqual(len(broken), 1)
        self.assertIn('second', broken[0])
        self.assertIn('first', broken[0])

    def test_a_head_without_a_blank_line_is_broken(self) -> None:
        self.criterion('glued', 'id: task\napplies: always\n## Критерий «task»: задача\nТекст.\n')
        self.criterion('plain', 'id: plain\napplies: always\nТекст без пустой строки.\n')
        text, ids, broken = self.assemble()
        self.assertEqual((text, ids), ('', ()))
        self.assertEqual([item.split(' ', 1)[0] for item in broken], ['glued', 'plain'])
        self.assertIn('пустая строка', broken[0])

    def test_a_proper_file_is_taken_whole(self) -> None:
        self.criterion('good', 'id: good\napplies: craft\n\n## Критерий «good»: всё\nТекст.\n')
        parts = self.assemble([write_record('blocks add')])
        self.assertIsInstance(parts, review_lib.BriefParts)
        self.assertEqual((parts.text, parts.criteria, parts.broken),
                         ('## Критерий «good»: всё\nТекст.', ('good',), ()))
        check = review_lib.review_criteria(sources(self.modules), [write_record('blocks add')],
                                           None)
        self.assertEqual(check.criteria, ('good',))

    def test_a_repeated_head_field_is_broken(self) -> None:
        self.criterion('twice', 'id: a\nid: b\napplies: always\n\nТекст.\n')
        _, ids, broken = self.assemble()
        self.assertEqual(ids, ())
        self.assertIn('поле «id» в шапке дважды', broken[0])

    def test_a_tag_follows_the_entity_rule(self) -> None:
        # Как теги в entity.json: «#» и хотя бы один знак, регистр не важен.
        self.criterion('bare', 'id: bare\napplies: tags #\n\nТекст.\n')
        self.criterion('upper', 'id: upper\napplies: tags #Задача\n\nТекст.\n')
        _, ids, broken = self.assemble([write_record()])
        self.assertEqual(ids, ('upper',))
        self.assertEqual([item.split(' ', 1)[0] for item in broken], ['bare'])

    def test_an_unreadable_file_is_broken_with_its_path(self) -> None:
        self.criterion('bytes', 'id: x\napplies: always\n\nТекст.\n')
        path = self.modules / 'bytes' / 'data' / 'review-criterion.md'
        path.write_bytes(b'id: x\napplies: always\n\n\xff\n')
        locked = self.modules / 'locked' / 'data' / 'review-criterion.md'
        locked.parent.mkdir(parents=True)
        locked.write_text('id: y\napplies: always\n\nТекст.\n', encoding='utf-8')
        real_read = Path.read_text

        def read_text(target, *args, **kwargs):
            if target == locked:
                raise PermissionError(13, 'нет доступа', str(target))
            return real_read(target, *args, **kwargs)

        with mock.patch.object(Path, 'read_text', read_text):
            _, ids, broken = self.assemble()
        self.assertEqual(ids, ())
        self.assertEqual(len(broken), 2)
        self.assertIn(str(path), broken[0])
        self.assertIn(str(locked), broken[1])

    def test_a_provider_with_nothing_to_give_is_broken(self) -> None:
        # for = ["turn-review"] без критерия и без хуков: модулю сущности,
        # потерявшему критерий, молча пройти нельзя. С хуками он поставщик записей.
        self.criterion('good', 'id: good\napplies: always\n\nТекст.\n')
        (self.modules / 'empty').mkdir()
        (self.modules / 'writer').mkdir()
        providers = (review_lib.Provider('empty', self.modules / 'empty', False),
                     review_lib.Provider('good', self.modules / 'good', False),
                     review_lib.Provider('writer', self.modules / 'writer', True))
        parts = review_lib.assemble_brief(sources(self.modules, providers), [], '{{CRITERIA}}',
                                          DESCRIPTIONS)
        self.assertEqual(parts.criteria, ('good',))
        self.assertEqual(len(parts.broken), 1)
        self.assertTrue(parts.broken[0].startswith('empty ('), parts.broken[0])
        self.assertIsNone(review_lib.providers_problem(sources(self.modules, providers)))
        self.assertIn('нет поставщика', review_lib.providers_problem(
            sources(self.modules, providers[:2])))


class RecordsTextTest(unittest.TestCase):
    """Описания видов записей — свои у ревью; в поручение идут виды из объёма."""

    def assemble(self, volume, descriptions: str = DESCRIPTIONS) -> str:
        with tempfile.TemporaryDirectory() as folder:
            return review_lib.assemble_brief(sources(Path(folder), ()), volume, '{{RECORDS}}',
                                             descriptions).text

    def test_only_kinds_of_the_volume_are_described(self) -> None:
        text = self.assemble([write_record(), skill_record('craft-task')])
        self.assertIn('`prompt_id`', text)
        self.assertIn('`write` — вызов записи', text)
        self.assertIn('`skill` — вызов скилла', text)
        self.assertNotIn('`read` — вызов чтения', text)
        self.assertNotIn('## ', text)

    def test_every_kind_is_described(self) -> None:
        rows = [review_lib.parse_record(row) for row in RecordTest.ROWS]
        text = self.assemble(rows)
        for kind in review_lib.KINDS:
            self.assertIn(f'`{kind}` — ', text)

    def test_a_kind_without_a_description_is_a_brief_error(self) -> None:
        with self.assertRaises(review_lib.BriefError) as caught:
            self.assemble([write_record()], 'Общее.\n\n## read\n\nЧтение.')
        self.assertIn('write', str(caught.exception))
        with self.assertRaises(review_lib.BriefError):
            self.assemble([], '## чужой\n\nТекст.')


class ModuleRequirementsFilesTest(unittest.TestCase):
    """Требования модулей: data/review.md модуля, чей скилл есть в записях skill объёма."""

    FRAME = '{{MODULE_REQUIREMENTS}}'

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.modules = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def requirements(self, slug: str, text: str) -> Path:
        path = self.modules / slug / 'data' / 'review.md'
        path.parent.mkdir(parents=True)
        path.write_text(text, encoding='utf-8')
        return path

    def assemble(self, volume=()):
        return review_lib.assemble_brief(sources(self.modules, ()), list(volume), self.FRAME,
                                         DESCRIPTIONS)

    def test_a_skill_with_a_file_brings_its_text_once(self) -> None:
        self.requirements('craft-snapshot', '1. Отметка прогона записана.\n')
        self.requirements('craft-note', '1. Резюме на месте.\n')
        volume = [skill_record('craft-snapshot'), write_record(),
                  skill_record('craft-snapshot')]
        parts = self.assemble(volume)
        self.assertEqual(parts.text,
                         '### Требования модуля craft-snapshot\n\n1. Отметка прогона записана.')
        self.assertEqual(parts.broken, ())

    def test_without_a_skill_record_the_section_is_none(self) -> None:
        self.requirements('craft-snapshot', '1. Отметка прогона записана.\n')
        for volume in ([], [write_record()],
                       [read_record('craft-snapshot')]):
            with self.subTest(volume=volume):
                self.assertEqual(self.assemble(volume).text, 'нет')

    def test_a_skill_without_a_file_or_with_a_path_is_none(self) -> None:
        (self.modules / 'craft-task' / 'data').mkdir(parents=True)
        self.requirements('outside', 'Чужое.')
        volume = [skill_record(name)
                  for name in ('craft-task', 'verification-loop', '../outside', 'x/../outside',
                               '..', '')]
        parts = self.assemble(volume)
        self.assertEqual((parts.text, parts.broken), ('нет', ()))
        self.assertEqual(review_lib.volume_skills(volume), ('craft-task', 'verification-loop'))

    def test_an_unreadable_file_is_broken_with_its_path(self) -> None:
        path = self.requirements('craft-snapshot', 'Текст.')
        path.write_bytes(b'\xff\xfe')
        volume = [skill_record('craft-snapshot'), write_record()]
        parts = self.assemble(volume)
        self.assertEqual(parts.text, 'нет')
        self.assertEqual(len(parts.broken), 1)
        self.assertTrue(parts.broken[0].startswith('craft-snapshot ('))
        self.assertIn(str(path), parts.broken[0])
        # Тот же файл мешает принять чистый вердикт: проблема у stop и превью.
        check = review_lib.review_criteria(sources(self.modules, ()), volume, ())
        self.assertIn(str(path), check.problem)
        # Без записи skill файл не читается и проблемой не становится.
        self.assertNotIn(str(path), review_lib.review_criteria(
            sources(self.modules, ()), [write_record()], ()).problem or '')


class CriterionTest(unittest.TestCase):
    """Разобранный критерий: один тип, одна фабрика, правило applies разобрано один раз."""

    def test_the_factory_makes_a_frozen_criterion(self) -> None:
        criterion = review_lib.make_criterion('id: task\napplies: tags #задача #Этап\n\nТекст.')
        self.assertEqual(criterion, review_lib.Criterion(
            'task', review_lib.Tags(frozenset({'#задача', '#этап'})), 'Текст.'))
        with self.assertRaises(FrozenInstanceError):
            criterion.id = 'другой'

    def test_rules_apply_to_the_volume(self) -> None:
        write = write_record('blocks add #этап')
        tags = frozenset({'#этап'})
        cases = (('always', [], True), ('craft', [], False), ('craft', [write], True),
                 ('tags #задача', [write], False), ('tags #задача #этап', [write], True))
        for rule, volume, expected in cases:
            criterion = review_lib.make_criterion(f'id: c\napplies: {rule}\n\nТекст.')
            self.assertEqual(review_lib.criterion_applies(criterion, volume, tags), expected,
                             rule)

    def test_tags_hold_their_invariant_without_the_factory(self) -> None:
        for tags in (frozenset(), frozenset({'#Задача'}), frozenset({'задача'}), frozenset({'#'}),
                     frozenset({'#задача', 1}), ['#задача']):
            with self.subTest(tags=tags), self.assertRaises(ValueError):
                review_lib.Tags(tags)

    def test_a_criterion_holds_its_invariant_without_the_factory(self) -> None:
        for criterion_id, rule, body in (('', review_lib.Always(), 'Текст.'),
                                         ('  ', review_lib.Always(), 'Текст.'),
                                         ('task', review_lib.Always(), ''),
                                         ('task', review_lib.Always(), '\n '),
                                         ('task', 'always', 'Текст.')):
            with self.subTest(id=criterion_id, body=body), self.assertRaises(ValueError):
                review_lib.Criterion(criterion_id, rule, body)



class BrokenCriterionTest(TurnReviewCase):
    """Набор с модулем, чей файл критерия не разобрался."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        path = cls.root / 'modules' / 'zz-broken' / 'data' / 'review-criterion.md'
        path.parent.mkdir(parents=True)
        path.write_text('id: zz\napplies: alway\n\nТекст.\n', encoding='utf-8')
        (cls.root / 'modules' / 'zz-broken' / 'module.toml').write_text(
            'slug = "zz-broken"\nfor = ["turn-review"]\n', encoding='utf-8')

    def test_a_broken_criterion_reaches_every_stop_problem(self) -> None:
        session = 's-broken-criterion'
        self.start_with_write(session)
        for _ in range(3):
            self.assertIn('zz-broken', self.blocked(self.fire(stop(session))))
        reason = self.blocked(self.fire(stop(session)))
        self.assertIn(QUESTION, reason)
        self.assertIn('zz-broken', reason)

    def test_a_clean_verdict_is_not_taken_while_a_file_is_broken(self) -> None:
        session = 's-broken-clean'
        self.start_with_write(session)
        self.blocked(self.fire(stop(session)))
        self.write_verdict(session, self.state(session)['volume_id'])
        self.assertEqual(self.shown(self.fire(message(session, 'Готово.'))),
                         'Ревью хода идёт, отчёт после него.')
        reason = self.blocked(self.fire(stop(session)))
        self.assertIn('zz-broken', reason)
        self.assertIn('ревью хода не сделано', reason)
        self.assertEqual(len(self.volume(session)), 1)

    def test_remarks_are_returned_with_the_broken_file_named(self) -> None:
        session = 's-broken-remark'
        self.start_with_write(session)
        self.write_verdict(session, self.state(session)['volume_id'], [remark()])
        reason = self.blocked(self.fire(stop(session)))
        self.assertIn('нет ссылки на этап', reason)
        self.assertIn('zz-broken', reason)


class ModuleRequirementsTest(TurnReviewCase):
    """Набор с модулем craft-snapshot: его data/review.md идёт в поручение за записью skill."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        shutil.copytree(MODULES_DIR / 'craft-snapshot', cls.root / 'modules' / 'craft-snapshot',
                        ignore=shutil.ignore_patterns('__pycache__'))
        cls.requirements = (MODULES_DIR / 'craft-snapshot' / 'data' / 'review.md').read_text(
            encoding='utf-8').strip()

    def brief(self, session: str) -> str:
        return self.storage(session).path('turn-review-brief.md').read_text(encoding='utf-8')

    def test_a_called_skill_brings_its_requirements_into_the_brief(self) -> None:
        session = 's-requirements'
        self.fire(prompt(session))
        self.fire(skill(session, 'craft-snapshot'))
        self.assert_silent(self.fire(write(session)))
        self.blocked(self.fire(stop(session)))
        brief = self.brief(session)
        self.assertIn('## Требования модулей', brief)
        self.assertIn(f'### Требования модуля craft-snapshot\n\n{self.requirements}', brief)
        self.assertNotIn('{{MODULE_REQUIREMENTS}}', brief)

    def test_without_the_skill_the_requirements_stay_out(self) -> None:
        session = 's-no-requirements'
        self.start_with_write(session)
        self.blocked(self.fire(stop(session)))
        brief = self.brief(session)
        self.assertNotIn('Требования модуля craft-snapshot', brief)
        self.assertNotIn(self.requirements, brief)
        self.assertNotIn('{{MODULE_REQUIREMENTS}}', brief)


class ForDiscoveryTest(TurnReviewCase):
    """Ревью ищет файлы только у модулей с for = ["turn-review"]."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        # Модуль сущности потерял критерий: for остался, дать ревью нечего.
        (cls.root / 'modules' / 'craft-task' / 'data' / 'review-criterion.md').unlink()
        # Модуль без for: его критерий лежит на месте, но не читается.
        stray = cls.root / 'modules' / 'zz-stray'
        (stray / 'data').mkdir(parents=True)
        (stray / 'module.toml').write_text('slug = "zz-stray"\n', encoding='utf-8')
        (stray / 'data' / 'review-criterion.md').write_text(
            'id: stray\napplies: always\n\nЧужой критерий.\n', encoding='utf-8')

    def test_a_provider_without_a_criterion_is_named_and_a_stray_file_is_ignored(self) -> None:
        session = 's-for'
        self.start_with_write(session)
        reason = self.blocked(self.fire(stop(session)))
        self.assertIn('craft-task (объявил for = ["turn-review"]', reason)
        self.assertNotIn('stray', self.state(session)['criteria'])
        self.write_verdict(session, self.state(session)['volume_id'])
        self.assertIn('craft-task (объявил', self.blocked(self.fire(stop(session))))
        self.assertEqual(len(self.volume(session)), 1)


class NoProviderTest(TurnReviewCase):
    """Набор, где ни один модуль с хуками не объявил for = ["turn-review"]: объём некому копить."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        for slug in PROVIDERS:
            path = cls.root / 'modules' / slug / 'module.toml'
            text = path.read_text(encoding='utf-8')
            path.write_text(text.replace('for = ["turn-review"]\n', ''), encoding='utf-8')

    def test_a_stop_without_a_provider_is_not_released_as_empty(self) -> None:
        session = 's-no-provider'
        self.fire(prompt(session))
        reason = self.blocked(self.fire(stop(session)))
        self.assertIn('Нет поставщика записей', reason)
        self.assertIn(str(self.root / 'modules'), reason)


class BrokenFrameTest(TurnReviewCase):
    """Набор, где каркас поручения не читается."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        (cls.module_dir / 'data' / 'review-frame.md').write_bytes(b'\xff\xfe')

    def test_an_unreadable_frame_is_a_block_and_not_a_crash(self) -> None:
        session = 's-frame'
        self.start_with_write(session)
        reason = self.blocked(self.fire(stop(session)))
        self.assertIn('каркас поручения', reason)
        self.assertIn('review-frame.md', reason)
        self.assertIn('Скажи об этом человеку', reason)
        state = self.state(session)
        self.assertEqual((state['nudges'], state['criteria']), (1, None))
        self.assertFalse(self.storage(session).path('turn-review-brief.md').exists())


class NoCriteriaTest(TurnReviewCase):
    """Набор, где к записи не подходит ни один критерий."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        for slug in (*CRITERIA_MODULES, 'craft-session-anchor'):
            (cls.root / 'modules' / slug / 'data' / 'review-criterion.md').unlink()

    def test_writes_without_criteria_are_not_released_by_a_clean_verdict(self) -> None:
        session = 's-no-criteria'
        self.start_with_write(session)
        self.assertIn('один критерий не подошёл', self.blocked(self.fire(stop(session))))
        # Поручение было, но без критериев: в состоянии пустой список, не None.
        self.assertEqual(self.state(session)['criteria'], [])
        self.write_verdict(session, self.state(session)['volume_id'], checked=())
        reason = self.blocked(self.fire(stop(session)))
        self.assertIn('один критерий не подошёл', reason)
        self.assertEqual(len(self.volume(session)), 1)

    def test_a_clean_verdict_without_criteria_keeps_the_answer_hidden_in_the_preview(self) -> None:
        # Превью судит как stop: объём с write без критериев чистым вердиктом
        # не открывается, и итог ревью под ответом не показывается.
        self.debug_file.write_text(json.dumps({'debug': True}), encoding='utf-8')
        self.addCleanup(self.debug_file.unlink, missing_ok=True)
        for session, nudged in (('s-no-criteria-peek-nudged', True), ('s-no-criteria-peek', False)):
            self.start_with_write(session)
            if nudged:
                self.blocked(self.fire(stop(session)))
            self.write_verdict(session, self.state(session)['volume_id'], checked=())
            self.assertEqual(self.shown(self.fire(message(session, 'Готово.'))),
                             'Ревью хода идёт, отчёт после него.')
            self.assertIsNone(self.state(session)['shown_verdict'])




class FingerprintScriptTest(unittest.TestCase):
    """Скрипт отпечатка печатает то же, что считает хук."""

    def test_the_script_matches_the_library(self) -> None:
        records = [write_record(), read_record('blocks get', ['a'])]
        with tempfile.TemporaryDirectory() as folder:
            volume = Path(folder) / 'volume.jsonl'
            rows = (json.dumps(review_lib.record_row(r), ensure_ascii=False) for r in records)
            volume.write_text(''.join(row + '\n' for row in rows), encoding='utf-8')
            script = MODULES_DIR / 'turn-review' / 'hooks' / 'fingerprint.py'
            printed = subprocess.run([sys.executable, str(script), str(volume)],
                                     capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(printed, review_lib.fingerprint(records))
        self.assertEqual(len(printed), review_lib.FINGERPRINT_CHARS)
        # Чтения отпечаток не меняют: ревью смотрит на записи.
        self.assertEqual(printed, review_lib.fingerprint(records[:1]))

    def test_the_script_parses_the_file_like_the_hook(self) -> None:
        # U+2028 внутри записи и битая строка: хук режет файл по байтам и
        # пропускает нечитаемое, скрипт обязан считать то же самое.
        records = [write_record(command='blocks add --json {"markdown":"раз\u2028два"}')]
        with tempfile.TemporaryDirectory() as folder:
            volume = Path(folder) / 'volume.jsonl'
            volume.write_bytes(json.dumps(review_lib.record_row(records[0]),
                                          ensure_ascii=False).encode('utf-8')
                               + b'\n{"kind": "write", \xff\n')
            script = MODULES_DIR / 'turn-review' / 'hooks' / 'fingerprint.py'
            run = subprocess.run([sys.executable, str(script), str(volume)],
                                 capture_output=True, text=True, check=True)
        self.assertEqual(run.stdout.strip(), review_lib.fingerprint(records))
        self.assertIn('нечитаемая строка объёма, строка 2', run.stderr)


if __name__ == '__main__':
    unittest.main()
