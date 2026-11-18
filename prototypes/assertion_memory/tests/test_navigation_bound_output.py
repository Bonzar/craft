"""Navigation models write meaning; program bindings own sources and relations."""
import copy
from dataclasses import asdict
import json
from types import SimpleNamespace
import unittest

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import ModelError
from prototypes.assertion_memory.navigation_overview import (
    PROTOCOL, _child_inputs, _reviewed, accepted_navigation_review, build,
)
from prototypes.assertion_memory.storage import MemoryStore


class NavigationBoundOutputTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.requests = []
        self.reject = False
        self.prose = 'Reported label Literal[0] and [source:domain-label]; release requires both checks.'
        for key, text in (('alpha', 'Reported label Literal[0] and [source:domain-label].'),
                          ('beta', 'Release requires both checks.')):
            self.store.append_assertion(assertion_id=key, text=text, actor='council',
                source='fixture://' + key, episode='pilot', scope='s', recorded_at='2026-01-01')
        self.store.append_edge(edge_id='condition', from_id='alpha', to_id='beta', kind='supports',
            recorded_at='2026-01-01', metadata={'condition': 'Only when both checks apply.'})
        self.engine = MemoryEngine(self.store, model=self.provider, verify_overviews=True)
        self.snapshot = self.engine.capture_snapshot()
        self.spec = ViewSpec('labels', 'Release labels', 'Which release labels and conditions were reported?',
                             scopes=('s',), root_ids=('alpha', 'beta'))
        self.engine.define_view(self.spec)
        self.context = {'registry_id': 'synthetic', 'route_revision': 1, 'scope': 's', 'view_id': self.spec.id,
                        'required_source_ids': ['alpha', 'beta'], 'required_view_ids': []}

    def provider(self, request):
        self.requests.append(request)
        if request.task.startswith('Review'):
            result = {'status': 'revise' if self.reject else 'accepted', 'reason': 'All meanings checked.',
                'issues': ['Missing condition.'] if self.reject else [], 'faithfulness': not self.reject,
                'navigation_coverage': 'complete', 'qualifications_preserved': not self.reject}
            if any(row.get('role') == 'editorial_candidate' for row in request.records):
                result['editorial_review'] = {'accepted': True, 'reason': 'Faithful subject title.'}
            return json.dumps(result)
        return json.dumps({'summary': self.prose, 'display_title': 'Reported release conditions'})

    def build(self):
        return build(self.engine, self.spec, self.snapshot, self.context, {}, {}, {})

    def test_semantic_only_models_publish_exact_program_sources_edges_and_editorial_binding(self):
        pipeline = self.engine.pipeline_key
        version = self.build()
        self.assertEqual(version.summary, self.prose)
        self.assertTrue(accepted_navigation_review(version.overview_review))
        self.assertEqual(version.pipeline_key, pipeline)
        self.assertEqual(version.overview_review['protocol'], PROTOCOL)
        proof = version.overview_review['model_review']
        self.assertEqual(proof['review']['source_ids'], ['alpha', 'beta'])
        self.assertEqual(proof['review']['edge_ids'], ['condition'])
        self.assertEqual(proof['packet_binding']['scope'], 'whole_packet_not_individual_phrases')
        self.assertEqual(proof['packet_binding']['text_sha256'], proof['summary_sha256'])
        editorial = proof['review']['editorial_review']
        self.assertEqual(editorial['display_title'], 'Reported release conditions')
        self.assertEqual(editorial['summary_sha256'], proof['summary_sha256'])
        self.assertEqual(len(self.requests), 2)
        for request in self.requests:
            self.assertNotIn('must appear in an actual citation', request.instructions)
            self.assertNotIn('all exact assessed IDs', request.instructions)
            self.assertNotIn('exact supplied hash', request.instructions)

    def test_rejection_of_meaning_still_prevents_parent_acceptance(self):
        self.reject = True
        with self.assertRaisesRegex(ModelError, 'independent review unresolved'):
            self.build()
        self.assertIsNone(self.engine.inspect_view(self.spec.id)['current_id'])

    def test_forged_reference_outputs_are_rejected_and_cannot_change_bound_membership(self):
        normal = self.provider
        for stage in ('Write', 'Review'):
            def forged(request):
                value = json.loads(normal(request))
                if request.task.startswith(stage):
                    value.update(source_ids=['foreign'], edge_ids=['foreign'])
                return json.dumps(value)
            self.engine.model = forged
            with self.subTest(stage=stage), self.assertRaises(ModelError):
                self.build()
        self.assertIsNone(self.engine.inspect_view(self.spec.id)['current_id'])

    def test_packet_mutation_during_acquisition_cannot_receive_a_bound_review(self):
        records = [{'role': 'source', 'id': 'alpha', 'text': 'A fixed reported condition.'}]
        def mutate(request):
            records[0]['text'] = 'A different condition after invocation.'
            return '{"summary":"A fixed reported condition."}'
        self.engine.model = mutate
        with self.assertRaisesRegex(ModelError, 'changed'):
            _reviewed(self.engine, 'Reported condition?', records, {'alpha'}, set(), {}, 'packet')

    def test_writer_membership_mutation_fails_before_reviewer_is_invoked(self):
        sources, edges = {'alpha'}, {'condition'}
        calls = []
        def mutate(request):
            calls.append(request)
            sources.clear(); sources.add('foreign')
            edges.clear(); edges.add('foreign-edge')
            return '{"summary":"A fixed reported condition."}'
        self.engine.model = mutate
        records = [{'role': 'source', 'id': 'alpha', 'text': 'A fixed reported condition.'}]
        with self.assertRaisesRegex(ModelError, 'membership changed'):
            _reviewed(self.engine, 'Reported condition?', records, sources, edges, {}, 'packet')
        self.assertEqual(len(calls), 1)

    def test_reviewer_membership_mutation_cannot_change_returned_program_references(self):
        sources, edges = {'alpha'}, {'condition'}
        normal = self.provider
        def mutate(request):
            if request.task.startswith('Review'):
                sources.clear(); sources.add('foreign')
                edges.clear(); edges.add('foreign-edge')
            return normal(request)
        self.engine.model = mutate
        records = [{'role': 'source', 'id': 'alpha', 'text': 'A fixed reported condition.'}]
        part = _reviewed(self.engine, 'Reported condition?', records, sources, edges, {}, 'packet')
        self.assertEqual(part['source_ids'], ['alpha'])
        self.assertEqual(part['edge_ids'], ['condition'])
        self.assertEqual(part['source_ids'], part['proof']['review']['source_ids'])
        self.assertEqual(part['edge_ids'], part['proof']['review']['edge_ids'])

    def test_legacy_anchors_are_removed_only_in_projection_and_new_brackets_are_literal(self):
        original = 'Legacy meaning [source:alpha], literal `[alpha]`.'
        old = SimpleNamespace(summary=original, evidence=[{'id': 'alpha'}], edges=[], overview_review={'model_review': {}})
        rows, _ = _child_inputs({'old': old}, [{'id': 'alpha'}], [])
        self.assertEqual(rows[0]['summary'], 'Legacy meaning , literal `[alpha]`.')
        self.assertEqual(old.summary, original)
        fresh = SimpleNamespace(summary='Literal[0] [unknown] [alpha]', evidence=[{'id': 'alpha'}], edges=[],
                                overview_review={'model_review': {'packet_binding': {'scope': 'whole_packet_not_individual_phrases'}}})
        rows, _ = _child_inputs({'new': fresh}, [{'id': 'alpha'}], [])
        self.assertEqual(rows[0]['summary'], fresh.summary)

    def test_existing_accepted_audits_remain_readable_without_a_protocol_bump(self):
        current = self.build()
        legacy = copy.deepcopy(current.overview_review)
        legacy['model_review'].pop('packet_binding')
        self.assertTrue(accepted_navigation_review(legacy))
        self.assertEqual(self.engine.get_version(current.id), current)


if __name__ == '__main__':
    unittest.main()
