"""Замок craft-type-lock: разбор нагрузки, находки ревью и строка типа в объёме."""

import json
import sys
import unittest

from jarvis import registry

from . import MODULES_DIR
from .craft_type_lock_harness import (
    BAD_BLOCK,
    BAD_NEXT,
    BAD_PAGE,
    BARE_TASK,
    BULLET_JSON,
    D1,
    D2,
    DAY,
    DAY_LINE,
    LEVEL0_JSON,
    M_STAGE,
    PAGE,
    PARSER,
    PROJECT,
    SECTION,
    STAGE,
    STAGE2,
    TASK,
    TASK2,
    TASK_ADD,
    TASK_JSON,
    TASK_TEMPLATES,
    ApiLockHarness,
    load_lock_module,
    prompt,
    read,
    skill,
    write,
    written,
)


class PayloadTest(unittest.TestCase):
    """Разбор нагрузки: отступ новых блоков и смена состояния."""

    @classmethod
    def setUpClass(cls) -> None:
        load_lock_module()
        cls.payload = sys.modules['payload']
        cls.craft = registry.LibraryModule(PARSER, MODULES_DIR / PARSER / 'lib').load()

    def write_of(self, command: str):
        return self.payload.write_of(self.craft.verb_of(command), self.craft.parse_command(command))

    def test_levels_come_from_the_json_and_markdown_payload(self) -> None:
        cases = ((f'blocks add --siblingId X --json {TASK_JSON}', frozenset({2})),
                 ('blocks add --siblingId X --json \'{"type":"text","markdown":"Строка"}\'', frozenset({0})),
                 ('blocks add --siblingId X --markdown "Строка"', frozenset({0})),
                 ('blocks add --siblingId X --json \'[{"indentationLevel":1},{"indentationLevel":2}]\'',
                  frozenset({1, 2})),
                 ('blocks add --siblingId X --json \'{broken\'', None),
                 ('blocks add --siblingId X --json \'[]\'', None),
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
                'blocks update --id X --markdown "- [ ] Учить --state done #задача/1ч"',
                'blocks update --id X --json \'{"markdown":"- [ ] Новое имя #задача/1ч"}\'',
                'blocks update --id X --json \'{"taskInfo":{"scheduleDate":"2026-11-05"}}\'')
        for command in kept:
            with self.subTest(command=command):
                self.assertFalse(self.payload.changes_state(self.write_of(command), current))

    def test_reopening_is_a_state_change_too(self) -> None:
        write_ = self.write_of('blocks update --id X --json \'{"markdown":"- [ ] ### Этап #этап"}\'')
        self.assertTrue(self.payload.changes_state(write_, '  - [x] ### Этап #этап/готово'))


class ReviewFindingsTest(ApiLockHarness):
    """Обходы замка, найденные ревью: пачка, флаги цели, JSON, состояние, шаблон."""

    def test_a_delete_after_an_add_in_one_batch_still_touches_the_stage(self) -> None:
        self.start('f-add-delete', PROJECT)
        self.prepare('f-add-delete', 'craft-task')
        command = f'blocks add --id {TASK} --position end --json {LEVEL0_JSON}; blocks delete --id {TASK2}'
        self.assert_denied(self.fire(write('f-add-delete', command)), 'не вызван скилл: craft-stage.')

    def test_a_move_after_an_update_in_one_batch_touches_both_stages(self) -> None:
        self.start('f-update-move', PROJECT)
        self.prepare('f-update-move', 'craft-task')
        update = f'blocks update --id {TASK} --json \'{{"markdown":"- [ ] Задача #задача/1ч","indentationLevel":2}}\''
        command = f'{update}; blocks move --id {TASK2} --targetSiblingId {BARE_TASK} --position after'
        result = self.fire(write('f-update-move', command))
        self.assert_denied(result, '«- [ ] ### Второй этап #этап»', '«- [ ] ### Этап #этап»',
                           'не вызван скилл: craft-stage.')

    def test_a_close_after_an_add_in_one_batch_touches_the_stage(self) -> None:
        self.start('f-add-close', PROJECT)
        self.prepare('f-add-close', 'craft-task')
        command = f'blocks add --id {TASK} --position end --json {LEVEL0_JSON}; tasks update --id {TASK2} --state done'
        self.assert_denied(self.fire(write('f-add-close', command)), 'не вызван скилл: craft-stage.')

    def test_a_batch_row_is_undetermined_when_one_command_is(self) -> None:
        self.start('f-row', PROJECT)
        command = (f'tasks update --id {TASK} --scheduleDate 2026-11-05; '
                   f'blocks add --siblingId {TASK} --position after --json \'{{broken\'')
        self.fire(write('f-row', command))
        self.fire(written(write('f-row', command)))
        row = json.loads(self.storage('f-row').read_lines('turn-review-volume.jsonl')[-1])
        self.assertEqual((row['targets'], row['determined']), ([TASK, TASK], False))

    def test_an_add_with_an_empty_json_list_requires_every_candidate_owner(self) -> None:
        self.start('f-empty', PROJECT)
        result = self.fire(write('f-empty', f'blocks add --siblingId {TASK} --position after --json \'[]\''))
        self.assert_denied(result, 'не вызван скилл: craft-project, craft-stage, craft-task.')

    def test_closing_a_task_by_its_task_flag_touches_its_stage(self) -> None:
        self.start('f-task-flag', PROJECT)
        result = self.fire(write('f-task-flag', f'tasks update --task {TASK2} --state done'))
        self.assert_denied(result, 'не вызван скилл: craft-stage, craft-task.')

    def test_an_update_targeted_only_in_its_json_is_judged(self) -> None:
        self.start('f-json-id', PAGE)
        command = f'blocks update --json \'{{"id":"{D1}","markdown":"Правка решения"}}\''
        self.assert_denied(self.fire(write('f-json-id', command)), SECTION, 'craft-section')

    def test_deleting_a_document_needs_its_type(self) -> None:
        self.start('f-document', PAGE)
        result = self.fire(write('f-document', f'documents delete --document {PAGE}'))
        self.assert_denied(result, 'Запись правит сущность «Раздел #раздел»', 'craft-section')
        unread = self.fire(write('f-document', f'documents move --document {PROJECT}'))
        self.assert_denied(unread, 'Запись в непрочитанное')

    def test_a_daily_note_by_date_is_not_a_missing_target(self) -> None:
        self.fire(prompt('f-date'))
        self.assert_silent(self.fire(write('f-date', 'blocks add --date today --markdown "Купить хлеб"')))
        self.assertIn('заметка дня', self.last_reason('f-date'))

    def test_a_broken_state_file_denies_the_write_with_the_way_out(self) -> None:
        self.start('f-state', PAGE)
        path = self.storage('f-state').path('craft-type-lock.json')
        path.write_text('{"turn": "x"}', encoding='utf-8')
        result = self.fire(write('f-state', f'blocks update --id {D1} --json {BULLET_JSON}'))
        self.assert_denied(result, str(path), 'удали', 'повтори запись')

    def test_a_corrupted_indent_of_a_moved_block_denies_the_write(self) -> None:
        self.start('f-indent', BAD_PAGE)
        result = self.fire(write('f-indent', f'blocks move --id {BAD_BLOCK} --targetSiblingId {BAD_NEXT}'))
        self.assert_denied(result, 'отступ не число', 'не смог судить запись')

    def test_a_template_counts_only_when_read_whole(self) -> None:
        for number, read_command in enumerate((f'blocks get {TASK_TEMPLATES[0]}',
                                               f'blocks get {TASK_TEMPLATES[0]} --depth 1',
                                               f'search "x" --document {TASK_TEMPLATES[0]} --depth -1')):
            with self.subTest(read=read_command):
                session = f'f-depth-{number}'
                self.fire(prompt(session))
                self.fire(skill(session, 'craft-task'))
                self.fire(read(session, read_command))
                self.assert_denied(self.fire(write(session, TASK_ADD)), 'не прочитан шаблон #задача')
        self.fire(prompt('f-depth-batch'))
        self.fire(skill('f-depth-batch', 'craft-task'))
        self.fire(read('f-depth-batch', f'blocks get {PAGE} --depth 1; blocks get {TASK_TEMPLATES[1]} --depth -1'))
        self.assert_silent(self.fire(write('f-depth-batch', TASK_ADD)))

    def test_state_words_in_the_text_do_not_close_the_task(self) -> None:
        self.start('f-text-state', PROJECT)
        self.prepare('f-text-state', 'craft-task')
        command = (f'blocks update --id {TASK} --json '
                   '\'{"markdown":"- [ ] Учить --state done #задача/1ч","indentationLevel":2}\'')
        self.assert_silent(self.fire(write('f-text-state', command)))


class ReviewRowTest(ApiLockHarness):
    """Строка `entity` в объёме ревью: тип места записи, без решения замка."""

    ROW_KEYS = {'kind', 'prompt_id', 'command', 'targets', 'place_types', 'content_types',
                'determined', 'note'}

    def rows(self, session: str) -> list[dict]:
        lines = self.storage(session).read_lines('turn-review-volume.jsonl')
        return [json.loads(line) for line in lines]

    def write_through(self, raw: dict):
        """Запись, которую инструмент исполнил: `pre-tool`, затем `post-tool`."""
        result = self.fire(raw)
        self.fire(written(raw))
        return result

    def test_a_denied_write_leaves_no_row_and_a_passed_one_leaves_it_after_the_call(self) -> None:
        self.start('r-same', PAGE)
        command = f'blocks add --siblingId {D2} --position after --json {BULLET_JSON}'
        # Запрет: инструмент не исполнен, `post-tool` нет — строки нет.
        self.assert_denied(self.fire({**write('r-same', command), 'prompt_id': 'p-1'}), 'craft-section')
        self.assertEqual(self.rows('r-same'), [])
        self.prepare('r-same', 'craft-section')
        raw = {**write('r-same', command), 'prompt_id': 'p-1', 'tool_use_id': 'toolu_1'}
        self.assert_silent(self.fire(raw))
        self.assertEqual(self.rows('r-same'), [])
        self.fire(written(raw))
        (passed,) = self.rows('r-same')
        self.assertEqual(set(passed), self.ROW_KEYS)
        self.assertEqual(passed['kind'], 'entity')
        self.assertEqual((passed['prompt_id'], passed['command'], passed['targets'],
                          passed['place_types'], passed['content_types'], passed['determined']),
                         ('p-1', command, [D2], ['#раздел'], [], True))
        # Повтор того же `post-tool` второй строки не даёт.
        self.fire(written(raw))
        self.assertEqual(len(self.rows('r-same')), 1)

    def test_a_row_without_a_call_id_is_found_by_prompt_and_command(self) -> None:
        self.start('r-key', PAGE)
        command = f'blocks update --id {D1} --json {BULLET_JSON}'
        self.fire({**write('r-key', command), 'prompt_id': 'p-2'})
        self.fire(written({**write('r-key', f'blocks update --id {D2} --json {BULLET_JSON}'), 'prompt_id': 'p-2'}))
        self.assertEqual(self.rows('r-key'), [])
        self.fire(written({**write('r-key', command), 'prompt_id': 'p-2'}))
        self.assertEqual([row['command'] for row in self.rows('r-key')], [command])

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
            self.write_through(write('r-split', command))
        rows = [(row['place_types'], row['content_types'], row['determined']) for row in self.rows('r-split')]
        self.assertEqual(rows, [(['#этап'], ['#задача'], True),
                                (['#этап'], ['#задача'], True),
                                ([], ['#задача'], True),
                                (['#этап'], ['#задача'], True),
                                ([], ['#этап'], True)])

    def test_a_move_between_stages_names_both_owners(self) -> None:
        self.start('r-move', PROJECT)
        self.write_through(write('r-move', f'blocks move --id {TASK2} --targetSiblingId {BARE_TASK} --position after'))
        (row,) = self.rows('r-move')
        self.assertIn(STAGE2[:8], row['note'])
        self.assertIn(f'у владельца {STAGE[:8]}', row['note'])

    def test_a_document_without_a_type_is_an_explicit_empty_type(self) -> None:
        self.start('r-none', DAY)
        self.write_through(write('r-none', f'blocks update --id {DAY_LINE} --json {BULLET_JSON}'))
        (row,) = self.rows('r-none')
        self.assertEqual((row['place_types'], row['content_types'], row['determined']), ([], [], True))
        self.assertNotIn('prompt_id', row)

    def test_an_unknown_place_is_an_undetermined_row(self) -> None:
        self.start('r-down', PAGE)
        down = write('r-down', f'blocks update --id {D1} --json {BULLET_JSON}')
        self.with_api_down(down)
        self.fire(written(down))
        self.write_through(write('r-down', f'blocks update --id ЗАДАЧА --json {BULLET_JSON}'))
        self.write_through(write('r-down', f'blocks update --id {TASK} --json {BULLET_JSON}'))
        down, broken, unread = self.rows('r-down')
        self.assertEqual((down['determined'], broken['determined'], unread['determined']),
                         (False, False, False))
        self.assertIn('место цели не определено', down['note'])
        self.assertIn('не найдена среди страниц', unread['note'])

    def test_an_unparsed_level_is_an_undetermined_row_with_every_candidate(self) -> None:
        self.start('r-guess', PROJECT)
        self.write_through(write('r-guess', f'blocks add --siblingId {TASK} --position after --json \'{{broken\''))
        (row,) = self.rows('r-guess')
        self.assertEqual((row['place_types'], row['determined']), (['#задача', '#проект', '#этап'], False))
        self.assertIn('отступ записи не разобран', row['note'])

    def test_a_write_of_a_subagent_leaves_no_row(self) -> None:
        self.start('r-agent', PAGE)
        self.write_through({**write('r-agent', f'blocks update --id {D1} --json {BULLET_JSON}'), 'agent_id': 'a-1'})
        self.assertEqual(self.rows('r-agent'), [])

if __name__ == '__main__':
    unittest.main()
