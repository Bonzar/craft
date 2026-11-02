"""Замок craft-type-lock: испорченные файлы замка и устаревшее дерево прочитанного."""

import json
import time
import unittest

from .craft_type_lock_harness import (
    BLOCKS,
    BULLET_JSON,
    D2,
    DIARY,
    DIARY_LINE,
    DIARY_TASK,
    GHOST,
    GHOST_PAGE,
    LEVEL0_JSON,
    PAGE,
    ApiLockHarness,
    FakeCraftApi,
    prompt,
    write,
    written,
)

ROWS_FILE = 'craft-type-lock-rows.json'
STATE_FILE = 'craft-type-lock.json'
TREE_FILE = 'craft-type-lock-tree.json'
TAGGED_ADD = f'blocks add --siblingId {D2} --position after --json {BULLET_JSON}'


def stale_read(session: str) -> dict:
    """JSON-чтение заметки до того, как в ней появилась задача: в ответе только первая строка."""
    diary = BLOCKS[DIARY][0]
    answer = {**diary, 'content': [child for child in diary['content'] if child['id'] == DIARY_LINE]}
    return {'hook_event_name': 'PostToolUse', 'session_id': session, 'cwd': '/work',
            'tool_name': 'mcp__Craft__craft_read',
            'tool_input': {'command': f'blocks get {DIARY} --depth -1 --format json'},
            'tool_response': [{'type': 'text', 'text': json.dumps({'data': [answer]}, ensure_ascii=False)}]}


class BrokenLockFilesTest(ApiLockHarness):
    """Испорченный файл замка — запрет с путём именно этого файла, а не пропуск упавшего хука."""

    def test_a_broken_pending_rows_file_denies_the_write_with_its_path(self) -> None:
        for number, body in enumerate(('{"x":', '[1]', '{"k": 5}')):
            session = f'b-rows-{number}'
            with self.subTest(body=body):
                self.start(session, PAGE)
                path = self.storage(session).path(ROWS_FILE)
                path.write_text(body, encoding='utf-8')
                result = self.fire(write(session, TAGGED_ADD))
                self.assert_denied(result, str(path), 'не смог судить запись')
                self.assertNotIn(STATE_FILE, self.reason(result))

    def test_a_broken_entry_of_the_tree_file_denies_the_write_with_its_path(self) -> None:
        now = time.time()
        good_page = {'id': PAGE, 'block': None, 'children': None, 'complete': False, 'at': now}
        bodies = {
            'связь без строки родителя': {'pages': {}, 'links': {D2.lower(): {'parent': 5, 'at': now}},
                                          'roots': {}},
            'связь без времени': {'pages': {}, 'links': {D2.lower(): {'parent': PAGE}}, 'roots': {}},
            'связь не объект': {'pages': {}, 'links': {D2.lower(): PAGE}, 'roots': {}},
            'корень не число': {'pages': {}, 'links': {}, 'roots': {PAGE.lower(): 'вчера'}},
            'ребёнок без строки id': {'pages': {PAGE.lower(): {**good_page, 'children': [{'id': 5}],
                                                               'complete': True}},
                                      'links': {}, 'roots': {}},
            'ребёнок не объект': {'pages': {PAGE.lower(): {**good_page, 'children': [D2]}},
                                  'links': {}, 'roots': {}},
            'блок страницы не объект': {'pages': {PAGE.lower(): {**good_page, 'block': 'текст'}},
                                        'links': {}, 'roots': {}},
        }
        for number, (name, body) in enumerate(bodies.items()):
            session = f'b-tree-{number}'
            with self.subTest(name):
                self.start(session, PAGE)
                path = self.storage(session).path(TREE_FILE)
                path.write_text(json.dumps(body), encoding='utf-8')
                result = self.fire(write(session, TAGGED_ADD))
                self.assert_denied(result, str(path), 'не смог судить запись')
                self.assertNotIn(STATE_FILE, self.reason(result))


class StaleTreeTest(ApiLockHarness):
    """Дерево прочитанного не выдаёт устаревший список детей за полный."""

    def begin(self, session: str) -> None:
        self.fire(prompt(session))
        self.assert_silent(self.fire(stale_read(session)))
        self.prepare(session, 'craft-note')

    def test_an_anchor_missing_from_a_complete_cached_list_is_asked_from_the_api(self) -> None:
        # Задачу в заметку положил человек после чтения: в дереве её нет.
        self.begin('st-anchor')
        FakeCraftApi.requests.clear()
        result = self.fire(write('st-anchor', f'blocks add --siblingId {DIARY_TASK} --position after '
                                              f'--json {BULLET_JSON}'))
        self.assert_denied(result, 'не вызван скилл: craft-task')
        self.assertIn(('blocks', {'id': DIARY, 'maxDepth': '1'}), FakeCraftApi.requests)

    def test_an_own_write_without_target_ids_resets_the_tree(self) -> None:
        commands = (f'blocks add --date today --json {LEVEL0_JSON}',
                    'tasks add --markdown "Купить хлеб"')
        for number, command in enumerate(commands):
            session = f'st-own-{number}'
            with self.subTest(command=command):
                self.begin(session)
                raw = write(session, command)
                self.fire(raw)
                self.fire(written(raw))
                # Запись легла в заметку без id цели: её дети больше не известны.
                result = self.fire(write(session, f'blocks add --id {DIARY} --position end '
                                                  f'--json {BULLET_JSON}'))
                self.assert_denied(result, 'не вызван скилл: craft-task')

    def test_a_target_missing_among_the_children_is_asked_once_more_and_then_denied(self) -> None:
        self.start('st-ghost', GHOST_PAGE)
        FakeCraftApi.requests.clear()
        result = self.fire(write('st-ghost', f'blocks add --siblingId {GHOST} --position after '
                                             f'--json {BULLET_JSON}'))
        self.assert_denied(result, 'Запись в непрочитанное')
        children = [query for endpoint, query in FakeCraftApi.requests
                    if endpoint == 'blocks' and query.get('id') == GHOST_PAGE and query.get('maxDepth') == '1']
        self.assertEqual(len(children), 2)


class BatchWrittenTest(ApiLockHarness):
    """Прошедшая пачка сбрасывает в дереве страницы всех своих целей, а не только последней."""

    def test_every_target_of_a_written_batch_is_forgotten(self) -> None:
        session = 'st-batch'
        self.fire(prompt(session))
        self.assert_silent(self.fire(stale_read(session)))
        tree = self.storage(session).read_json(TREE_FILE)
        self.assertIn(DIARY, json.dumps(tree))
        raw = write(session, f'blocks delete --id {DIARY_LINE}; blocks delete --id {GHOST}')
        self.fire(written(raw))
        tree = self.storage(session).read_json(TREE_FILE)
        pages = tree.get('pages', {})
        self.assertIsNone((pages.get(DIARY) or {}).get('children'))


if __name__ == '__main__':
    unittest.main()
