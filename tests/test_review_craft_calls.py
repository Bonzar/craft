"""Поставщик review-craft-calls: какие вызовы ложатся в объём ревью и в каком виде."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jarvis import registry
from jarvis.storage import Storage
from jarvis.trace import Trace

from . import CORE_SOURCE, MODULES_DIR

SLUG = 'review-craft-calls'
# Команду разбирает command-tree-craft. Ревью поставщику не нужно: строки он
# пишет по контракту сам.
NEIGHBOURS = ('command-tree-craft',)
TASK_ADD = ('blocks add --siblingId X --position after --json '
            '\'{"type":"text","markdown":"- [ ] Купить молоко #задача/5м"}\'')
BLOCK = 'a6784801-9d92-875c-f146-50159368745b'


def post_tool(session: str, tool: str, tool_input: dict, response) -> dict:
    return {'hook_event_name': 'PostToolUse', 'session_id': session, 'cwd': '/work',
            'tool_name': tool, 'tool_input': tool_input, 'tool_response': response}


class ReviewCraftCallsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        ignore = shutil.ignore_patterns('__pycache__')
        for slug in (SLUG, *NEIGHBOURS):
            module_dir = cls.root / 'modules' / slug
            shutil.copytree(MODULES_DIR / slug, module_dir, ignore=ignore)
            shutil.copytree(CORE_SOURCE, module_dir / '_core', ignore=ignore)
        cls.entry = cls.root / 'modules' / SLUG / 'hooks' / 'module.py'

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def run_hook(self, raw: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.entry), '--harness', 'claude', '--event', 'post-tool'],
            input=json.dumps(raw, ensure_ascii=False), text=True, capture_output=True,
            env={'PATH': '/usr/bin:/bin', 'HOME': str(self.root)}, check=False,
        )

    def storage(self, session: str) -> Storage:
        return Storage(self.root / '.local' / 'state' / 'jarvis', session)

    def volume(self, session: str) -> list[dict]:
        return [json.loads(line) for line in self.storage(session).read_lines('turn-review-volume.jsonl')]

    def last_reason(self, session: str) -> str:
        return Trace(self.storage(session)).read()[-1]['reason']

    def test_a_write_lies_as_is_with_the_prompt_id(self) -> None:
        raw = {**post_tool('s-write', 'mcp__Craft__craft_write', {'command': TASK_ADD},
                           {'id': BLOCK}), 'prompt_id': 'p-1'}
        run = self.run_hook(raw)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.volume('s-write'), [
            {'kind': 'write', 'prompt_id': 'p-1', 'tool': 'mcp__Craft__craft_write',
             'command': TASK_ADD, 'result': {'id': BLOCK}}])

    def test_without_a_prompt_id_the_field_is_left_out(self) -> None:
        run = self.run_hook(post_tool('s-no-id', 'Skill', {'skill': 'jarvis:craft-task'},
                                      {'success': True}))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.volume('s-no-id'), [{'kind': 'skill', 'skill': 'craft-task'}])

    def test_the_rows_follow_the_review_contract(self) -> None:
        # Схему строк держит ревью: строка поставщика читается его разбором.
        review = registry.LibraryModule('turn-review', MODULES_DIR / 'turn-review' / 'lib').load()
        raw = {**post_tool('s-contract', 'mcp__Craft__craft_read',
                           {'command': f'blocks get {BLOCK}'}, '# Страница'), 'prompt_id': 'p'}
        self.assertEqual(self.run_hook(raw).returncode, 0)
        [row] = self.volume('s-contract')
        self.assertEqual(review.record_row(review.parse_record(row)), row)

    def test_another_craft_tool_is_silent_with_its_own_reason(self) -> None:
        run = self.run_hook(post_tool('s-other-craft', 'mcp__Craft__craft_search',
                                      {'command': 'search молоко'}, 'ничего'))
        self.assertEqual((run.returncode, run.stdout), (0, ''), run.stderr)
        self.assertIn('craft_search', self.last_reason('s-other-craft'))
        self.assertIn('не пишется', self.last_reason('s-other-craft'))
        self.assertEqual(self.volume('s-other-craft'), [])

    def test_a_tool_outside_craft_keeps_the_foreign_reason(self) -> None:
        run = self.run_hook(post_tool('s-foreign', 'mcp__github__get_me', {}, {}))
        self.assertEqual((run.returncode, run.stdout), (0, ''), run.stderr)
        self.assertEqual(self.last_reason('s-foreign'), 'вызов не Craft и не скилл')


if __name__ == '__main__':
    unittest.main()
