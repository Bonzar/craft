"""New thematic context must be able to acquire unchanged eligible sources."""
import copy
import json
import unittest

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import Candidate, CandidateSearch, ModelError, format_prompt
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.topic_discovery import discover


class ThemeAcquisitionTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.requests, self.searches = [], []
        self.new_context = False
        self.unassigned = False
        self.invalid_selection = False
        owner = self
        class Ranking:
            def rank(self, query, candidates, *, limit):
                owner.searches.append({'query': query, 'ids': set(candidates)})
                if candidates and all(key.isdigit() for key in candidates):
                    return CandidateSearch().rank(query, candidates, limit=limit)
                return [Candidate(key, 1, 'fixture relevance') for key in candidates if key == 'old-run'][:limit]
        self.engine = MemoryEngine(self.store, model=self.model, candidate_search=Ranking())
        self.spec = ViewSpec('scope-overview', 'Overview', 'What is known?', scopes=('lab',))
        self.request = {'question': self.spec.query, 'scopes': ['lab'], 'valid_as_of': None, 'recorded_as_of': None}

    def append(self, key, text, scope='lab', recorded_at='2026-01-01'):
        self.store.append_assertion(assertion_id=key, text=text, actor='Cy', source='fixture://' + key,
            episode='fictional-run', scope=scope, recorded_at=recorded_at)

    def model(self, request):
        self.requests.append(request)
        self.assertLessEqual(len(format_prompt(request)), 60000)
        if request.task == 'Discover reusable memory themes':
            ids = [row['id'] for row in request.records if row.get('role') == 'source']
            question = 'How was the run time used?' if self.new_context else 'How long was the run?'
            return json.dumps({'themes': [] if self.unassigned else [{'question': question, 'source_ids': ids}],
                               'unassigned_source_ids': ids if self.unassigned else []})
        if request.task == 'Plan memory retrieval for a question':
            candidate = next((row['id'] for row in request.records if row.get('role') == 'view'
                              and row['question'] == request.records[0]['question']), None)
            return json.dumps({'reuse_view_id': candidate, 'related_view_ids': [], 'subquestions': [], 'reason': 'New intent.'})
        if request.task == 'Review thematic source membership':
            ids = [row['id'] for row in request.records if row.get('role') == 'candidate']
            return json.dumps({'source_ids': ['forbidden'] if self.invalid_selection else ids,
                               'unresolved_source_ids': [], 'reason': 'The prior duration supports this new account.'})
        self.fail('Unexpected model task: ' + request.task)

    def run_discovery(self, previous=None, checkpointed=False):
        return discover(self.engine, self.spec, self.request, self.engine.capture_snapshot(), previous=previous,
                        progress={} if checkpointed else None)

    def exercise_new_theme(self, checkpointed):
        self.append('old-run', 'Cy reports the test run lasted from 09:00 until 12:00.')
        _, previous = self.run_discovery(checkpointed=checkpointed)
        frozen = copy.deepcopy(previous)
        self.append('cleaning', 'Cy reports the 20-minute equipment cleaning was inside that test run.')
        self.new_context = True
        definitions, manifest = self.run_discovery(previous, checkpointed)
        selected = next(theme for theme in manifest['themes'] if theme['question'] == 'How was the run time used?')
        self.assertEqual(set(selected['source_ids']), {'old-run', 'cleaning'})
        self.assertEqual(previous, frozen, 'prior immutable discovery proof must stay unchanged')
        self.assertIn('old-run', manifest['membership_acquisitions'][0]['examined_source_ids'])
        self.assertEqual(manifest['scan_source_ids'], ['cleaning'])
        self.assertNotEqual(definitions, [])

    def test_new_theme_acquires_old_unchanged_source(self):
        self.exercise_new_theme(False)

    def test_checkpointed_new_theme_acquires_old_unchanged_source(self):
        self.exercise_new_theme(True)

    def test_previously_unassigned_source_can_join_new_theme(self):
        self.append('old-run', 'Cy reports the test run lasted three hours.')
        self.unassigned = True
        _, previous = self.run_discovery()
        self.unassigned = False
        self.new_context = True
        self.append('cleaning', 'Cy reports cleaning occurred within that test run.')
        _, manifest = self.run_discovery(previous)
        self.assertEqual(set(manifest['themes'][0]['source_ids']), {'old-run', 'cleaning'})
        self.assertEqual(manifest['unassigned_source_ids'], [])

    def test_acquisition_never_accepts_an_unoffered_source(self):
        self.append('old-run', 'Cy reports the test run lasted three hours.')
        _, previous = self.run_discovery()
        self.append('cleaning', 'Cy reports cleaning occurred within that test run.')
        self.new_context = self.invalid_selection = True
        with self.assertRaises(ModelError):
            self.run_discovery(previous)

    def test_many_new_anchors_are_processed_in_bounded_contexts(self):
        self.append('old-run', 'Cy reports the test run lasted three hours.')
        _, previous = self.run_discovery()
        for index in range(40):
            self.append('new-' + str(index), 'Cy reports a qualified activity inside that test run. ' * 55)
        self.new_context = True
        _, manifest = self.run_discovery(previous, checkpointed=True)
        selected = next(row for row in manifest['themes'] if row['question'] == 'How was the run time used?')
        self.assertEqual(len(selected['source_ids']), 41)
        self.assertEqual(set(manifest['scan_source_ids']), {'new-' + str(index) for index in range(40)})

    def test_checkpoint_resume_does_not_repeat_a_membership_model_call(self):
        from prototypes.assertion_memory.derived_work import DerivedYield
        self.append('old-run', 'Cy reports the test run lasted three hours.')
        _, previous = self.run_discovery()
        self.append('cleaning', 'Cy reports cleaning occurred within that test run.')
        self.new_context = True
        progress = {}
        snapshot = self.engine.capture_snapshot()
        def yield_after_membership(value):
            if value.get('membership_work', {}).get('batches'):
                raise DerivedYield()
        with self.assertRaises(DerivedYield):
            discover(self.engine, self.spec, self.request, snapshot,
                     previous=previous, progress=progress, save_progress=yield_after_membership)
        progress = json.loads(json.dumps(progress))
        _, manifest = discover(self.engine, self.spec, self.request, snapshot,
                               previous=previous, progress=progress)
        calls = [row for row in self.requests if row.task == 'Review thematic source membership']
        self.assertEqual(len(calls), 1)
        self.assertEqual(set(manifest['themes'][-1]['source_ids']), {'old-run', 'cleaning'})


if __name__ == '__main__':
    unittest.main()
