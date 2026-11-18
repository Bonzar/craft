"""History continuations retain filters and their immutable version boundary."""
import json
import unittest

from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_service import ThoughtService


class ThoughtHistoryPagesTest(unittest.TestCase):
    def setUp(self):
        self.memory=MemoryStore(':memory:');self.addCleanup(self.memory.close)
        self.service=ThoughtService(self.memory)
        self.first=self.service.thoughts.publish('First version. '+('a'*500))

    def collect(self,first):
        chunks=[first['content']];cursor=first.get('continuation')
        while cursor:
            method=getattr(self.service,'history' if cursor['tool']=='memory_history' else 'source')
            page=method(**cursor['arguments']);chunks.append(page['content']);cursor=page.get('continuation')
        return json.loads(''.join(chunks))

    def test_history_keeps_exact_version_filter_on_every_page(self):
        second=self.service.thoughts.publish('Second version. '+('b'*500),thought_id=self.first.id,expected_version=1)
        page=self.service.history(self.first.id,version_id=second.version,limit=240)
        self.assertIsNotNone(page['continuation'])
        values=self.collect(page)
        self.assertEqual([value['version'] for value in values],[2])
        self.assertEqual(values[0]['text'],second.text)

    def test_extraction_history_ignores_versions_published_between_pages(self):
        page=self.service.source(self.first.id,section='extraction_history',snapshot_id=1,limit=240)
        self.assertIsNotNone(page['continuation'])
        self.service.thoughts.publish('Second version. '+('b'*500),thought_id=self.first.id,expected_version=1)
        values=self.collect(page)
        self.assertEqual([value['version'] for value in values],[1])
        self.assertEqual(values[0]['text'],self.first.text)
