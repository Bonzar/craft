"""Ready upper maps publish before residual classification, with fresh child checks."""
import copy
import json
import unittest
from unittest.mock import patch

from prototypes.assertion_memory import background_overviews as background
from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.publication_fences import GuardLeaseLost, bind_guard
from prototypes.assertion_memory.query_snapshot import receipt
from prototypes.assertion_memory.ready_map_groups import prepare
from prototypes.assertion_memory.storage import MemoryStore


class ClassificationReached(RuntimeError):
    pass


class ReadyMapGroupsTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.calls = []
        self.expire = False
        self.expired = False
        self.reject_parent = False
        for key in ('permit', 'desk', 'optional', 'residual'):
            self.store.append_assertion(assertion_id=key, text=key + ' has a reported release condition.',
                actor='council', source='fixture://' + key, episode='pilot', scope='s', recorded_at='2026-01-01')
        self.old = MemoryEngine(self.store, model=self.provider, model_key='old', verify_overviews=True)
        self.origin_snapshot = self.old.capture_snapshot()
        self.origin = self.registry(self.old, self.origin_snapshot)
        self.old_versions = [self.old.build_view(key, _snapshot=self.origin_snapshot) for key in self.origin['plan']['depends_on']]
        self.engine = MemoryEngine(self.store, model=self.provider, model_key='new', verify_overviews=True)
        self.snapshot = self.engine.capture_snapshot()
        self.spec, self.request, _ = background._identity(self.engine, 's', self.snapshot)
        self.state = {'stage': 'discovery', 'accepted': {}, 'discovery': {'classifier_cursor': 'preserve'}}
        self.checkpoint = {'overview': self.state}
        self.calls.clear()

    def registry(self, engine, snapshot):
        from prototypes.assertion_memory.discovery_frontier import corpus_state
        from prototypes.assertion_memory.topic_discovery import _definitions
        spec, request, _ = background._identity(engine, 's', snapshot)
        themes = [{'question': key + ' release conditions?', 'source_ids': [key]} for key in ('permit', 'desk', 'optional')]
        children = _definitions(engine, spec, themes)
        _, _, hashes, revision = corpus_state(spec, snapshot)
        manifest = {'request': request, 'pipeline_key': engine.pipeline_key, 'snapshot': snapshot.descriptor(),
            'corpus_revision': revision, 'source_fingerprints': hashes, 'source_count': len(hashes),
            'themes': themes, 'hierarchy': [], 'unassigned_source_ids': ['residual'],
            'membership_acquisitions': [], 'explicit_dependency_ids': [], 'dependency_ids': children}
        plan = {'reuse_view_id': None, 'related_view_ids': [], 'subquestions': [], 'answer_metadata_requests': [],
            'depends_on': children, 'discover_topics': True, 'reason': 'Synthetic prior partial registry',
            'method': 'model', 'protocol': background.PLAN_PROTOCOL, 'request': request, 'discovery': manifest,
            'automatic': {'policy': background.POLICY, 'scope': 's', 'root_request_id': spec.id}}
        return background.publish_navigation_registry(engine, spec, plan)

    def provider(self, request):
        self.calls.append(request)
        if request.task == 'Plan reusable memory theme hierarchy':
            self.expired = self.expire
            ids = [row['id'] for row in request.records if row.get('role') == 'view']
            return json.dumps({'groups': [{'question': 'How do release conditions fit together?', 'depends_on': ids[:2]}]})
        context = request.records[0]
        sources, edges = context['required_source_ids'], context['required_edge_ids']
        if request.task.startswith('Review'):
            rejected = self.reject_parent and context['topic'] == 'How do release conditions fit together?'
            return json.dumps({'status': 'revise' if rejected else 'accepted',
                'reason': 'Missing combined condition.' if rejected else 'All reported qualifications retained.',
                'issues': ['Missing combined condition.'] if rejected else [],
                'faithfulness': not rejected,
                'navigation_coverage': 'complete', 'qualifications_preserved': not rejected})
        prose = [row.get('summary', row.get('text', '')) for row in request.records
                 if row.get('role') in ('source', 'verified_navigation_contribution')]
        return json.dumps({'summary': '\n'.join(prose)})

    def reach_classifier(self):
        def classify(*args, **kwargs):
            raise ClassificationReached('Residual classifier resumes after ready maps.')
        with patch('prototypes.assertion_memory.topic_discovery.discover', side_effect=classify):
            with self.assertRaises(ClassificationReached):
                background._route_for_generation(self.engine, self.spec, self.request, self.snapshot,
                    self.state, self.checkpoint, lambda value: None)

    def test_upper_group_precedes_classifier_and_old_checks_remain_historical(self):
        self.reach_classifier()
        groups = [spec for spec in self.engine.list_views() if spec.id.startswith('theme-group-')]
        self.assertEqual(len(groups), 1)
        current = self.engine.inspect_view(groups[0].id)
        version = self.engine.get_version(current['current_id'])
        self.assertEqual(version.pipeline_key, self.engine.pipeline_key)
        self.assertEqual(set(version.upstream), set(self.origin['plan']['depends_on'][:2]))
        unrelated = self.old_versions[2]
        self.assertEqual(self.engine.inspect_view(unrelated.view_id)['current_id'], unrelated.id)
        self.assertEqual(self.state['discovery'], {'classifier_cursor': 'preserve'})
        self.assertEqual(self.state['stage'], 'discovery')
        with self.store.transaction() as connection:
            from prototypes.assertion_memory.question_routes import _route
            route = _route(connection, self.spec.id)
        manifest = route['plan']['discovery']
        self.assertEqual(manifest['unassigned_source_ids'], ['residual'])
        self.assertEqual(manifest['origin_prior_registry']['view_id'], self.origin['view_id'])
        self.assertTrue(manifest['partial_ready_groups'])
        self.assertEqual(version.overview_review['context']['origin_prior_registry'], manifest['origin_prior_registry'])
        for child in version.overview_review['children']:
            check = receipt(self.engine, child['check_id'])
            self.assertEqual(check['pipeline_key'], self.engine.pipeline_key)
            self.assertEqual(check['query_snapshot'], self.snapshot.descriptor())
        for old in self.old_versions:
            self.assertEqual(self.engine.get_version(old.id), old)
            self.assertNotEqual(old.pipeline_key, self.engine.pipeline_key)

    def test_resume_does_not_repeat_group_model_work_or_consume_classifier_checkpoint(self):
        self.reach_classifier()
        calls = len(self.calls)
        self.reach_classifier()
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(self.state['discovery'], {'classifier_cursor': 'preserve'})

    def test_rejected_optional_parent_records_failure_and_resumes_residual_without_retry_loop(self):
        self.reject_parent = True
        self.reach_classifier()
        work = self.state['ready_groups']
        self.assertEqual(work['stage'], 'failed')
        self.assertIn('independent review unresolved', work['failure']['message'])
        parent = next(spec for spec in self.engine.list_views() if spec.id.startswith('theme-group-'))
        self.assertFalse(any((version.overview_review or {}).get('status') == 'accepted'
                             for version in self.engine.view_history(parent.id)))
        calls = len(self.calls)
        self.reach_classifier()
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(self.state['discovery'], {'classifier_cursor': 'preserve'})

    def test_changed_source_fingerprint_is_not_a_membership_proposal(self):
        from dataclasses import replace
        altered = copy.deepcopy(self.snapshot.assertions)
        altered['permit']['text'] = 'A changed source meaning.'
        altered['optional']['text'] = 'Another changed source meaning.'
        self.snapshot = replace(self.snapshot, assertions=altered)
        prepare(self.engine, self.spec, self.request, self.snapshot, self.state, self.checkpoint, lambda value: None)
        self.assertEqual(self.calls, [])
        self.assertFalse(any(spec.id.startswith('theme-group-') for spec in self.engine.list_views()))

    def test_first_scope_without_a_registry_skips_the_ready_lane(self):
        with MemoryStore(':memory:') as store:
            engine = MemoryEngine(store, model=lambda request: self.fail('No ready maps exist yet.'), verify_overviews=True)
            snapshot = engine.capture_snapshot()
            spec, request, _ = background._identity(engine, 'new', snapshot)
            state = {'stage': 'discovery'}
            prepare(engine, spec, request, snapshot, state, {'overview': state}, lambda value: None)
            self.assertEqual(state['ready_groups']['stage'], 'complete')
            self.assertEqual(state['stage'], 'discovery')

    def test_lost_lease_cannot_publish_a_group_definition_or_partial_registry(self):
        self.expire = True
        def guard(connection):
            if self.expired:
                raise RuntimeError('synthetic lost lease')
        with bind_guard(self.engine, guard):
            with self.assertRaises(GuardLeaseLost):
                prepare(self.engine, self.spec, self.request, self.snapshot, self.state, self.checkpoint, lambda value: None)
        self.assertFalse(any(spec.id.startswith('theme-group-') for spec in self.engine.list_views()))
        self.assertEqual(self.store._connection.execute('SELECT count(*) FROM memory_question_routes WHERE request_id=?',
                                                       (self.spec.id,)).fetchone()[0], 0)


    def legacy_failed_work(self):
        self.reject_parent = True
        self.reach_classifier()
        work = self.state['ready_groups']
        work.pop('writer_policy', None)
        self.reject_parent = False
        return work, copy.deepcopy(work['failure'])

    def test_codec_change_resumes_existing_failed_plan_once_without_replanning(self):
        work, failure = self.legacy_failed_work()
        planner_calls = sum(call.task == 'Plan reusable memory theme hierarchy' for call in self.calls)
        discovery = copy.deepcopy(self.state['discovery'])
        old_versions = [version for spec in self.engine.list_views()
                        for version in self.engine.view_history(spec.id)]
        saved = []
        from prototypes.assertion_memory.ready_map_groups import prepare
        prepare(self.engine, self.spec, self.request, self.snapshot, self.state, self.checkpoint,
                lambda value: saved.append(copy.deepcopy(value)))
        self.assertEqual(work['stage'], 'complete')
        self.assertEqual(work['failure_history'][0]['failure'], failure)
        self.assertEqual(saved[0]['overview']['ready_groups']['stage'], 'build')
        self.assertEqual(saved[0]['overview']['ready_groups']['failure_history'][0]['failure'], failure)
        self.assertNotIn('failure', saved[0]['overview']['ready_groups'])
        self.assertEqual(sum(call.task == 'Plan reusable memory theme hierarchy' for call in self.calls), planner_calls)
        self.assertEqual(self.state['discovery'], discovery)
        for version in old_versions:
            self.assertEqual(self.engine.get_version(version.id), version)
        calls = len(self.calls)
        prepare(self.engine, self.spec, self.request, self.snapshot, self.state, self.checkpoint, lambda value: None)
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(len(work['failure_history']), 1)

    def test_failed_policy_retry_keeps_new_failure_without_another_attempt(self):
        work, old_failure = self.legacy_failed_work()
        self.reject_parent = True
        self.reach_classifier()
        self.assertEqual(work['stage'], 'failed')
        self.assertEqual(work['failure_history'][0]['failure'], old_failure)
        self.assertIn('independent review unresolved', work['failure']['message'])
        self.assertIsNot(work['failure'], old_failure)
        calls = len(self.calls)
        self.reach_classifier()
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(len(work['failure_history']), 1)

    def test_descriptor_exposes_only_bounded_early_assembly_metadata(self):
        from prototypes.assertion_memory.derived_work import DerivedWorkQueue
        queue = DerivedWorkQueue(self.store)
        work = {'stage': 'failed', 'themes': [{'question': 'private source text'}],
                'hierarchy': [{'groups': [{'view_id': 'group-a'}, {'view_id': 'group-b'}]}],
                'accepted': {'group-a': {'version_id': 1, 'check_id': 2},
                             'child-leaf': {'version_id': 3, 'check_id': 4}},
                'failure': {'type': 'ModelError', 'message': 'private diagnostic text'}}
        row = {'scope': 's', 'status': 'pending', 'checkpoint': json.dumps({'overview': {'ready_groups': work}}),
               'result': '{}', 'desired_frontier': '{}', 'completed_frontier': None,
               'error': None, 'updated_at': '2026-01-01', 'next_wake_at': None}
        descriptor = queue._descriptor(row)
        self.assertEqual(descriptor['early_assemblies'], {'state': 'failed', 'group_count': 2,
            'accepted_count': 1, 'failure_type': 'ModelError'})
        self.assertNotIn('private', json.dumps(descriptor))
        row['checkpoint'] = '{}'
        self.assertEqual(queue._descriptor(row)['early_assemblies'], {'state': 'not_started',
            'group_count': 0, 'accepted_count': 0, 'failure_type': None})


if __name__ == '__main__':
    unittest.main()
