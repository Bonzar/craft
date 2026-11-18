"""Editorial provider bodies contain meanings; program state binds their inputs."""
import json
import unittest

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import ModelError
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.view_names import NameReviewContractError


class ProgramBoundNamesTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.requests = []
        self.engine = MemoryEngine(self.store, naming_model=self.provider)
        self.engine.define_view(ViewSpec('private-view-id', 'Existing definition',
            'Under which conditions may the fictional valve be inspected?', scopes=('fiction',)))

    def provider(self, request):
        self.requests.append(request)
        if request.task == 'Name memory view':
            return '{"title":"Valve inspection conditions","reason":"Names the supplied subject."}'
        return '{"accepted":true,"reason":"Faithful to the supplied material."}'

    def prepare(self):
        return self.engine.prepare_view_name('private-view-id', authorized_scopes=('fiction',), operation='fixture')

    def append(self, approved):
        return self.engine.append_view_name(approved,
            expected_definition_sha=approved['definition_sha256'],
            expected_previous_name_id=approved['previous_name_id'])

    def test_semantic_only_writer_and_review_are_program_bound_and_persisted(self):
        pipeline = self.engine.pipeline_key
        candidate = self.prepare()
        approved = self.engine.review_view_name(candidate)
        record = self.append(approved)
        self.assertEqual(record['title'], 'Valve inspection conditions')
        self.assertEqual(approved['review']['context_sha256'], candidate['context_sha256'])
        self.assertEqual(approved['review']['input_fingerprint'], candidate['input_fingerprint'])
        self.assertEqual(approved['review']['title'], candidate['title'])
        self.assertEqual(self.engine.pipeline_key, pipeline)
        self.assertEqual(len(self.requests), 2)
        for request in self.requests:
            data = request.records[0]
            self.assertNotIn('private-view-id', json.dumps(data))
            self.assertFalse(any('sha256' in key or key.endswith('_id') or key == 'input_fingerprint'
                                 for key in data))
            self.assertNotIn('exact input hash', request.instructions)
            self.assertNotIn('exact supplied hash', request.instructions)

    def test_forged_reviewer_binding_is_rejected_without_publication(self):
        candidate = self.prepare()

        def forged(request):
            return json.dumps({'accepted': True, 'reason': 'Fine.',
                               'context_sha256': 'foreign', 'title': 'Different subject'})

        with self.assertRaises(NameReviewContractError):
            self.engine.review_view_name(candidate, model=forged, format_retry=False)
        self.assertEqual(self.store._connection.execute('SELECT count(*) FROM memory_view_names').fetchone()[0], 0)
        self.assertEqual(candidate['title'], 'Valve inspection conditions')

    def test_candidate_mutation_during_review_cannot_publish_another_title(self):
        candidate = self.prepare()

        def mutate(request):
            candidate['title'] = 'Different inspection subject'
            return '{"accepted":true,"reason":"Checked the original supplied title."}'

        try:
            approved = self.engine.review_view_name(candidate, model=mutate)
            self.append(approved)
        except (ValueError, ModelError):
            pass
        else:
            self.fail('review of the original title must not approve a changed title')
        self.assertEqual(self.store._connection.execute('SELECT count(*) FROM memory_view_names').fetchone()[0], 0)

    def test_saved_name_remains_reusable_without_regeneration(self):
        record = self.append(self.engine.review_view_name(self.prepare()))
        self.requests.clear()
        self.engine.naming_model = lambda request: self.fail('saved correct names must not regenerate')
        name = self.engine.resolve_view_name('private-view-id')
        self.assertEqual(name['display_title'], record['title'])
        self.assertEqual(name['naming_status'], 'accepted')
        self.assertEqual(self.requests, [])


if __name__ == '__main__':
    unittest.main()
