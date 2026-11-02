"""Замок craft-type-lock: дерево из прочитанного — место записи без похода в API."""

import json
import sys
import unittest

from jarvis import registry

from . import MODULES_DIR
from .craft_type_lock_harness import (
    BARE_TASK,
    BLOCKS,
    BULLET_JSON,
    D2,
    DAY,
    DAY_LINE,
    LEVEL0_JSON,
    PAGE,
    PARSER,
    PROJECT,
    STAGE,
    STAGE2,
    TASK,
    TASK2,
    TASK_JSON,
    ApiLockHarness,
    FakeCraftApi,
    compact,
    load_lock_module,
    prune,
    written,
)

TREE_FILE = 'craft-type-lock-tree.json'
CLOSE_TASK = f'tasks update --id {TASK} --state done'
ADD_TO_STAGE = f'blocks add --siblingId {TASK} --position after --json {TASK_JSON}'
MOVE_TASK = f'blocks move --id {TASK2} --targetSiblingId {BARE_TASK} --position after'


def mcp_answer(text: str) -> list[dict]:
    """Ответ инструмента Craft MCP в `post-tool`: список текстовых кусков (замер)."""
    return [{'type': 'text', 'text': text}]


def json_read(session: str, page_id: str, depth: int = -1) -> dict:
    tree = prune(BLOCKS[page_id][0], depth)
    return {'hook_event_name': 'PostToolUse', 'session_id': session, 'cwd': '/work',
            'tool_name': 'mcp__Craft__craft_read',
            'tool_input': {'command': f'blocks get {page_id} --depth {depth} --format json'},
            'tool_response': mcp_answer(json.dumps({'data': [tree]}, ensure_ascii=False))}


def render(node: dict, pad: str = '') -> list[str]:
    """Страница, как её рисует markdown-чтение: id только у страниц, отступов страниц нет."""
    if node.get('type') != 'page':
        return [pad + node.get('markdown', '')]
    lines = [f'{pad}<page id="{node["id"]}">', f'{pad}  <pageTitle>{node["markdown"].strip()}</pageTitle>',
             f'{pad}  <content>']
    for child in node.get('content', []):
        lines += render(child, pad + '    ')
    return [*lines, f'{pad}  </content>', f'{pad}</page>']


def markdown_read(session: str, page_id: str) -> dict:
    return {'hook_event_name': 'PostToolUse', 'session_id': session, 'cwd': '/work',
            'tool_name': 'mcp__Craft__craft_read',
            'tool_input': {'command': f'blocks get {page_id} --depth -1'},
            'tool_response': mcp_answer('\n'.join(render(BLOCKS[page_id][0])))}


def lock_write(session: str, command: str) -> dict:
    return {'hook_event_name': 'PreToolUse', 'session_id': session, 'cwd': '/work',
            'tool_name': 'mcp__Craft__craft_write', 'tool_input': {'command': command}}


class TreeFromReadsTest(ApiLockHarness):
    """Сколько запросов к API стоит запись после чтения: цель — ноль."""

    def calls(self, session: str, command: str, needles=()) -> list[str]:
        """Запросы к API на одной записи; запись запрещена с причинами needles."""
        FakeCraftApi.requests.clear()
        result = self.fire(lock_write(session, command))
        if needles:
            self.assert_denied(result, *needles)
        return [f'{endpoint}:{query.get("maxDepth", "")}' for endpoint, query in FakeCraftApi.requests]

    def begin(self, session: str, *raws: dict) -> None:
        self.fire({'hook_event_name': 'UserPromptSubmit', 'session_id': session, 'cwd': '/work',
                   'prompt': 'дальше'})
        for raw in raws:
            self.assert_silent(self.fire(raw))

    def test_a_json_read_costs_no_request_on_add_close_and_move(self) -> None:
        self.begin('t-json', json_read('t-json', PROJECT))
        stage = '«- [ ] ### Этап #этап»'
        cases = ((ADD_TO_STAGE, (stage, 'не вызван скилл: craft-stage, craft-task')),
                 (CLOSE_TASK, (stage, 'не вызван скилл: craft-stage, craft-task')),
                 (MOVE_TASK, (stage, '«- [ ] ### Второй этап #этап»')),
                 (f'blocks add --id {TASK} --position end --json {LEVEL0_JSON}', ('не вызван скилл: craft-task.',)))
        for command, needles in cases:
            with self.subTest(command=command):
                self.assertEqual(self.calls('t-json', command, needles), [])

    def test_the_same_writes_judge_alike_without_the_tree(self) -> None:
        # Сверка с путём через API: дерево не меняет суждения, только цену.
        for number, command in enumerate((ADD_TO_STAGE, CLOSE_TASK, MOVE_TASK)):
            session = f't-api-{number}'
            with self.subTest(command=command):
                self.begin(session, {**json_read(session, PROJECT), 'tool_response': {'ok': True}})
                self.assertNotEqual(self.calls(session, command, ('«- [ ] ### Этап #этап»',)), [])

    def test_a_markdown_read_costs_one_request_for_the_page_children(self) -> None:
        for number, command in enumerate((CLOSE_TASK, f'tasks update --id {BARE_TASK} --state done')):
            session = f't-markdown-{number}'
            with self.subTest(command=command):
                self.begin(session, markdown_read(session, PROJECT))
                calls = self.calls(session, command, ('«- [ ] ### Этап #этап»', 'craft-stage, craft-task'))
                self.assertEqual(calls, ['blocks:1'])
                # Дети страницы пришли в дерево: следующая запись — без запроса.
                self.assertEqual(self.calls(session, MOVE_TASK, ('«- [ ] ### Второй этап #этап»',)), [])

    def test_an_own_write_makes_its_page_be_asked_again(self) -> None:
        self.begin('t-stale', json_read('t-stale', PROJECT))
        raw = lock_write('t-stale', CLOSE_TASK)
        self.fire(raw)
        self.fire(written(raw))
        calls = self.calls('t-stale', f'tasks update --id {TASK2} --state done', ('«- [ ] ### Второй этап #этап»',))
        self.assertEqual(calls, ['blocks:1'])

    def test_compaction_drops_the_tree(self) -> None:
        self.begin('t-compact', json_read('t-compact', PROJECT))
        self.fire(compact('t-compact'))
        self.assertEqual(self.storage('t-compact').read_json(TREE_FILE)['pages'], {})
        self.assertNotEqual(self.calls('t-compact', CLOSE_TASK, ('«- [ ] ### Этап #этап»',)), [])

    def test_a_document_root_is_asked_once_and_then_remembered(self) -> None:
        self.begin('t-root', json_read('t-root', DAY))
        command = f'blocks add --siblingId {DAY_LINE} --position after --json {BULLET_JSON}'
        self.assertEqual(self.calls('t-root', command), ['blocks/search:'])
        self.assertIn('без владельца с типом', self.last_reason('t-root'))
        self.assertEqual(self.calls('t-root', command), [])

    def test_a_target_outside_every_read_is_still_into_the_unread(self) -> None:
        self.begin('t-unread', json_read('t-unread', PROJECT))
        self.calls('t-unread', f'blocks add --siblingId {D2} --position after --json {BULLET_JSON}',
                   ('Запись в непрочитанное',))

    def test_a_section_read_in_json_finds_its_decisions_without_requests(self) -> None:
        self.begin('t-section', json_read('t-section', PAGE))
        calls = self.calls('t-section', f'blocks add --siblingId {D2} --position after --json {BULLET_JSON}',
                           ('Запись задевает сущность «Раздел #раздел»',))
        self.assertEqual(calls, [])

    def test_a_shallow_json_read_still_knows_the_stages(self) -> None:
        # Глубина 1: у этапов и задач нет детей в ответе, но их отступы и тексты есть.
        self.begin('t-shallow', json_read('t-shallow', PROJECT, depth=1))
        self.assertEqual(self.calls('t-shallow', CLOSE_TASK, ('«- [ ] ### Этап #этап»',)), [])
        self.assertEqual(self.calls('t-shallow', f'blocks add --id {TASK} --position end --json {LEVEL0_JSON}',
                                    ('не вызван скилл: craft-task.',)), [])
        # Отступ в детях задачи: её дети не прочитаны — один запрос.
        self.assertEqual(self.calls('t-shallow', f'blocks add --id {TASK} --position end --json {BULLET_JSON}',
                                    ('не вызван скилл: craft-task.',)), ['blocks:1'])


class TreeUnitTest(unittest.TestCase):
    """Разбор ответов чтения и потолки кэша."""

    @classmethod
    def setUpClass(cls) -> None:
        load_lock_module()
        cls.tree = sys.modules['tree']
        cls.reads = sys.modules['reads']
        cls.craft = registry.LibraryModule(PARSER, MODULES_DIR / PARSER / 'lib').load()

    def new(self, now: float = 1000.0):
        return self.tree.Tree(self.tree.empty(), now)

    def test_every_answer_shape_gives_its_text(self) -> None:
        text = '{"data": []}'
        for shape in (text, mcp_answer(text), {'content': mcp_answer(text)}):
            with self.subTest(shape=shape):
                self.assertEqual(self.reads.response_text(shape), text)

    def test_markdown_gives_page_nesting_only(self) -> None:
        pages = self.reads.markdown_pages('\n'.join(render(BLOCKS[PROJECT][0])))
        parents = dict(pages)
        self.assertIsNone(parents[PROJECT])
        self.assertEqual((parents[STAGE], parents[TASK]), (PROJECT, PROJECT))
        self.assertNotIn(BARE_TASK, parents)

    def test_a_cursor_read_appends_the_next_children(self) -> None:
        tree = self.new()
        root = {'id': PROJECT, 'type': 'page', 'markdown': 'Проект',
                'content': [{'id': f'00000000-0000-0000-0000-{n:012d}', 'markdown': str(n)} for n in range(50)]}
        tree.learn_json(root, 0, '50:x')
        self.assertEqual(tree.children(PROJECT)[1], False)
        rest = {**root, 'content': [{'id': STAGE2, 'markdown': 'последний'}]}
        tree.learn_json(rest, 50, None)
        children, complete = tree.children(PROJECT)
        self.assertEqual((len(children), complete), (51, True))
        self.assertEqual(tree.parent(STAGE2), PROJECT)

    def test_a_cursor_read_that_does_not_join_leaves_the_list_alone(self) -> None:
        tree = self.new()
        tree.learn_json({'id': PROJECT, 'content': [{'id': STAGE}]}, 0, None)
        tree.learn_json({'id': PROJECT, 'content': [{'id': STAGE2}]}, 50, None)
        self.assertEqual([child['id'] for child in tree.children(PROJECT)[0]], [STAGE])

    def test_the_cache_keeps_the_freshest_pages_and_forgets_old_ones(self) -> None:
        tree = self.new()
        for number in range(self.tree.MAX_PAGES + 5):
            tree.now = 1000.0 + number
            tree.learn_json({'id': f'00000000-0000-0000-0000-{number:012d}', 'content': []}, 0, None)
        tree.prune()
        self.assertEqual(len(tree.data['pages']), self.tree.MAX_PAGES)
        self.assertNotIn('00000000-0000-0000-0000-000000000000', tree.data['pages'])
        tree.now += self.tree.TTL_SEC + 60
        self.assertIsNone(tree.page(f'00000000-0000-0000-0000-{self.tree.MAX_PAGES + 4:012d}'))
        tree.prune()
        self.assertEqual(tree.data['pages'], {})

    def test_a_fresh_read_replaces_the_subtree(self) -> None:
        tree = self.new()
        tree.learn_json(BLOCKS[PROJECT][0], 0, None)
        self.assertIsNotNone(tree.page(STAGE))
        tree.learn_markdown([(PROJECT, None)])
        self.assertIsNone(tree.page(STAGE))
        self.assertTrue(tree.is_unknown(PROJECT))

    def test_a_broken_tree_file_is_an_error_with_its_path(self) -> None:
        with self.assertRaises(ValueError) as caught:
            self.tree.check({'pages': []}, '/x/tree.json')
        self.assertIn('/x/tree.json', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
