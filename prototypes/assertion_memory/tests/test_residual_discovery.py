"""Cap pressure allows bounded discovery on the exact residual, never forced membership."""
import copy
import json
import unittest

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import ModelError, format_prompt
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.topic_discovery import discover


class ResidualDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        for index in range(17):
            self.store.append_assertion(assertion_id=f'r{index:02}', text=f'Attributed fixture condition {index}.',
                actor='Cy', source=f'fixture://{index}', episode='fixture', scope='s', recorded_at='2026-01-01')
        self.ids = [f'r{index:02}' for index in range(17)]
        self.responses, self.calls = [], []
        self.engine = MemoryEngine(self.store, model=self.model, model_key='fixture', verify_overviews=True)
        self.spec = ViewSpec('fixture-parent', 'Parent', 'What is known?', scopes=('s',))
        self.request = {'question': self.spec.query, 'scopes': ['s'], 'valid_as_of': None, 'recorded_as_of': None}
        self.snapshot = self.engine.capture_snapshot()

    def model(self, request):
        self.assertLessEqual(len(format_prompt(request)), 60000)
        if request.task == 'Discover reusable memory themes':
            ids = [row['id'] for row in request.records if row.get('role') == 'source']
            self.calls.append(ids)
            self.assertTrue(self.responses, 'unexpected extra discovery call')
            response = self.responses.pop(0)
            if callable(response):
                response = response(ids)
            return json.dumps(response)
        if request.task == 'Plan memory retrieval for a question':
            match = next((row['id'] for row in request.records if row.get('role') == 'view'
                          and row['question'] == request.records[0]['question']), None)
            return json.dumps({'reuse_view_id': match, 'related_view_ids': [], 'subquestions': [], 'reason': 'Equivalent only.'})
        # Known leaves now run before genuine residual discovery refuses NAV.
        if request.task.startswith('Question ') and request.task.endswith('?'):
            return '\n'.join(row['text'] + ' [source:' + row['id'] + ']' for row in request.records
                             if row.get('id') and row.get('text'))
        if request.task == 'Review grounded memory overview':
            return json.dumps({'status': 'accepted', 'coverage': 'all_claims', 'reason': 'Exact synthetic sources.',
                               'issues': [], 'context_requests': [], 'unknown_needs': []})
        self.fail('Unexpected task: ' + request.task)

    def response(self, groups, unknown):
        return {'themes': [{'question': question, 'source_ids': ids} for question, ids in groups],
                'unassigned_source_ids': unknown}

    def initial(self):
        return self.response([(f'Question {n}?', self.ids[n * 3:(n + 1) * 3] + ([self.ids[12]] if n == 3 else []))
                              for n in range(4)], self.ids[13:])

    def run_discovery(self, progress=None, save=None, snapshot=None):
        return discover(self.engine, self.spec, self.request, snapshot or self.snapshot,
                        progress=progress, save_progress=save)

    def test_cap_saturated_packet_discovers_six_themes_and_preserves_original_audit(self):
        first = self.initial()
        self.responses = [first, self.response([('Question 4?', self.ids[13:15]), ('Question 5?', self.ids[15:])], [])]
        _, manifest = self.run_discovery()
        self.assertEqual(self.calls, [self.ids, self.ids[13:]])
        self.assertEqual(len(manifest['themes']), 6)
        self.assertEqual(manifest['unassigned_source_ids'], [])
        self.assertEqual(manifest['batches'][0]['unassigned_source_ids'], first['unassigned_source_ids'])
        self.assertEqual(manifest['source_count'], 17)
        self.assertEqual(manifest['membership_acquisitions'], [])
        self.assertEqual(manifest['residual_discoveries'][0]['status'], 'resolved')

    def test_true_ambiguity_stops_immediately_without_fabricating_membership(self):
        self.responses = [self.initial(), self.response([], self.ids[13:])]
        _, manifest = self.run_discovery()
        self.assertEqual(self.calls, [self.ids, self.ids[13:]])
        self.assertEqual(len(manifest['themes']), 4)
        self.assertEqual(manifest['unassigned_source_ids'], self.ids[13:])
        self.assertEqual(manifest['residual_discoveries'][0]['status'], 'no_progress')

    def test_unsaturated_initial_ambiguity_has_no_residual_retries(self):
        self.responses = [self.response([('One question?', self.ids[:13])], self.ids[13:])]
        _, manifest = self.run_discovery()
        self.assertEqual(self.calls, [self.ids])
        self.assertEqual(manifest['unassigned_source_ids'], self.ids[13:])
        self.assertEqual(manifest['residual_discoveries'], [])

    def test_two_pass_budget_persists_on_ready_restart(self):
        self.responses = [self.initial(), self.response([('Next?', self.ids[13:14])], self.ids[14:]),
                          self.response([('Last?', self.ids[14:15])], self.ids[15:])]
        progress = {}
        _, manifest = self.run_discovery(progress)
        self.assertEqual(self.calls, [self.ids, self.ids[13:], self.ids[14:]])
        self.assertEqual(manifest['unassigned_source_ids'], self.ids[15:])
        receipt = manifest['residual_discoveries'][0]
        self.assertEqual(receipt['status'], 'exhausted')
        self.assertEqual(len(receipt['passes']), 2)
        _, resumed = self.run_discovery(json.loads(json.dumps(progress)))
        self.assertEqual(resumed, manifest)
        self.assertEqual(len(self.calls), 3)

    def test_checkpoint_crashes_do_not_repeat_accepted_responses_or_merge_effects(self):
        class Crash(BaseException):
            pass
        for boundary in ('reserved', 'response', 'merged', 'completed'):
            with self.subTest(boundary=boundary):
                self.calls = []
                self.responses = [self.initial(), self.response([('Question 4?', self.ids[13:15]),
                                                               ('Question 5?', self.ids[15:])], [])]
                progress, durable, crashed = {}, {}, []
                def save(value):
                    durable.clear()
                    durable.update(copy.deepcopy(value))
                    residual = value.get('batch', {}).get('residual', {})
                    passes = residual.get('passes', [])
                    if not passes or crashed:
                        return
                    work = passes[-1]
                    hit = {'reserved': 'themes' not in work,
                           'response': 'themes' in work and work['merge_cursor'] == 0,
                           'merged': work.get('merge_cursor') == 1,
                           'completed': residual.get('status') == 'resolved'}[boundary]
                    if hit:
                        crashed.append(True)
                        raise Crash()
                with self.assertRaises(Crash):
                    self.run_discovery(progress, save)
                _, manifest = self.run_discovery(json.loads(json.dumps(durable)))
                self.assertEqual(self.calls, [self.ids, self.ids[13:]])
                self.assertEqual(len(manifest['themes']), 6)
                self.assertEqual(manifest['unassigned_source_ids'], [])

    def test_residual_many_to_many_is_union_accounted_once(self):
        self.responses = [self.initial(), self.response([('Question 4?', self.ids[13:16]),
                                                       ('Question 5?', self.ids[15:])], [])]
        _, manifest = self.run_discovery()
        memberships = [set(row['source_ids']) for row in manifest['themes']]
        self.assertEqual(set.union(*memberships), set(self.ids))
        self.assertEqual(sum(self.ids[15] in row for row in memberships), 2)
        self.assertEqual(manifest['source_count'], 17)

    def test_residual_rejects_unknown_or_assigned_and_unassigned_overlap(self):
        for value in (self.response([('Invalid?', ['outside'])], self.ids[13:]),
                      self.response([('Invalid?', [self.ids[13]])], self.ids[13:])):
            with self.subTest(value=value):
                self.responses = [self.initial(), value]
                with self.assertRaises(ModelError):
                    self.run_discovery()

    def test_frozen_residual_does_not_absorb_sources_arriving_between_calls(self):
        def residual(ids):
            self.store.append_assertion(assertion_id='new', text='Later unrelated condition.', actor='Cy',
                source='fixture://new', episode='later', scope='s', recorded_at='2026-01-02')
            return self.response([('Question 4?', ids)], [])
        self.responses = [self.initial(), residual]
        _, manifest = self.run_discovery()
        self.assertEqual(manifest['snapshot'], self.snapshot.descriptor())
        self.assertEqual(set(manifest['source_fingerprints']), set(self.ids))
        self.assertNotIn('new', self.calls[-1])

    def test_old_progress_protocol_cannot_skip_new_residual_work(self):
        progress = {}
        self.responses = [self.response([('One?', self.ids)], [])]
        self.run_discovery(progress)
        old = copy.deepcopy(progress)
        old['protocol'] = 'old-discovery'
        self.responses = [self.initial(), self.response([('Question 4?', self.ids[13:])], [])]
        _, manifest = self.run_discovery(old)
        self.assertEqual(self.calls[-2:], [self.ids, self.ids[13:]])
        self.assertEqual(manifest['unassigned_source_ids'], [])

    def test_real_unknown_blocks_full_navigation_and_keeps_exact_partial_catalog(self):
        from prototypes.assertion_memory.background_overviews import run_background_scope, navigation_context, registered_navigation_membership
        self.responses = [self.initial(), self.response([], self.ids[13:])]
        checkpoint = {}
        with self.assertRaisesRegex(ModelError, 'unassigned') as caught:
            run_background_scope(self.engine, 's', self.snapshot, checkpoint, lambda value: None)
        self.assertTrue(caught.exception.terminal_discovery)
        with self.store.transaction() as connection:
            row = connection.execute('SELECT *,rowid AS route_watermark FROM memory_question_route_versions ORDER BY rowid DESC LIMIT 1').fetchone()
        catalog = registered_navigation_membership(self.engine, row)
        self.assertEqual(catalog['registered_canonical_count'], 13)
        self.assertEqual(catalog['unassigned_canonical_count'], 4)
        with self.assertRaisesRegex(ModelError, 'unassigned'):
            navigation_context(self.engine, self.engine._spec(row['view_id']), self.snapshot)
        before = len(self.calls)
        with self.assertRaisesRegex(ModelError, 'unassigned'):
            run_background_scope(self.engine, 's', self.snapshot, json.loads(json.dumps(checkpoint)), lambda value: None)
        self.assertEqual(len(self.calls), before)

    def test_terminal_discovery_stops_scheduler_retry_without_completing_frontier(self):
        from prototypes.assertion_memory.derived_work import DerivedWorkQueue
        from prototypes.assertion_memory.background_overviews import run_background_scope
        self.responses = [self.initial(), self.response([], self.ids[13:])]
        from datetime import datetime, timedelta, timezone
        now = [datetime(2026, 11, 13, tzinfo=timezone.utc)]
        queue = DerivedWorkQueue(self.store, clock=lambda: now[0])
        claim = queue.claim(self.engine, with_model=True)
        with self.assertRaises(ModelError) as caught:
            run_background_scope(self.engine, 's', claim.snapshot, claim.checkpoint,
                                 lambda value: queue.save_checkpoint(claim, value))
        queue.release(claim, error=caught.exception)
        now[0] += timedelta(seconds=3)
        self.assertIsNone(queue.claim(self.engine, with_model=True))
        self.assertIsNone(queue.seconds_until_work(with_model=True))
        with self.store.transaction() as connection:
            self.assertEqual(connection.execute('SELECT error_count FROM memory_derived_work').fetchone()[0], 1)
        status = queue.status('s')[0]
        self.assertIsNone(status['completed_frontier'])
        self.assertIn('unassigned', status['error'])
        self.store.append_assertion(assertion_id='new', text='A later independent condition.', actor='Cy',
            source='fixture://new', episode='later', scope='s', recorded_at='2026-01-02')
        # SQLite's insertion trigger schedules on its wall clock. Advance the
        # injected queue clock to that exact boundary before checking rearming.
        with self.store.transaction() as connection:
            ready_at = connection.execute('SELECT available_at FROM memory_derived_work WHERE scope=?',
                                          ('s',)).fetchone()[0]
        now[0] = max(now[0], datetime.fromisoformat(ready_at))
        next_claim = queue.claim(self.engine, with_model=True)
        self.assertIsNotNone(next_claim)
        self.assertIn('new', next_claim.snapshot.assertions)
        self.assertNotIn('overview', next_claim.checkpoint)

    def test_terminal_failure_rearms_if_source_arrived_during_the_leased_generation(self):
        from prototypes.assertion_memory.derived_work import DerivedWorkQueue
        from prototypes.assertion_memory.background_overviews import run_background_scope
        queue = DerivedWorkQueue(self.store)
        claim = queue.claim(self.engine, with_model=True)
        self.store.append_assertion(assertion_id='new', text='New independently grounded condition.', actor='Cy',
            source='fixture://new', episode='later', scope='s', recorded_at='2026-01-02')
        self.responses = [self.initial(), self.response([], self.ids[13:])]
        with self.assertRaises(ModelError) as caught:
            run_background_scope(self.engine, 's', claim.snapshot, claim.checkpoint,
                                 lambda value: queue.save_checkpoint(claim, value))
        released = queue.release(claim, error=caught.exception)
        self.assertEqual(released.status, 'pending')
        next_claim = queue.claim(self.engine, with_model=True)
        self.assertIn('new', next_claim.snapshot.assertions)
        self.assertNotIn('overview', next_claim.checkpoint)

    def test_cached_provider_response_survives_crash_before_residual_checkpoint(self):
        from prototypes.assertion_memory.derived_work import DerivedYield, DerivedWorkQueue
        from prototypes.assertion_memory.queue import _identity, CaptureQueue
        source_queue = CaptureQueue(self.store)
        queue = DerivedWorkQueue(self.store)
        claim = queue.claim(self.engine, with_model=True)
        self.responses = [self.initial(), self.response([('Question 4?', self.ids[13:])], [])]
        provider = self.model
        crashed = []
        def cached(request):
            key = _identity('refresh-generation', self.engine.pipeline_key, format_prompt(request))
            response = source_queue._refresh_generation(key)
            if response is not None:
                return response
            response = provider(request)
            queue.save_response(claim, key, response)
            if request.task == 'Discover reusable memory themes' and len(self.calls) == 2 and not crashed:
                crashed.append(True)
                raise DerivedYield()
            return response
        self.engine.model = cached
        with self.assertRaises(DerivedYield):
            self.run_discovery(claim.checkpoint.setdefault('discovery', {}),
                               lambda value: queue.save_checkpoint(claim, claim.checkpoint), snapshot=claim.snapshot)
        queue.release(claim)
        claim = queue.claim(self.engine, with_model=True)
        _, manifest = self.run_discovery(claim.checkpoint['discovery'], snapshot=claim.snapshot)
        self.assertEqual(self.calls, [self.ids, self.ids[13:]])
        self.assertEqual(manifest['unassigned_source_ids'], [])
        self.assertEqual(len(manifest['residual_discoveries'][0]['passes']), 1)

    def test_residual_assignment_preserves_independently_unresolved_old_memberships(self):
        self.responses = [self.response([('Previous?', self.ids)], [])]
        _, previous = self.run_discovery()
        old = copy.deepcopy(previous)
        self.ids = [f'z{index:02}' for index in range(17)]
        for key in self.ids:
            self.store.append_assertion(assertion_id=key, text='New attributed fixture condition.', actor='Cy',
                source='fixture://' + key, episode='new', scope='s', recorded_at='2026-01-02')
        self.responses = [self.initial(), self.response([('Question 4?', self.ids[13:])], [])]
        provider = self.model
        def membership(request):
            if request.task == 'Review thematic source membership':
                ids = [row['id'] for row in request.records if row.get('role') == 'candidate']
                return json.dumps({'source_ids': [], 'unresolved_source_ids': ids[:1],
                                   'reason': 'The separate earlier episode remains ambiguous for this question.'})
            return provider(request)
        self.engine.model = membership
        _, manifest = discover(self.engine, self.spec, self.request, self.engine.capture_snapshot(), previous=previous)
        self.assertEqual(manifest['unassigned_source_ids'], [])
        self.assertTrue(any(row['unresolved_source_ids'] for row in manifest['membership_acquisitions']))
        self.assertEqual(previous, old)

    def test_reserved_second_pass_keeps_total_budget_across_restart(self):
        class Crash(BaseException):
            pass
        self.responses = [self.initial(), self.response([('Next?', self.ids[13:14])], self.ids[14:]),
                          self.response([('Last?', self.ids[14:15])], self.ids[15:])]
        durable = {}
        def save(value):
            durable.clear()
            durable.update(copy.deepcopy(value))
            passes = value.get('batch', {}).get('residual', {}).get('passes', [])
            if len(passes) == 2 and 'themes' not in passes[-1]:
                raise Crash()
        with self.assertRaises(Crash):
            self.run_discovery({}, save)
        _, manifest = self.run_discovery(json.loads(json.dumps(durable)))
        self.assertEqual(self.calls, [self.ids, self.ids[13:], self.ids[14:]])
        receipt = manifest['residual_discoveries'][0]
        self.assertEqual(receipt['status'], 'exhausted')
        self.assertEqual(receipt['remaining_ids'], self.ids[15:])
        self.assertEqual(len(receipt['passes']), 2)

    def test_protocol_change_rearms_terminal_generation_with_fresh_checkpoint(self):
        from prototypes.assertion_memory.derived_work import DerivedWorkQueue
        from prototypes.assertion_memory.background_overviews import run_background_scope
        queue = DerivedWorkQueue(self.store)
        claim = queue.claim(self.engine, with_model=True)
        self.responses = [self.initial(), self.response([], self.ids[13:])]
        with self.assertRaises(ModelError) as caught:
            run_background_scope(self.engine, 's', claim.snapshot, claim.checkpoint,
                                 lambda value: queue.save_checkpoint(claim, value))
        queue.release(claim, error=caught.exception)
        changed = MemoryEngine(self.store, model=self.model, model_key='next-protocol', verify_overviews=True)
        next_claim = queue.claim(changed, with_model=True)
        self.assertEqual(next_claim.frontier['pipeline_key'], changed.pipeline_key)
        self.assertNotIn('overview', next_claim.checkpoint)
        self.assertIsNone(queue.status('s')[0]['completed_frontier'])
