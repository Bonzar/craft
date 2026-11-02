"""Ревью хода turn-review: путь от сырого события Claude до ответа и следа."""

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest import mock

from jarvis import confirm, registry
from jarvis.storage import Storage
from jarvis.trace import Trace

from . import CORE_SOURCE, MODULES_DIR

# lib грузится пакетом через реестр ядра, как у хуков: отпечаток и проверки в
# тестах — те же функции. Загрузку по пути без ядра проверяет скрипт отпечатка.
review_lib = registry.LibraryModule('turn-review', MODULES_DIR / 'turn-review' / 'lib').load()

SLUG = 'turn-review'
TYPE_LOCK = 'craft-type-lock'
# Поставщики, якорь и модули с критериями: без них объём пуст, а поручение
# без критериев. В тестах набор как у настоящей установки.
PROVIDERS = ('review-craft-calls', 'review-prompts', 'craft-session-anchor')
CRITERIA_MODULES = ('craft-task', 'craft-stage', 'craft-section', 'craft-note', 'craft-call',
                    'craft-decision')
CRAFT_WRITE = 'mcp__Craft__craft_write'
CRAFT_READ = 'mcp__Craft__craft_read'
BLOCK = 'a6784801-9d92-875c-f146-50159368745b'
PARENT = '9a9b271a-3656-53e3-b933-72500de42db3'
TASK_ADD = ('blocks add --siblingId X --position after --json \'{"type":"text","markdown":'
            '"- [ ] Купить молоко #задача/5м [Сборка #этап](block://AB-1)"}\'')
QUESTION = 'Ревью хода не состоялось после трёх поручений. Провести ревью или закончить без него?'

EVENT_OF = {'UserPromptSubmit': 'prompt', 'PostToolUse': 'post-tool', 'Stop': 'stop',
            'SessionStart': 'after-compact', 'MessageDisplay': 'model-message'}


def prompt(session: str, text: str = 'дальше') -> dict:
    return {'hook_event_name': 'UserPromptSubmit', 'session_id': session, 'cwd': '/work',
            'prompt': text}


def tool(session: str, name: str, tool_input: dict, response) -> dict:
    return {'hook_event_name': 'PostToolUse', 'session_id': session, 'cwd': '/work',
            'tool_name': name, 'tool_input': tool_input, 'tool_response': response}


def write(session: str, command: str = TASK_ADD) -> dict:
    return tool(session, CRAFT_WRITE, {'command': command}, {'id': BLOCK, 'ok': True})


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
        for slug in (SLUG, TYPE_LOCK, *PROVIDERS, *CRITERIA_MODULES):
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
        """Событие уходит всем модулям набора на нём; возвращается ответ ревью."""
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
        # Реплики prompt лежат в объёме рядом с записями хода и без every
        # убираются из выборки; ответы answer остаются.
        lines = self.storage(session).read_lines('turn-review-volume.jsonl')
        rows = [json.loads(line) for line in lines]
        return rows if every else [row for row in rows if row['kind'] != 'prompt']

    def last_reason(self, session: str) -> str:
        return Trace(self.storage(session)).read()[-1]['reason']

    def write_verdict(self, session: str, volume_id: str, remarks=(), fingerprint: str | None = None,
                      checked=None) -> None:
        if fingerprint is None:
            fingerprint = review_lib.fingerprint(self.volume(session))
        if checked is None:
            # Критерии последнего поручения; поручения не было — как у записи задачи.
            checked = self.state(session).get('criteria') or ['craft-base', 'consistency', 'anchor', 'task']
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
                       state['volume_id'], 'критерии craft-base, consistency, anchor, task'):
            self.assertIn(needle, reason)
        self.assertEqual(state['nudges'], 1)
        self.assertEqual(state['criteria'], ['craft-base', 'consistency', 'anchor', 'task'])
        # Поручение собрано из каркаса, записей поставщиков и критериев модулей.
        brief = storage.path('turn-review-brief.md').read_text(encoding='utf-8')
        for needle in ('Записи поставщика Craft', 'Записи поставщика реплик', 'Записи поставщика якоря',
                       'Критерий «task»', 'Критерий «craft-base»', 'Критерий «consistency»',
                       'Критерий «anchor»', '"craft-base", "consistency", "anchor", "task"'):
            self.assertIn(needle, brief)
        for absent in ('Критерий «stage»', 'Критерий «section»', 'Критерий «note»', '{{CRITERIA'):
            self.assertNotIn(absent, brief)

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
        # Вопрос о якоре записан дважды: как сырой ответ поставщиком реплик и как якорь.
        self.assertEqual([row['kind'] for row in rows], ['write', 'read', 'skill', 'answer', 'anchor'])
        self.assertEqual(rows[0], {'kind': 'write', 'turn': 1, 'tool': CRAFT_WRITE,
                                   'command': TASK_ADD, 'result': {'id': BLOCK, 'ok': True},
                                   'types': ['#задача']})
        self.assertEqual(rows[1]['ids'], [BLOCK, PARENT])
        self.assertEqual(rows[2], {'kind': 'skill', 'turn': 1, 'skill': 'craft-task'})
        self.assertEqual(rows[4], {'kind': 'anchor', 'turn': 1, 'question': 'Какой якорь у сессии?',
                                   'answer': {'answers': {'Какой якорь у сессии?':
                                                          'Задача про ревью'}}})

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
        # Счёт ходов продолжается от записей объёма: разговор снова отпускается.
        self.fire(prompt(session, 'А что это?'))
        self.assertEqual(self.state(session)['turn'], 2)
        self.assert_silent(self.fire(stop(session)))

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
        self.assertEqual((rows[0]['kind'], rows[0]['turn'], rows[0]['text'], rows[0]['author']),
                         ('prompt', 1, 'Это не противоречие, оставь как есть', 'Влад'))
        self.assertRegex(rows[0]['at'], r'^\d{4}-\d{2}-\d{2}T')
        self.assertEqual((rows[1]['kind'], rows[1]['turn'], rows[1]['question']),
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
        self.assertEqual(self.volume('s-anchor'),
                         [{'kind': 'anchor', 'turn': 2,
                           'answer': 'Беру задачу «Собрать ревью хода»'}])

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

    def test_a_free_answer_to_the_question_changes_nothing_and_stays_out_of_the_volume(self) -> None:
        self.start_with_write('s-free')
        self.nudge_three_times('s-free')
        self.fire(stop('s-free'))
        before = self.state('s-free')
        volume = self.volume('s-free', every=True)
        self.fire(ask('s-free', QUESTION, 'Подожди, сам посмотрю'))
        self.assertEqual(self.state('s-free'), before)
        self.assertEqual(self.volume('s-free', every=True), volume)
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
        for needle in (str(path), 'строка 3', 'их ревью не было', 'ревью хода не сделано'):
            self.assertIn(needle, reason)
        self.assertNotEqual(self.state(session)['volume_id'], volume_id)
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
        self.assertIn('критерии не пройдены: craft-base, consistency, anchor, task', reason)
        self.write_verdict(session, self.state(session)['volume_id'],
                           checked=('craft-base', 'consistency', 'anchor', 'task', 'код'))
        self.assertIn('критерии не из craft-base, consistency, anchor, task: код',
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
        self.assertEqual([row['turn'] for row in self.volume('s-no-anchor')], [2])

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

    def test_prompt_hooks_give_one_turn_in_any_order(self) -> None:
        # Хуки одного события харнес запускает параллельно: номер хода у
        # записей реплики не зависит от того, кто из них пришёл первым.
        entries = self.hooks['prompt']
        self.assertEqual(entries[0], self.entry)
        for session, order in (('s-order-review', entries), ('s-order-providers', entries[::-1])):
            for text in ('Беру задачу «Ревью»', 'Беру задачу «Ревью»', 'дальше'):
                for entry in order:
                    run = self.run_entry(prompt(session, text), entry)
                    self.assertEqual(run.returncode, 0, run.stderr)
            rows = self.volume(session, every=True)
            self.assertEqual([(row['kind'], row['turn']) for row in rows],
                             [('prompt', 1), ('anchor', 1), ('prompt', 2), ('anchor', 2),
                              ('prompt', 3)] if order is entries else
                             [('anchor', 1), ('prompt', 1), ('anchor', 2), ('prompt', 2),
                              ('prompt', 3)])
            self.assertEqual(self.state(session)['turn'], 3)

    def test_a_damaged_state_is_an_error_for_providers_and_the_write_is_kept(self) -> None:
        session = 's-provider-damage'
        self.start_with_write(session)
        self.storage(session).path('turn-review-state.json').write_text('{не json', encoding='utf-8')
        provider = self.root / 'modules' / 'review-craft-calls' / 'hooks' / 'module.py'
        run = self.run_entry(write(session), provider)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('turn-review-state.json', run.stderr)
        # Запись не потеряна: без номера хода она не раньше ни одного хода и идёт на ревью.
        rows = self.volume(session)
        self.assertEqual([row['kind'] for row in rows], ['write', 'write'])
        self.assertNotIn('turn', rows[1])
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
        self.write_verdict(session, self.state(session)['volume_id'],
                           checked=('craft-base', 'consistency', 'anchor', 'task'))
        self.assertIn('один критерий не подошёл', self.blocked(self.fire(stop(session))))
        self.assertEqual(len(self.volume(session)), 1)

    def test_a_write_with_unknown_tags_is_not_released_by_a_clean_verdict(self) -> None:
        # Поставщик не собрал справочник тегов: критерии по тегам эту запись
        # не выбирают, и чистый вердикт прошёл бы без критериев её типа.
        session = 's-unknown-tags'
        self.start_with_write(session)
        review_lib.append_turn_record(self.storage(session), review_lib.make_write_record(
            tool=CRAFT_WRITE, command='blocks add', result=None, types=None,
            types_error='entity.json: файл сущности не читается'))
        self.write_verdict(session, self.state(session)['volume_id'])
        self.assertEqual(self.shown(self.fire(message(session, 'Готово.'))),
                         'Ревью хода идёт, отчёт после него.')
        reason = self.blocked(self.fire(stop(session)))
        self.assertIn('Теги записи неизвестны, проверить нечем', reason)
        self.assertIn('entity.json: файл сущности не читается', reason)
        self.assertEqual(len(self.volume(session)), 2)

    def test_an_unwritable_brief_is_a_block_and_not_a_crash(self) -> None:
        session = 's-brief-dir'
        self.start_with_write(session)
        self.storage(session).path('turn-review-brief.md').mkdir(parents=True)
        reason = self.blocked(self.fire(stop(session)))
        self.assertIn('turn-review-brief.md не записать', reason)
        self.assertIn('Скажи об этом человеку', reason)
        self.assertEqual(self.state(session)['nudges'], 1)


class CurrentTurnTest(unittest.TestCase):
    """Номер хода из состояния: отсутствие файла законно, порча — ошибка."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name), 's-turn')

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_a_missing_state_is_turn_zero(self) -> None:
        self.assertEqual(review_lib.current_turn(self.storage), 0)

    def test_a_damaged_state_is_an_error(self) -> None:
        path = self.storage.path('turn-review-state.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        for body in ('{не json', '[1, 2]', '{"turn": "1"}', ''):
            path.write_text(body, encoding='utf-8')
            with self.assertRaises(ValueError) as caught:
                review_lib.current_turn(self.storage)
            self.assertIn(str(path), str(caught.exception))

    def rows(self) -> list[dict]:
        return [json.loads(line) for line in self.storage.read_lines('turn-review-volume.jsonl')]

    def test_a_provider_cannot_override_the_turn(self) -> None:
        review_lib.save_state(self.storage, review_lib.ReviewState(turn=4))
        review_lib.append_turn_record(self.storage, review_lib.SkillRecord(skill='x', turn=1))
        self.assertEqual(self.rows(), [{'kind': 'skill', 'turn': 4, 'skill': 'x'}])

    def test_a_provider_turn_is_dropped_when_the_state_is_damaged(self) -> None:
        # Без номера запись идёт на ревью; номер поставщика выдал бы её за прошлый ход.
        path = self.storage.path('turn-review-state.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{не json', encoding='utf-8')
        with self.assertRaises(ValueError):
            review_lib.append_turn_record(self.storage, review_lib.SkillRecord(skill='x', turn=1))
        self.assertEqual(self.rows(), [{'kind': 'skill', 'skill': 'x'}])

    def test_a_given_turn_is_taken_without_the_state(self) -> None:
        # Реплика берёт номер у claim_prompt_turn и кладёт запись с ним.
        record = review_lib.make_prompt_record(text='дальше', author='Влад', at='t')
        review_lib.append_turn_record(self.storage, record, turn=7)
        self.assertEqual(self.rows(), [{'kind': 'prompt', 'turn': 7, 'text': 'дальше',
                                        'author': 'Влад', 'at': 't'}])
        self.assertFalse(self.storage.path('turn-review-state.json').exists())


class RecordTest(unittest.TestCase):
    """Запись объёма: размеченное объединение по kind, строится фабриками lib."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name), 's-record')

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_a_record_without_a_known_kind_is_refused_at_the_border(self) -> None:
        # Раньше такая запись ложилась с kind: null, и её не видел ни один потребитель.
        for record in ({'command': 'x'}, {'kind': None, 'command': 'x'},
                       {'kind': 'write', 'command': 'x'}, 'write'):
            with self.subTest(record=record), self.assertRaises(ValueError) as caught:
                review_lib.append_turn_record(self.storage, record)
            self.assertIn('kind', str(caught.exception))
        self.assertFalse(self.storage.path('turn-review-volume.jsonl').exists())

    def test_every_kind_has_a_factory_with_its_fields(self) -> None:
        cases = (
            (review_lib.make_write_record(tool='t', command='c', result={'id': 1}, types=['#задача']),
             {'kind': 'write', 'tool': 't', 'command': 'c', 'result': {'id': 1},
              'types': ['#задача']}),
            (review_lib.make_write_record(tool='t', command='c', result=None, types=None,
                                          types_error='нет справочника'),
             {'kind': 'write', 'tool': 't', 'command': 'c', 'result': None, 'types': None,
              'types_error': 'нет справочника'}),
            (review_lib.make_read_record(command='c', ids=['a']),
             {'kind': 'read', 'command': 'c', 'ids': ['a']}),
            (review_lib.make_skill_record(skill='craft-task'), {'kind': 'skill', 'skill': 'craft-task'}),
            (review_lib.make_prompt_record(text='т', author='Влад', at='t'),
             {'kind': 'prompt', 'text': 'т', 'author': 'Влад', 'at': 't'}),
            (review_lib.make_answer_record(question='в', answer='о', author='Влад', at='t'),
             {'kind': 'answer', 'question': 'в', 'answer': 'о', 'author': 'Влад', 'at': 't'}),
            (review_lib.make_anchor_record(answer='Беру задачу'),
             {'kind': 'anchor', 'answer': 'Беру задачу'}),
            (review_lib.make_anchor_record(answer={'a': 1}, question='Якорь?'),
             {'kind': 'anchor', 'answer': {'a': 1}, 'question': 'Якорь?'}),
        )
        self.assertEqual({record.kind for record, _ in cases}, set(review_lib.KINDS))
        for record, row in cases:
            with self.subTest(kind=record.kind):
                self.assertEqual(review_lib.record_row(record), row)

    def test_a_wrong_field_is_refused(self) -> None:
        for make in (lambda: review_lib.make_read_record(command=1, ids=[]),
                     lambda: review_lib.make_read_record(command='c', ids='a'),
                     lambda: review_lib.make_write_record(tool='t', command='c', result=None,
                                                          types='#задача'),
                     lambda: review_lib.make_write_record(tool='t', command='c', result=None,
                                                          types=None),
                     lambda: review_lib.make_write_record(tool='t', command='c', result=None,
                                                          types=[], types_error='лишняя'),
                     lambda: review_lib.make_answer_record(question='в', answer=1, author='В',
                                                           at='t'),
                     lambda: review_lib.SkillRecord(skill='x', turn='1')):
            with self.assertRaises(ValueError):
                make()

    def test_factories_take_no_turn(self) -> None:
        with self.assertRaises(TypeError):
            review_lib.make_skill_record(skill='x', turn=1)


class VolumeLockTest(unittest.TestCase):
    """Запись в объём идёт под локом ревью, как очистка на остановке."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name), 's-lock')

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def appends(self):
        """Номер хода из состояния и номер, данный репликой: оба пути под локом."""
        return (('из состояния', review_lib.append_turn_record),
                ('номер дан', lambda storage, record: review_lib.append_turn_record(
                    storage, record, turn=1)))

    def commands(self) -> list[str]:
        records, damage = review_lib.parse_volume_file(self.storage.path('turn-review-volume.jsonl'))
        self.assertEqual(damage, [])
        return sorted(record['command'] for record in records)

    def test_parallel_records_land_as_whole_lines(self) -> None:
        # Строка пишется в два приёма с паузой посередине: без лока второй
        # поток вклинился бы в первую строку.
        def append_in_halves(storage, name, line, zone='session'):
            path = storage.path(name, zone)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a', encoding='utf-8') as handle:
                handle.write(line[:len(line) // 2])
                handle.flush()
                time.sleep(0.1)
                handle.write(line[len(line) // 2:] + '\n')
            return path

        records = [review_lib.make_read_record(command=f'команда {number}', ids=[])
                   for number in range(2)]
        for name, append in self.appends():
            with self.subTest(append=name):
                self.storage.path('turn-review-volume.jsonl').unlink(missing_ok=True)
                workers = [threading.Thread(target=append, args=(self.storage, record))
                           for record in records]
                with mock.patch.object(Storage, 'append_line', append_in_halves):
                    for worker in workers:
                        worker.start()
                    for worker in workers:
                        worker.join(5)
                self.assertEqual(self.commands(), ['команда 0', 'команда 1'])

    def test_a_record_waits_for_the_lock_held_by_the_stop(self) -> None:
        # Остановка чистит объём под локом: запись, легшая посреди очистки,
        # пропала бы вместе со старыми.
        for name, append in self.appends():
            with self.subTest(append=name):
                self.storage.path('turn-review-volume.jsonl').unlink(missing_ok=True)
                worker = threading.Thread(target=append, args=(
                    self.storage, review_lib.make_read_record(command='после очистки', ids=[])))
                with review_lib.state_lock(self.storage):
                    worker.start()
                    worker.join(0.2)
                    self.assertTrue(worker.is_alive())
                    self.assertEqual(self.commands(), [])
                worker.join(5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(self.commands(), ['после очистки'])


class UnknownTagsTest(unittest.TestCase):
    """Запись write с неизвестными тегами: чистый вердикт по ней не принимается."""

    TASK = review_lib.make_criterion('id: task\napplies: tags #задача\n\nТекст.')

    def problem(self, record: dict) -> str | None:
        return review_lib.criteria_problem([record], ('task',), (self.TASK,), ())

    def test_a_types_error_is_a_problem_with_its_text(self) -> None:
        for types in (None, ['#задача']):
            with self.subTest(types=types):
                problem = self.problem({'kind': 'write', 'types': types,
                                        'types_error': '/m/entity.json: не JSON'})
                self.assertIn('теги записи неизвестны, проверить нечем', problem)
                self.assertIn('/m/entity.json: не JSON', problem)

    def test_types_not_a_list_is_a_problem(self) -> None:
        for record in ({'kind': 'write'}, {'kind': 'write', 'types': '#задача'}):
            with self.subTest(record=record):
                self.assertIn('теги записи неизвестны', self.problem(record))

    def test_known_tags_and_other_kinds_are_no_problem(self) -> None:
        self.assertIsNone(self.problem({'kind': 'write', 'types': ['#задача']}))
        self.assertIsNone(self.problem({'kind': 'read', 'types': None}))


class TakenCriteriaTest(unittest.TestCase):
    """Критерии поручения сверяются с найденными: пропавший id называется."""

    TASK = review_lib.make_criterion('id: task\napplies: tags #задача\n\nТекст.')
    def test_a_taken_id_without_a_found_criterion_is_a_problem(self) -> None:
        write = {'kind': 'write', 'types': ['#задача']}
        problem = review_lib.criteria_problem([write], ['task', 'stage'], [self.TASK], [])
        self.assertIn('критерий «stage» из поручения', problem)
        self.assertNotIn('«task»', problem)

    def test_taken_ids_all_found_are_no_problem(self) -> None:
        write = {'kind': 'write', 'types': ['#задача']}
        self.assertIsNone(review_lib.criteria_problem([write], ['task'], [self.TASK], []))



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
                                       prompt_taken=('turn-review',))
        self.storage.write_json('turn-review-state.json', {**state.to_json(), 'future': 1})
        loaded, problem = review_lib.load_state(self.storage)
        self.assertIsNone(problem)
        self.assertEqual(replace(loaded, unknown={}), state)
        review_lib.save_state(self.storage, loaded)
        self.assertEqual(self.disk(), {**state.to_json(), 'future': 1})

    def test_load_fills_a_missing_field_with_its_default(self) -> None:
        data = review_lib.ReviewState(turn=3, volume_id='v').to_json()
        del data['prompt_taken']
        self.storage.write_json('turn-review-state.json', data)
        loaded, problem = review_lib.load_state(self.storage)
        self.assertIsNone(problem)
        self.assertEqual(loaded.prompt_taken, ())

    def test_wrong_items_are_damage_and_not_a_crash_later(self) -> None:
        for field_name, value in (('criteria', [1]), ('prompt_taken', [None]),
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
                       {'criteria': ('task', 1)}, {'prompt_taken': ['m']}, {'volume_id': ''},
                       {'last_remarks': ('нет ссылки',)}, {'summary_shown': None}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(state, **change)

    def test_json_round_trip_keeps_the_state(self) -> None:
        state = review_lib.ReviewState(turn=3, nudges=1, criteria=('task',),
                                       last_remarks=({'criterion': 'task'},),
                                       prompt_taken=('a', 'b'), summary='итог')
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
        return review_lib.assemble_brief(self.modules, list(volume), '{{CRITERIA}}')

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
        parts = self.assemble([{'kind': 'write'}])
        self.assertIsInstance(parts, review_lib.BriefParts)
        self.assertEqual((parts.text, parts.criteria, parts.broken),
                         ('## Критерий «good»: всё\nТекст.', ('good',), ()))
        check = review_lib.review_criteria(self.modules, [{'kind': 'write'}], None)
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
        _, ids, broken = self.assemble([{'kind': 'write', 'types': ['#задача']}])
        self.assertEqual(ids, ('upper',))
        self.assertEqual([item.split(' ', 1)[0] for item in broken], ['bare'])

    def test_an_unreadable_file_is_broken_with_its_path(self) -> None:
        self.criterion('bytes', 'id: x\napplies: always\n\nТекст.\n')
        path = self.modules / 'bytes' / 'data' / 'review-criterion.md'
        path.write_bytes(b'id: x\napplies: always\n\n\xff\n')
        records = self.modules / 'locked' / 'data' / 'review-records.md'
        records.parent.mkdir(parents=True)
        records.write_text('Записи.', encoding='utf-8')
        real_read = Path.read_text

        def read_text(target, *args, **kwargs):
            if target == records:
                raise PermissionError(13, 'нет доступа', str(target))
            return real_read(target, *args, **kwargs)

        with mock.patch.object(Path, 'read_text', read_text):
            _, ids, broken = self.assemble()
        self.assertEqual(ids, ())
        self.assertEqual(len(broken), 2)
        self.assertIn(str(path), broken[0])
        self.assertIn(str(records), broken[1])


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
        return review_lib.assemble_brief(self.modules, list(volume), self.FRAME)

    def test_a_skill_with_a_file_brings_its_text_once(self) -> None:
        self.requirements('craft-snapshot', '1. Отметка прогона записана.\n')
        self.requirements('craft-note', '1. Резюме на месте.\n')
        volume = [{'kind': 'skill', 'skill': 'craft-snapshot'}, {'kind': 'write'},
                  {'kind': 'skill', 'skill': 'craft-snapshot'}]
        parts = self.assemble(volume)
        self.assertEqual(parts.text,
                         '### Требования модуля craft-snapshot\n\n1. Отметка прогона записана.')
        self.assertEqual(parts.broken, ())

    def test_without_a_skill_record_the_section_is_none(self) -> None:
        self.requirements('craft-snapshot', '1. Отметка прогона записана.\n')
        for volume in ([], [{'kind': 'write'}], [{'kind': 'read', 'skill': 'craft-snapshot'}]):
            with self.subTest(volume=volume):
                self.assertEqual(self.assemble(volume).text, 'нет')

    def test_a_skill_without_a_file_or_with_a_path_is_none(self) -> None:
        (self.modules / 'craft-task' / 'data').mkdir(parents=True)
        self.requirements('outside', 'Чужое.')
        volume = [{'kind': 'skill', 'skill': name}
                  for name in ('craft-task', 'verification-loop', '../outside', 'x/../outside',
                               '..', '', 7)]
        parts = self.assemble(volume)
        self.assertEqual((parts.text, parts.broken), ('нет', ()))
        self.assertEqual(review_lib.volume_skills(volume), ('craft-task', 'verification-loop'))

    def test_an_unreadable_file_is_broken_with_its_path(self) -> None:
        path = self.requirements('craft-snapshot', 'Текст.')
        path.write_bytes(b'\xff\xfe')
        volume = [{'kind': 'skill', 'skill': 'craft-snapshot'}, {'kind': 'write'}]
        parts = self.assemble(volume)
        self.assertEqual(parts.text, 'нет')
        self.assertEqual(len(parts.broken), 1)
        self.assertTrue(parts.broken[0].startswith('craft-snapshot ('))
        self.assertIn(str(path), parts.broken[0])
        # Тот же файл мешает принять чистый вердикт: проблема у stop и превью.
        check = review_lib.review_criteria(self.modules, volume, ())
        self.assertIn(str(path), check.problem)
        # Без записи skill файл не читается и проблемой не становится.
        self.assertNotIn(str(path), review_lib.review_criteria(
            self.modules, [{'kind': 'write'}], ()).problem or '')


class CriterionTest(unittest.TestCase):
    """Разобранный критерий: один тип, одна фабрика, правило applies разобрано один раз."""

    def test_the_factory_makes_a_frozen_criterion(self) -> None:
        criterion = review_lib.make_criterion('id: task\napplies: tags #задача #Этап\n\nТекст.')
        self.assertEqual(criterion, review_lib.Criterion(
            'task', review_lib.Tags(frozenset({'#задача', '#этап'})), 'Текст.'))
        with self.assertRaises(FrozenInstanceError):
            criterion.id = 'другой'

    def test_rules_apply_to_the_volume(self) -> None:
        write = {'kind': 'write', 'types': ['#этап']}
        cases = (('always', [], True), ('craft', [], False), ('craft', [write], True),
                 ('tags #задача', [write], False), ('tags #задача #этап', [write], True))
        for rule, volume, expected in cases:
            criterion = review_lib.make_criterion(f'id: c\napplies: {rule}\n\nТекст.')
            self.assertEqual(review_lib.criterion_applies(criterion, volume), expected, rule)

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


class UntaggedTest(TurnReviewCase):
    """Набор, где у модуля сущности нет критерия на свой тег."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        (cls.root / 'modules' / 'craft-task' / 'data' / 'review-criterion.md').unlink()

    def test_a_tag_without_a_criterion_is_not_released_by_a_clean_verdict(self) -> None:
        # Общие критерии always и craft подходят к любой записи, но тег задачи
        # они не проверяют: без этой проверки запись прошла бы молча.
        session = 's-untagged'
        self.start_with_write(session)
        self.assertIn('Нет критерия на тег #задача', self.blocked(self.fire(stop(session))))
        self.write_verdict(session, self.state(session)['volume_id'])
        reason = self.blocked(self.fire(stop(session)))
        self.assertIn('Нет критерия на тег #задача', reason)
        self.assertEqual(len(self.volume(session)), 1)


class NoProviderTest(TurnReviewCase):
    """Набор без единого описания записей: объём некому копить."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        for slug in PROVIDERS:
            (cls.root / 'modules' / slug / 'data' / 'review-records.md').unlink()

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
        records = [{'kind': 'write', 'command': 'blocks add', 'types': ['задача']},
                   {'kind': 'read', 'command': 'blocks get', 'ids': ['a']}]
        with tempfile.TemporaryDirectory() as folder:
            volume = Path(folder) / 'volume.jsonl'
            volume.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in records) + '\n',
                              encoding='utf-8')
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
        records = [{'kind': 'write', 'command': 'blocks add --json {"markdown":"раз\u2028два"}'}]
        with tempfile.TemporaryDirectory() as folder:
            volume = Path(folder) / 'volume.jsonl'
            volume.write_bytes(json.dumps(records[0], ensure_ascii=False).encode('utf-8')
                               + b'\n{"kind": "write", \xff\n')
            script = MODULES_DIR / 'turn-review' / 'hooks' / 'fingerprint.py'
            run = subprocess.run([sys.executable, str(script), str(volume)],
                                 capture_output=True, text=True, check=True)
        self.assertEqual(run.stdout.strip(), review_lib.fingerprint(records))
        self.assertIn('нечитаемая строка объёма, строка 2', run.stderr)


if __name__ == '__main__':
    unittest.main()
