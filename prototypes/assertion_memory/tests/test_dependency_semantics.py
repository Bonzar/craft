"""Mandatory dependencies require meaning beyond authentic endpoint quotations."""
import json
import unittest
from dataclasses import replace

from prototypes.assertion_memory.capture import CaptureInput
from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.tests.test_semantic_links import assertion
from prototypes.assertion_memory.worker import LinkWorker, _propose


class DependencySemanticsTest(unittest.TestCase):
    def propose(self, source, target, *, status='accepted', source_quote=None, direction=None):
        self.requests = []
        def model(request):
            self.requests.append(request)
            if request.task == 'Verify semantic evidence links':
                return json.dumps({'verdicts': [{'index': 0, 'status': status,
                    'reason': 'Assessment of the exact prerequisite relation.'}]})
            item = {'target_id': target.assertion_id, 'kind': 'depends_on',
                    'source_quote': source_quote or source.text, 'target_quote': target.text}
            if direction:
                item['direction'] = direction
            return json.dumps({'links': [item]})
        return _propose(model, source, {target.assertion_id: target}, {})[0]

    def test_author_identity_is_not_a_substantive_prerequisite_modern_or_legacy(self):
        for modern in (True, False):
            with self.subTest(modern=modern):
                source = assertion('rule', 'The council approved the Atlas deployment.', modern=modern)
                target = assertion('identity', 'Mira is the council secretary.', kind='assertion', modern=modern)
                result = self.propose(source, target, status='unsupported')
                self.assertEqual(result['status'], 'rejected')
                self.assertEqual(result['verification']['status'], 'unsupported')
                self.assertEqual(len(self.requests), 2)
                self.assertIn('substantive prerequisite', self.requests[-1].instructions)
                self.assertIn('attribution', self.requests[-1].instructions)

    def test_cross_topic_planning_dependency_keeps_proposal_kind(self):
        source = assertion('release', 'We propose shipping Atlas after the finance team approves the budget.',
                           kind='proposal')
        target = replace(assertion('budget', 'The finance team must approve the budget.', kind='proposal'),
                         scope='finance')
        result = self.propose(source, target)
        self.assertEqual(result['status'], 'eligible')
        self.assertEqual(result['verification']['status'], 'accepted')
        self.assertEqual(source.metadata['kind'], 'proposal')
        self.assertEqual(target.metadata['kind'], 'proposal')
        self.assertEqual(result['from_id'], 'release')
        self.assertEqual(result['to_id'], 'budget')

    def test_dependency_quotes_cannot_drop_endpoint_qualification(self):
        source = assertion('release', 'Deploy Atlas after approval, only during the pilot.')
        target = assertion('approval', 'The council approves Atlas only after a safety review.')
        result = self.propose(source, target, source_quote='Deploy Atlas after approval')
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(len(self.requests), 1)

    def test_reversed_direction_is_independently_reviewed(self):
        prerequisite = assertion('review', 'The safety review is required before deploying Atlas.')
        dependent = assertion('deploy', 'Deploy Atlas only after the safety review.')
        # Quotes follow relation direction, just as they do for policy links.
        self.requests = []
        def model(request):
            self.requests.append(request)
            if request.task.startswith('Propose'):
                return json.dumps({'links': [{'target_id': 'deploy', 'kind': 'depends_on',
                    'direction': 'candidate_to_source', 'source_quote': dependent.text,
                    'target_quote': prerequisite.text}]})
            link = next(row for row in request.records if row.get('role') == 'proposed_link')
            self.assertEqual((link['from_id'], link['to_id']), ('deploy', 'review'))
            return json.dumps({'verdicts': [{'index': 0, 'status': 'accepted', 'reason': 'Correct direction.'}]})
        result = _propose(model, prerequisite, {'deploy': dependent}, {})[0]
        self.assertEqual(result['verification']['status'], 'accepted')
        self.assertEqual(len(self.requests), 2)

    def test_rejected_identity_dependency_leaves_graph_untouched(self):
        with MemoryStore(':memory:') as store:
            queue = CaptureQueue(store)
            rows = (assertion('identity', 'Atlas council secretary is Mira.', kind='assertion'),
                    assertion('rule', 'Atlas council approved deployment.', day=2))
            events = [queue.capture_event('s', row.assertion_id, row.text, source=row.source) for row in rows]
            with store.transaction(write=True) as connection:
                connection.execute("UPDATE memory_capture_jobs SET status='completed', phase='done'")
            for row, event in zip(rows, events):
                metadata = {**row.metadata, 'evidence_refs': [{'event_id': event.id, 'start': 0,
                            'end': len(row.text), 'role': 'user', 'relation': 'statement'}]}
                queue.capture(CaptureInput(row.text, row.actor, row.source, row.episode,
                    assertion_id=row.assertion_id, scope=row.scope, recorded_at=row.recorded_at,
                    metadata=metadata))
            def model(request):
                if request.task.startswith('Propose'):
                    if request.records[0]['id'] == 'identity':
                        return '{"links":[]}'
                    return json.dumps({'links': [{'target_id': 'identity', 'kind': 'depends_on',
                        'source_quote': rows[1].text, 'target_quote': rows[0].text}]})
                return json.dumps({'verdicts': [{'index': 0, 'status': 'unsupported',
                    'reason': 'Reporter identity is attribution, not a prerequisite.'}]})
            LinkWorker(queue, MemoryEngine(store), model).drain()
            self.assertEqual(store.list_edges(), [])
            self.assertEqual(queue.get('rule').proposals[0]['verification']['status'], 'unsupported')


if __name__ == '__main__':
    unittest.main()
