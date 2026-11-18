"""Synthetic bounded provider tests; scheduling evidence, not model quality."""
import copy
from contextlib import ExitStack
import json
import sqlite3
import unittest
from dataclasses import replace
from unittest.mock import patch

from prototypes.assertion_memory import background_overviews as background
from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.model import ModelError, format_prompt
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.tests import test_background_overviews as fixtures


class IndependentPartialLeavesTest(unittest.TestCase):
    setUp = fixtures.BackgroundOverviewTest.setUp

    def configure(self, *, unassigned=False, rejected=(), groups=False):
        self.unassigned, self.rejected, self.groups = unassigned, set(rejected), groups
        self.themes = [('Voltage?', ['voltage']), ('Gate?', ['gate']), ('Interface?', ['interface'])]
        self.engine = MemoryEngine(self.store, model=self.provider, model_key='independent-fixture', verify_overviews=True)
        return self.engine

    def provider(self, request):
        self.requests.append(request)
        self.assertLessEqual(len(format_prompt(request)), 60000)
        if request.task == 'Discover reusable memory themes':
            ids = {r['id'] for r in request.records if r.get('role') == 'source'}
            themes = [{'question': q, 'source_ids': [k for k in members if k in ids]}
                      for q, members in self.themes]
            themes = [t for t in themes if t['source_ids']]
            assigned = {k for t in themes for k in t['source_ids']}
            return json.dumps({'themes': themes, 'unassigned_source_ids': sorted(ids - assigned)})
        if request.task == 'Plan reusable memory theme hierarchy':
            ids = [r['id'] for r in request.records if r.get('role') == 'view']
            return json.dumps({'groups': [{'question': 'First group?', 'depends_on': ids[:2]},
                                          {'question': 'Later group?', 'depends_on': ids[2:]}]})
        if request.task == 'Review grounded memory overview':
            question = next(r['question'] for r in request.records if r.get('role') == 'query_context')
            value = {'status': 'revise' if question in self.rejected else 'accepted',
                'coverage': 'all_claims', 'reason': 'Fixture checks the entire question and its conditions.',
                'issues': ['Missing material qualification.'] if question in self.rejected else [],
                'context_requests': [], 'unknown_needs': []}
            inventory = next((r for r in request.records if r.get('role') == 'source_gap_inventory'), None)
            if inventory:
                quote = 'The condition remains unresolved; permission cannot be established.'
                value['source_gap_dispositions'] = [{'event_id': r['event_id'], 'run_id': r['run_id'],
                    'disposition': 'material', 'reason': 'A condition could change the action conclusion.',
                    'source_ids': [], 'answer_qualified': True, 'qualification_quote': quote} for r in inventory['gaps']]
            return json.dumps(value)
        if request.task in {q for q, _ in self.themes}:
            text = '\n'.join(r['text'] + ' [source:' + r['id'] + ']' for r in request.records
                             if r.get('id') and r.get('text'))
            return text + '\nThe condition remains unresolved; permission cannot be established.'
        # Existing fixture handles equivalence planning and packet contracts.
        self.requests.pop()
        return fixtures.BackgroundOverviewTest.model(self, request)

    def prepare(self, *, unresolved=False, snapshot=None):
        snapshot = snapshot or self.engine.capture_snapshot()
        spec, request, generation = background._identity(self.engine, 's', snapshot)
        state = {'generation': generation, 'root_request_id': spec.id, 'stage': 'discovery', 'accepted': {}}
        checkpoint = {'overview': state}
        with ExitStack() as stack:
            if self.groups:
                from prototypes.assertion_memory.topic_hierarchy import _level
                def group(engine, spec, request, ids):
                    children = [{'role': 'view', 'id': k, 'question': engine._spec(k).query} for k in ids]
                    dependencies, level = _level(engine, spec, request, children, 24000)
                    return dependencies, [level]
                stack.enter_context(patch('prototypes.assertion_memory.topic_hierarchy.group_definitions', side_effect=group))
            if unresolved:
                from prototypes.assertion_memory.topic_discovery import discover
                def acquired(*args, **kwargs):
                    children, manifest = discover(*args, **kwargs)
                    manifest['membership_acquisitions'] = [{'unresolved_source_ids': ['interface']}]
                    return children, manifest
                stack.enter_context(patch('prototypes.assertion_memory.topic_discovery.discover', side_effect=acquired))
            route = background._route_for_generation(self.engine, spec, request, snapshot, state, checkpoint, lambda v: None)
        return snapshot, checkpoint, route

    def run_scope(self, snapshot, checkpoint, save=lambda v: None):
        return background.run_background_scope(self.engine, 's', snapshot, checkpoint, save)

    def leaf_ids(self, route):
        return [t['view_id'] for t in route['plan']['discovery']['themes']]

    def assert_partial(self, checkpoint):
        state = checkpoint['overview']
        self.assertEqual(state['stage'], 'build')
        self.assertIsNone(self.engine.inspect_view(state['root_view_id'])['current_id'])

    def test_genuine_residual_attempts_all_four_registered_leaves_before_terminal_refusal(self):
        self.configure(unassigned=True)
        for i in range(14):
            self.store.append_assertion(assertion_id='r' + str(i), text='Attributed condition ' + str(i),
                actor='Cy', source='fixture://r' + str(i), episode='one', scope='s', recorded_at='2026-01-01')
        self.themes = [('Theme ' + str(i) + '?', ['r' + str(n) for n in range(i * 3, (i + 1) * 3)]) for i in range(4)]
        snapshot, checkpoint, route = self.prepare()
        with self.assertRaisesRegex(ModelError, 'unassigned') as caught:
            self.run_scope(snapshot, checkpoint)
        self.assertTrue(caught.exception.terminal_discovery)
        self.assertEqual(set(checkpoint['overview']['accepted']), set(self.leaf_ids(route)))
        from prototypes.assertion_memory.service import MemoryService
        page = MemoryService(self.store, self.engine).status(scope='s', section='navigation_catalog')['navigation_catalog']
        self.assertTrue(all(r['accepted_snapshot'] for r in page['entries']))
        self.assertTrue(all(not r['domain_answer_current'] for r in page['entries']))
        self.assert_partial(checkpoint)

    def test_unresolved_membership_keeps_full_scoped_selection_and_blocks_nav(self):
        self.configure()
        snapshot, checkpoint, route = self.prepare(unresolved=True)
        from prototypes.assertion_memory.discovery_frontier import current_membership
        self.assertFalse(current_membership(self.engine, self.engine._spec(self.leaf_ids(route)[0]), snapshot))
        with self.assertRaisesRegex(ModelError, 'unassigned') as caught:
            self.run_scope(snapshot, checkpoint)
        self.assertFalse(getattr(caught.exception, 'terminal_discovery', False))
        self.assertEqual(set(checkpoint['overview']['accepted']), set(self.leaf_ids(route)))
        self.assert_partial(checkpoint)

    def test_first_semantic_rejection_does_not_starve_later_safe_leaf(self):
        self.configure(rejected={'Voltage?'})
        snapshot, checkpoint, route = self.prepare()
        with self.assertRaises(ModelError):
            self.run_scope(snapshot, checkpoint)
        first, *later = self.leaf_ids(route)
        self.assertEqual(set(checkpoint['overview']['accepted']), set(later))
        failure = checkpoint['overview']['failed'][first]
        self.assertEqual(failure['view_id'], first)
        self.assertTrue(self.engine.get_version(failure['version_id']).overview_review['failure_proof'])
        self.assertIsNone(self.engine.inspect_view(first)['current_id'])
        self.assert_partial(checkpoint)

    def pending(self):
        return {'scope': 's', 'event_id': 'unread', 'run_id': None, 'status': 'pending', 'gap': False, 'context_only': False}

    def source_gap(self):
        from prototypes.assertion_memory.queue import CaptureQueue
        event = CaptureQueue(self.store).capture_event('invented', 'ambiguous',
            'RAW_ONLY: operation depends on that missing prerequisite.', actor='Cy',
            source='fixture://ambiguous', scope='s', recorded_at='2026-01-01')
        result = {'assertion_ids': [], 'reviews': [{'kind': 'source_coverage', 'status': 'unresolved',
            'reason': 'The referent remains unresolved.'}], 'context_recovery': [
                {'operation': 'source_coverage', 'protocol': 'fixture-v1', 'status': 'unresolved',
                 'attempt': 0, 'omissions': []}],
            'source_revision': {'schema': 'source-interpretation/v1', 'event_id': event.id,
                'protocol': 'fixture-v1', 'input_fingerprint': 'fixture-gap', 'status': 'unresolved',
                'assertion_ids': [], 'reconciliation': []}}
        with self.store.transaction(write=True) as db:
            db.execute('INSERT INTO memory_extraction_runs(event_id,fingerprint,completed_at,result) VALUES(?,?,?,?)',
                (event.id, 'gap', '2026-01-02', json.dumps(result)))
            db.execute("UPDATE memory_capture_jobs SET status='completed',phase='done',proposals=? WHERE event_id=?",
                (json.dumps(result['reviews']), event.id))

    def test_material_raw_unknown_qualifies_real_leaf_without_becoming_positive_evidence(self):
        self.configure()
        self.source_gap()
        snapshot, checkpoint, route = self.prepare()
        with self.assertRaisesRegex(ModelError, 'source processing'):
            self.run_scope(snapshot, checkpoint)
        self.assertEqual(len(checkpoint['overview']['accepted']), 3)
        for key, saved in checkpoint['overview']['accepted'].items():
            version = self.engine.get_version(saved['version_id'])
            self.assertTrue(version.overview_review['source_gap_review']['processing_gaps_apply_to_query'])
            self.assertIn('permission cannot be established', version.summary)
            self.assertNotIn('RAW_ONLY', version.summary)
        self.assertTrue(any('RAW_ONLY' in format_prompt(r) for r in self.requests if r.task == 'Review grounded memory overview'))
        self.assertFalse(any('RAW_ONLY' in format_prompt(r) for r in self.requests if r.task in {q for q, _ in self.themes}))

    def test_generic_unknown_disclaimer_cannot_authorize_action_or_starve_qualified_sibling(self):
        self.configure()
        self.source_gap()
        snapshot, checkpoint, route = self.prepare()
        original = self.engine.model
        def unsafe(request):
            output = original(request)
            if request.task == 'Voltage?':
                return output + '\nNevertheless operation is allowed now.'
            if request.task == 'Review grounded memory overview':
                question = next(r['question'] for r in request.records if r.get('role') == 'query_context')
                if question == 'Voltage?':
                    value = json.loads(output)
                    for row in value['source_gap_dispositions']:
                        row['answer_qualified'] = False
                    return json.dumps(value)
            return output
        self.engine.model = unsafe
        with self.assertRaises(ModelError):
            self.run_scope(snapshot, checkpoint)
        first, *later = self.leaf_ids(route)
        self.assertNotIn(first, checkpoint['overview']['accepted'])
        self.assertEqual(set(checkpoint['overview']['accepted']), set(later))
        self.assertIn(first, checkpoint['overview']['failed'])
        self.assert_partial(checkpoint)

    def test_raw_gap_alone_already_allows_reviewed_leaves_before_source_gate(self):
        self.configure()
        snapshot = replace(self.engine.capture_snapshot(), source_processing_evidence=(self.pending(),))
        snapshot, checkpoint, route = self.prepare(snapshot=snapshot)
        with self.assertRaisesRegex(ModelError, 'source processing'):
            self.run_scope(snapshot, checkpoint)
        self.assertEqual(set(checkpoint['overview']['accepted']), set(self.leaf_ids(route)))
        self.assert_partial(checkpoint)

    def test_first_group_source_failure_cannot_starve_later_subtree_leaf(self):
        self.configure(groups=True)
        snapshot = replace(self.engine.capture_snapshot(), source_processing_evidence=(self.pending(),))
        snapshot, checkpoint, route = self.prepare(snapshot=snapshot)
        with self.assertRaisesRegex(ModelError, 'source processing'):
            self.run_scope(snapshot, checkpoint)
        self.assertEqual(set(checkpoint['overview']['accepted']), set(self.leaf_ids(route)))

    def test_unassigned_cross_topic_exception_is_not_excluded_or_permission_waived(self):
        self.configure(rejected={'Voltage?'})
        self.themes = [('Voltage?', ['voltage']), ('Gate?', ['gate'])]
        self.store.append_edge(edge_id='condition', from_id='interface', to_id='voltage', kind='exception_to',
            recorded_at='2026-01-01', metadata={'condition': 'Both confirmations are required.'})
        snapshot, checkpoint, route = self.prepare()
        with self.assertRaises(ModelError):
            self.run_scope(snapshot, checkpoint)
        first, second = self.leaf_ids(route)
        self.assertIn(second, checkpoint['overview']['accepted'])
        self.assertNotIn(first, checkpoint['overview']['accepted'])
        writer = next(r for r in self.requests if r.task == 'Voltage?')
        self.assertIn('interface', {r['id'] for r in writer.records if r.get('id')})
        failure = self.engine.get_version(checkpoint['overview']['failed'][first]['version_id'])
        self.assertIn('condition', {r['id'] for r in failure.edges})

    def test_operational_provider_error_stops_before_independent_siblings(self):
        self.configure()
        snapshot, checkpoint, route = self.prepare()
        original = self.engine.model
        def unavailable(request):
            if request.task == 'Voltage?':
                raise ModelError('provider authorization unavailable')
            return original(request)
        self.engine.model = unavailable
        with self.assertRaisesRegex(ModelError, 'authorization'):
            self.run_scope(snapshot, checkpoint)
        self.assertFalse(any(r.task == 'Gate?' for r in self.requests))
        self.assertNotIn('failed', checkpoint['overview'])

    def bad_review_provider(self, output, *, repair=False, raised=None):
        original = self.engine.model
        def malformed(request):
            first = (request.task == 'Review grounded memory overview' and
                next(r['question'] for r in request.records if r.get('role') == 'query_context') == 'Voltage?')
            repaired = any(r.get('role') == 'review_contract_feedback' for r in request.records)
            if first and (not repair or repaired):
                # A returned bad body is distinct from an invocation that raises.
                self.requests.append(request)
                if raised is not None:
                    raise raised
                return output
            result = original(request)
            if first:
                value = json.loads(result)
                value['coverage'] = 'unsupported fixture coverage'
                return json.dumps(value)
            return result
        self.engine.model = malformed

    def test_acquired_initial_bad_review_bodies_fail_locally_without_extra_calls(self):
        outputs = ['not-json', '[]', None, 'x' * 60001, json.dumps({'status': 'accepted', 'reason': 'x' * 8001})]
        for output in outputs:
            with self.subTest(kind=type(output).__name__, size=len(output) if isinstance(output, str) else None):
                fixtures.BackgroundOverviewTest.setUp(self)
                self.configure()
                snapshot, checkpoint, route = self.prepare()
                self.bad_review_provider(output)
                with self.assertRaisesRegex(ModelError, 'required children'):
                    self.run_scope(snapshot, checkpoint)
                first, *later = self.leaf_ids(route)
                self.assertEqual(set(checkpoint['overview']['accepted']), set(later))
                failed = checkpoint['overview']['failed'][first]
                self.assertTrue(self.engine.get_version(failed['version_id']).overview_review['failure_proof'])
                calls = [r for r in self.requests if r.task == 'Review grounded memory overview' and
                    next(v['question'] for v in r.records if v.get('role') == 'query_context') == 'Voltage?']
                self.assertEqual(len(calls), 1)
                self.assert_partial(checkpoint)

    def test_acquired_malformed_repair_body_persists_failure_and_attempts_safe_siblings(self):
        self.configure()
        snapshot, checkpoint, route = self.prepare()
        self.bad_review_provider('not-json', repair=True)
        with self.assertRaisesRegex(ModelError, 'required children'):
            self.run_scope(snapshot, checkpoint)
        first, *later = self.leaf_ids(route)
        self.assertEqual(set(checkpoint['overview']['accepted']), set(later))
        self.assertIn(first, checkpoint['overview']['failed'])
        reviews = [r for r in self.requests if r.task == 'Review grounded memory overview' and
            next(v['question'] for v in r.records if v.get('role') == 'query_context') == 'Voltage?']
        self.assertEqual(len(reviews), 2, 'one initial response and the existing one repair only')
        self.assert_partial(checkpoint)

    def test_raised_repair_provider_auth_and_storage_errors_remain_fatal(self):
        for error in (ModelError('provider authorization unavailable'), OSError('cache unavailable'),
                      sqlite3.OperationalError('storage unavailable')):
            with self.subTest(error=type(error).__name__):
                fixtures.BackgroundOverviewTest.setUp(self)
                self.configure()
                snapshot, checkpoint, route = self.prepare()
                self.bad_review_provider(None, repair=True, raised=error)
                with self.assertRaisesRegex(ModelError, 'unavailable'):
                    self.run_scope(snapshot, checkpoint)
                self.assertFalse(any(r.task == 'Gate?' for r in self.requests))
                self.assertNotIn('failed', checkpoint['overview'])

    def test_review_request_preflight_stays_fatal_before_provider_invocation(self):
        from prototypes.assertion_memory.model import ModelRequest, SemanticUnavailable
        from prototypes.assertion_memory.overview_review import _review_contract
        calls = []
        request = ModelRequest('Review grounded memory overview', [{'text': 'x' * 60001}])
        with self.assertRaises(ModelError) as caught:
            _review_contract(lambda request: calls.append(request), request)
        self.assertNotIsInstance(caught.exception, SemanticUnavailable)
        self.assertEqual(calls, [])

    def test_malformed_body_with_failed_proof_write_cannot_continue_siblings(self):
        self.configure()
        snapshot, checkpoint, route = self.prepare()
        self.bad_review_provider('not-json')
        with patch('prototypes.assertion_memory.build_proof._write_failure', side_effect=sqlite3.OperationalError('disk unavailable')):
            with self.assertRaises(ModelError):
                self.run_scope(snapshot, checkpoint)
        self.assertFalse(any(r.task == 'Gate?' for r in self.requests))
        self.assertNotIn('failed', checkpoint['overview'])

    def test_failure_proof_persistence_error_aborts_sibling_pass(self):
        self.configure(rejected={'Voltage?'})
        snapshot, checkpoint, route = self.prepare()
        with patch('prototypes.assertion_memory.build_proof._write_failure', side_effect=sqlite3.OperationalError('disk unavailable')):
            with self.assertRaises(ModelError):
                self.run_scope(snapshot, checkpoint)
        self.assertFalse(any(r.task == 'Gate?' for r in self.requests))
        self.assertNotIn('failed', checkpoint['overview'])

    def test_checkpoint_restart_restores_accepted_leaf_and_failed_proof_then_finishes_siblings(self):
        self.configure(rejected={'Gate?'})
        provider, cache = self.engine.model, {}
        def exact_call_cache(request):
            key = (format_prompt(request), request.cache_context)
            if key not in cache:
                cache[key] = provider(request)
            return cache[key]
        self.engine.model = exact_call_cache
        snapshot, checkpoint, route = self.prepare()
        durable = []
        class Crash(BaseException):
            pass
        def save(value):
            durable.append(copy.deepcopy(value))
            if value['overview'].get('failed'):
                raise Crash()
        with self.assertRaises(Crash):
            self.run_scope(snapshot, checkpoint, save)
        resumed = json.loads(json.dumps(durable[-1]))
        first, second, third = self.leaf_ids(route)
        pinned = resumed['overview']['accepted'][first]
        failed_proof = self.engine.get_version(resumed['overview']['failed'][second]['version_id'])
        calls = sum(r.task == 'Voltage?' for r in self.requests)
        failed_calls = sum(r.task == 'Gate?' for r in self.requests)
        with self.assertRaises(ModelError):
            self.run_scope(snapshot, resumed)
        self.assertEqual(resumed['overview']['accepted'][first], pinned)
        self.assertEqual(sum(r.task == 'Voltage?' for r in self.requests), calls)
        self.assertEqual(sum(r.task == 'Gate?' for r in self.requests), failed_calls)
        self.assertEqual(self.engine.get_version(failed_proof.id), failed_proof)
        self.assertIn(third, resumed['overview']['accepted'])
        self.assertIn(second, resumed['overview']['failed'])

    def test_failed_child_skips_dependent_group_without_recursive_retry_of_leaf(self):
        self.configure(rejected={'Voltage?'}, groups=True)
        snapshot, checkpoint, route = self.prepare()
        with self.assertRaisesRegex(ModelError, 'required children'):
            self.run_scope(snapshot, checkpoint)
        groups = route['plan']['discovery']['hierarchy'][0]['groups']
        self.assertNotIn(groups[0]['view_id'], checkpoint['overview']['accepted'])
        self.assertIn(groups[1]['view_id'], checkpoint['overview']['accepted'])
        from prototypes.assertion_memory.overview_review import MAX_ROUNDS
        self.assertEqual(sum(r.task == 'Voltage?' for r in self.requests), MAX_ROUNDS)
        self.assert_partial(checkpoint)

    def test_stale_or_forged_work_manifest_stops_before_model_calls(self):
        self.configure()
        snapshot, checkpoint, route = self.prepare()
        before = len(self.requests)
        with self.assertRaises(ModelError):
            background._build(self.engine, replace(snapshot, source_version=999), route,
                              checkpoint['overview'], checkpoint, lambda v: None)
        forged = copy.deepcopy(route)
        forged['plan']['discovery']['themes'][0]['source_ids'] = ['outside']
        with self.assertRaises(ModelError):
            background._build(self.engine, snapshot, forged, checkpoint['overview'], checkpoint, lambda v: None)
        self.assertEqual(len(self.requests), before)

    def test_frozen_generation_ignores_new_arrival_and_next_frontier_is_distinct(self):
        self.configure(rejected={'Voltage?'})
        snapshot, checkpoint, route = self.prepare()
        self.store.append_assertion(assertion_id='new', text='Later reported prerequisite.', actor='Cy',
            source='fixture://new', episode='later', scope='s', recorded_at='2026-01-02')
        with self.assertRaises(ModelError):
            self.run_scope(snapshot, checkpoint)
        self.assertEqual(len(checkpoint['overview']['accepted']), 2)
        for saved in checkpoint['overview']['accepted'].values():
            from prototypes.assertion_memory.query_snapshot import receipt
            self.assertEqual(receipt(self.engine, saved['check_id'])['query_snapshot'], snapshot.descriptor())
            self.assertNotIn('new', {r['id'] for r in self.engine.get_version(saved['version_id']).evidence})
        self.assertNotEqual(background._identity(self.engine, 's', self.engine.capture_snapshot())[2], checkpoint['overview']['generation'])

    def test_healthy_complete_nav_keeps_one_generation_per_leaf_and_exact_resume(self):
        self.configure(groups=True)
        snapshot, checkpoint, route = self.prepare()
        result = self.run_scope(snapshot, checkpoint)
        self.assertEqual(checkpoint['overview']['stage'], 'complete')
        self.assertEqual(self.engine.get_version(result['version_id']).overview_review['assembly_coverage'], 'navigation_index')
        for q, _ in self.themes:
            self.assertEqual(sum(r.task == q for r in self.requests), 1)
        count = len(self.requests)
        self.assertEqual(self.run_scope(snapshot, checkpoint), result)
        self.assertEqual(len(self.requests), count)

    def test_scheduling_protocol_change_rearms_prior_terminal_generation(self):
        from prototypes.assertion_memory.derived_work import DerivedWorkQueue
        self.configure()
        self.themes = self.themes[:2]
        with patch.object(background, 'AUTOMATIC_LEAF_SCHEDULING_PROTOCOL', 'prior-terminal-scheduling', create=True):
            old = MemoryEngine(self.store, model=self.provider, model_key='independent-fixture', verify_overviews=True)
        queue = DerivedWorkQueue(self.store)
        claim = queue.claim(old, with_model=True)
        with self.assertRaises(ModelError) as caught:
            background.run_background_scope(old, 's', claim.snapshot, claim.checkpoint,
                                             lambda value: queue.save_checkpoint(claim, value))
        queue.release(claim, error=caught.exception)
        self.assertNotEqual(old.pipeline_key, self.engine.pipeline_key)
        next_claim = queue.claim(self.engine, with_model=True)
        self.assertNotIn('overview', next_claim.checkpoint)
        self.assertIsNone(queue.status('s')[0]['completed_frontier'])
