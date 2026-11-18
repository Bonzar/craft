"""Operative status follows repair while immutable extraction history keeps attempts."""
import json
from pathlib import Path
import tempfile
import unittest

from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.worker import LinkWorker
from prototypes.assertion_memory.tests import test_dialogue_repair as repair_fixture


class DialogueRepairStatusTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = MemoryStore(Path(self.directory.name) / 'toy.sqlite')
        self.addCleanup(self.store.close)
        self.service = MemoryService(self.store)
        self.fixture = repair_fixture.DialogueRepairTest()
        self.fixture.setUp()
        self.sources = [self.service.queue.capture_event('toy', source.id, source.prompt,
                        actor=source.actor, source=source.source, scope='toy') for source in self.fixture.context]
        self.fixture.intro, self.fixture.rooms, self.fixture.proposal, self.fixture.target = self.sources

    def run_worker(self, *, exhausted=False):
        def provider(request):
            if request.task.startswith('Extract') and request.records[0]['id'] != self.fixture.target.id:
                return json.dumps({'assertions': []})
            if request.task=='Review source material coverage':
                # This fixture tests operative claim repair, not malformed wire output.
                return json.dumps({'source_coverage':{'status':'unresolved',
                    'reason':'Completeness of this source remains semantically unresolved.','omissions':[]}})
            if request.task.startswith('Propose'):
                return json.dumps({'links': [], 'branches': []})
            if exhausted and request.task.startswith('Repair'):
                return json.dumps({'assertions': [self.fixture.candidate()]})
            return self.fixture.provider(request)
        worker = LinkWorker(self.service.queue, self.service.engine, provider)
        worker.drain()
        return self.service.status(self.fixture.target.id, scope='toy')

    def test_repaired_claim_clears_live_unresolved_but_history_keeps_both_verdicts(self):
        status = self.run_worker()
        # The legacy fixture does not review completeness of its three user sources.
        self.assertEqual(status['unresolved_count'], 3)
        self.assertTrue(all(row['proposal']['kind'] == 'source_coverage' for row in status['unresolved']))
        self.assertEqual(status['error_count'], 0)
        self.assertEqual(status['pending_count'], 0)
        self.assertEqual(len(status['job']['extracted_ids']), 2)
        self.assertEqual(len(self.store.list_assertions()), 2)
        self.assertEqual([row['status'] for row in status['job']['proposals'] if row['kind'] == 'interpretation'], ['accepted', 'accepted'])
        history = self.service.queue.extraction_history(self.fixture.target.id)
        self.assertEqual([row['status'] for row in history[0]['reviews'] if row['kind'] == 'interpretation'], ['unresolved', 'accepted', 'accepted'])
        self.assertEqual(history[0]['reviews'][0]['index'], history[0]['reviews'][2]['index'])
        identity = history[0]['reviews'][2]['evidence_refs'][-1]
        self.assertEqual(self.service.queue.resolve_reference(identity)['text'], self.fixture.intro.prompt)
        self.assertEqual([row.prompt for row in self.service.queue.list_events('toy')],
                         [source.prompt for source in self.sources])

    def test_exhausted_claim_counts_once_in_live_status_and_keeps_all_attempts(self):
        status = self.run_worker(exhausted=True)
        # One unresolved interpretation plus three unchecked source inventories.
        self.assertEqual(status['unresolved_count'], 4)
        self.assertEqual(sum(row['proposal']['kind'] == 'source_coverage' for row in status['unresolved']), 3)
        self.assertEqual(status['error_count'], 0)
        self.assertEqual(len(status['job']['extracted_ids']), 1)
        history = self.service.queue.extraction_history(self.fixture.target.id)
        self.assertEqual([row['status'] for row in history[0]['reviews'] if row['kind'] == 'interpretation'],
                         ['unresolved', 'accepted', 'unresolved', 'unresolved'])
        self.assertEqual([row['status'] for row in status['job']['proposals'] if row['kind'] == 'interpretation'], ['accepted', 'unresolved'])

    def test_other_review_kinds_and_unindexed_interpretations_remain_operative(self):
        from prototypes.assertion_memory.queue import _current_reviews
        reviews = [
            {'kind': 'context_recovery', 'index': 0, 'status': 'unresolved'},
            {'kind': 'interpretation', 'status': 'unresolved'},
            {'kind': 'interpretation', 'index': 0, 'status': 'unresolved'},
            {'kind': 'interpretation', 'index': 0, 'status': 'accepted', 'attempt': 1},
        ]
        self.assertEqual(_current_reviews(reviews), [reviews[0], reviews[1], reviews[3]])
        self.assertEqual(reviews[2]['status'], 'unresolved')


if __name__ == '__main__':
    unittest.main()
