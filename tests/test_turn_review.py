"""Ревью хода turn-review: путь от сырого события Claude до ответа и следа."""

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jarvis import confirm
from jarvis.storage import Storage
from jarvis.trace import Trace

from . import CORE_SOURCE, MODULES_DIR

# Отпечаток в тестах считается той же функцией, что у хука: lib не зависит от ядра.
LIB_SPEC = importlib.util.spec_from_file_location('turn_review_lib', MODULES_DIR / 'turn-review' / 'lib' / '__init__.py')
review_lib = importlib.util.module_from_spec(LIB_SPEC)
LIB_SPEC.loader.exec_module(review_lib)

SLUG = 'turn-review'
TYPE_LOCK = 'craft-type-lock'
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
    return {'criterion': 'craft', 'where': BLOCK, 'text': text}


def stop(session: str, active: bool = True) -> dict:
    return {'hook_event_name': 'Stop', 'session_id': session, 'cwd': '/work',
            'stop_hook_active': active}


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
        for slug in (SLUG, TYPE_LOCK):
            module_dir = cls.root / 'modules' / slug
            shutil.copytree(MODULES_DIR / slug, module_dir, ignore=ignore)
            shutil.copytree(CORE_SOURCE, module_dir / '_core', ignore=ignore)
        cls.module_dir = cls.root / 'modules' / SLUG
        cls.entry = cls.module_dir / 'hooks' / 'module.py'
        # Отладочный вывод в источнике может быть включён: тесты ревью идут
        # без него, его тесты включают его сами.
        cls.debug_file = cls.module_dir / 'data' / 'debug.json'
        cls.debug_file.unlink(missing_ok=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def command(self, raw: dict) -> list[str]:
        return [sys.executable, str(self.entry), '--harness', 'claude',
                '--event', EVENT_OF[raw['hook_event_name']]]

    def env(self, autonomous: bool = False) -> dict:
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.root)}
        if autonomous:
            env['JARVIS_AUTONOMOUS'] = '1'
        return env

    def fire(self, raw: dict, autonomous: bool = False) -> subprocess.CompletedProcess:
        result = subprocess.run(
            self.command(raw), input=json.dumps(raw, ensure_ascii=False), text=True,
            capture_output=True, env=self.env(autonomous), check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
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
                      checked=('craft', 'consistency', 'anchor')) -> None:
        if fingerprint is None:
            fingerprint = review_lib.fingerprint(self.volume(session))
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
        for needle in ('Agent', 'general-purpose', 'записей write в объёме сейчас 1',
                       str((self.module_dir / 'data' / 'review-brief.md').resolve()),
                       str(storage.path('turn-review-volume.jsonl').resolve()),
                       str(storage.path('turn-review-verdict.json').resolve()),
                       state['volume_id'],
                       str((self.root / 'modules' / TYPE_LOCK / 'data' / 'types.json').resolve())):
            self.assertIn(needle, reason)
        self.assertEqual(state['nudges'], 1)

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
        self.assertIn(f'[craft] {BLOCK}: нет ссылки на этап', reason)
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
            'remarks': [{'criterion': 'craft', 'where': BLOCK, 'text': 'x'}], 'checked': []})
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
        self.assertEqual([row['kind'] for row in rows], ['write', 'read', 'skill', 'anchor'])
        self.assertEqual(rows[0], {'kind': 'write', 'turn': 1, 'tool': CRAFT_WRITE,
                                   'command': TASK_ADD, 'result': {'id': BLOCK, 'ok': True},
                                   'types': ['#задача']})
        self.assertEqual(rows[1]['ids'], [BLOCK, PARENT])
        self.assertEqual(rows[2], {'kind': 'skill', 'turn': 1, 'skill': 'craft-task'})
        self.assertEqual(rows[3], {'kind': 'anchor', 'turn': 1, 'question': 'Какой якорь у сессии?',
                                   'answer': {'answers': {'Какой якорь у сессии?':
                                                          'Задача про ревью'}}})

    def test_the_human_word_goes_to_the_volume(self) -> None:
        session = 's-word'
        self.fire(prompt(session, 'Это не противоречие, оставь как есть'))
        self.fire(ask(session, 'Переносить задачу?', 'Нет'))
        self.fire(prompt(session, '   '))
        rows = self.volume(session, every=True)
        self.assertEqual(rows[0], {'kind': 'prompt', 'turn': 1,
                                   'text': 'Это не противоречие, оставь как есть'})
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
        workers = [subprocess.Popen(self.command(raw), stdin=subprocess.PIPE, text=True,
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
        self.write_verdict(session, self.state(session)['volume_id'], checked=())
        reason = self.blocked(self.fire(stop(session)))
        self.assertIn('критерии не пройдены: craft, consistency, anchor', reason)
        self.write_verdict(session, self.state(session)['volume_id'],
                           checked=('craft', 'consistency', 'anchor', 'код'))
        self.assertIn('критерии не из craft, consistency, anchor: код',
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
