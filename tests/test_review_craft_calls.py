"""Поставщик review-craft-calls: какие вызовы ложатся в объём ревью и в каком виде."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jarvis.storage import Storage
from jarvis.trace import Trace

from . import CORE_SOURCE, MODULES_DIR

SLUG = 'review-craft-calls'
# Объём ведёт ревью хода, теги ищет замок по data/entity.json модулей сущностей.
NEIGHBOURS = ('turn-review', 'craft-type-lock', 'craft-task', 'craft-stage', 'craft-section',
              'craft-note', 'craft-decision')
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

    def test_a_write_carries_the_type_tags(self) -> None:
        run = self.run_hook(post_tool('s-write', 'mcp__Craft__craft_write', {'command': TASK_ADD},
                                      {'id': BLOCK}))
        self.assertEqual(run.returncode, 0, run.stderr)
        [record] = self.volume('s-write')
        self.assertEqual((record['kind'], record['types']), ('write', ['#задача']))
        self.assertNotIn('types_error', record)

    def test_a_broken_entity_file_keeps_the_write_and_names_the_error(self) -> None:
        path = self.root / 'modules' / 'craft-note' / 'data' / 'entity.json'
        original = path.read_text(encoding='utf-8')
        self.addCleanup(path.write_text, original, encoding='utf-8')
        path.write_text('{"tags":', encoding='utf-8')
        run = self.run_hook(post_tool('s-broken', 'mcp__Craft__craft_write', {'command': TASK_ADD},
                                      {'id': BLOCK}))
        self.assertNotEqual(run.returncode, 0)
        self.assertIn(str(path), run.stderr)
        [record] = self.volume('s-broken')
        self.assertEqual((record['kind'], record['command'], record['types']),
                         ('write', TASK_ADD, None))
        self.assertIn(str(path), record['types_error'])

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
