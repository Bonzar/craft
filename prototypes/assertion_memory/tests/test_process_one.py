"""A manual run owns one source and only its causal queue work."""
import unittest

from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.tests.test_thought_worker import Engine
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.thought_worker import ThoughtWorker
from prototypes.assertion_memory.process_one import process_one


class ChainEngine(Engine):
    def consolidate(self, changed, *, on_affected=None):
        thought, = changed
        super().consolidate(changed, on_affected=on_affected)
        if thought.id.startswith('from-'):
            overview = self.thoughts.publish('Overview', thought_id='overview',
                origin={'kind': 'inference'}, bases=[self._basis(thought)])
            self.thoughts.connect(thought.id, overview.id, 'Summary')


class ProcessOneTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.queue = CaptureQueue(self.store)
        self.thoughts = ThoughtStore(self.store)
        self.engine = ChainEngine(self.thoughts)
        self.worker = ThoughtWorker(self.queue, self.engine, processing_session_ids=['film'])

    def capture(self, capture_session, turn, stamp, **metadata):
        source = 'test'
        return self.queue.capture_event(capture_session, turn, turn, source=source, recorded_at=stamp,
            metadata={**metadata, 'source_message_at': stamp, 'source_message_clock_basis': {
                'kind': 'producer', 'field': 'timestamp', 'value': stamp, 'source': source}})

    def test_oldest_message_and_its_descendant_finish_without_claiming_other_work(self):
        newer = self.capture('film', 'Newer', '2026-11-19T11:00:00Z')
        other = self.capture('other', 'Other chat', '2026-11-19T08:00:00Z')
        oldest = self.capture('producer', 'Oldest', '2026-11-19T10:00:00Z', native_session_id='film')
        tool = self.capture('film', 'Tool result', '2026-11-19T09:00:00Z', role='tool')
        unrelated = self.thoughts.publish('Unrelated', thought_id='unrelated')
        self.worker.work.enqueue([unrelated])
        report = process_one(self.worker, 'film')
        self.assertEqual(report['event_id'], oldest.id)
        self.assertEqual(report['status'], 'completed')
        self.assertEqual([job['thought_id'] for job in report['jobs']], ['from-' + oldest.id, 'overview'])
        self.assertEqual(self.queue.get(oldest.id).status, 'completed')
        for item in (newer, other, tool):
            self.assertEqual(self.queue.get(item.id).attempts, 0)
        self.assertEqual(self.worker.work.get(unrelated.id)['status'], 'pending')
        self.assertEqual(self.engine.consolidations, [['from-' + oldest.id], ['overview']])

    def test_session_without_dialogue_leaves_all_work_pending(self):
        other = self.capture('other', 'Other', '2026-11-19T08:00:00Z')
        self.assertEqual(process_one(self.worker, 'film')['status'], 'empty')
        self.assertEqual(self.queue.get(other.id).attempts, 0)


if __name__ == '__main__':
    unittest.main()
