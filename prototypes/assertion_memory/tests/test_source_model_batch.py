"""Individual source requests share a generation call, retaining their responses."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

from prototypes.assertion_memory.model import ModelError, ModelRequest, processing_cancellation
from prototypes.assertion_memory.processing_pool import ActiveClaims
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.source_model_batch import SourceModelBatch
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_processing_selection import processing_cohort
from prototypes.assertion_memory.thought_processing import TracedThoughtWorker
from prototypes.assertion_memory.thought_store import ThoughtStore


class SourceModelBatchTest(unittest.TestCase):
    def test_two_sources_get_independent_drafts_from_one_call(self):
        calls = []
        batch = SourceModelBatch(2)
        def generate(request, bindings):
            calls.append((request, bindings))
            result = {row['source']: {'thoughts': [row['records'][0]['text']]}
                      for row in request.records}
            return json.dumps(result), 'batch-run'
        def construct(text):
            request = ModelRequest('Construct thought', [{'text': text}], 'shared rules',
                                   response_schema={'type': 'object'})
            return batch.submit(request, ({'event_id': text},), generate)
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(construct, ['first', 'second']))
        self.assertEqual([json.loads(result.body) for result in results],
                         [{'thoughts': ['first']}, {'thoughts': ['second']}])
        self.assertEqual({result.run_id for result in results}, {'batch-run'})
        self.assertEqual(len(calls), 1)
        self.assertEqual({row['event_id'] for row in calls[0][1]}, {'first', 'second'})

    def test_invalid_envelope_falls_back_instead_of_crossing_source_responses(self):
        batch = SourceModelBatch(2)
        request = ModelRequest('Construct thought', [], 'rules', response_schema={'type': 'object'})
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: batch.submit(request, (), lambda *_: ('{}', 'run')), range(2)))
        self.assertEqual(results, [None, None])

    def test_worker_publishes_and_verifies_each_source_independently(self):
        self.check_worker_results(use_group=False)

    def test_explicit_group_exceeds_lane_count_and_leaves_other_jobs_pending(self):
        self.check_worker_results(use_group=True)

    def test_group_error_releases_all_claims_without_publishing(self):
        store = self.enterContext(MemoryStore(':memory:'))
        queue = CaptureQueue(store)
        thoughts = ThoughtStore(store)
        events = [queue.capture_event('chosen', text, text, source='fixture') for text in ('One', 'Two')]
        worker = TracedThoughtWorker(queue, ThoughtEngine(thoughts, lambda _: '{}'), lock_owned=True,
                                     processing_session_ids=('chosen',))
        with self.assertRaisesRegex(ModelError, 'expected 2 labeled results'):
            worker.process_source_group([event.id for event in events], limit=2)
        for event in events:
            job = queue.get(event.id)
            self.assertEqual((job.status, job.attempts, job.extracted_ids), ('pending', 0, []))
            self.assertIn('expected 2 labeled results', job.error)

    def test_group_can_recover_expired_selected_claim(self):
        store = self.enterContext(MemoryStore(':memory:'))
        queue = CaptureQueue(store)
        worker = TracedThoughtWorker(queue, ThoughtEngine(ThoughtStore(store), lambda _: '{}'),
                                     lock_owned=True, processing_session_ids=('chosen',))
        selected = queue.capture_event('chosen', 'selected', 'Selected', source='fixture')
        other = queue.capture_event('chosen', 'other', 'Other', source='fixture')
        old_claim = worker._claim_source(event_id=selected.id)
        with store.transaction(write=True) as db:
            db.execute('UPDATE memory_capture_jobs SET lease_until=? WHERE id=?', (queue._time(-1), old_claim.job.id))
        with self.assertRaisesRegex(ModelError, 'expected 1 labeled results'):
            worker.process_source_group([selected.id], limit=1)
        self.assertEqual(queue.get(selected.id).status, 'pending')
        self.assertEqual(queue.get(other.id).attempts, 0)

    def test_group_cancellation_keeps_completed_sources(self):
        self.check_worker_results(use_group=True, cancel_after_first=True)

    def check_worker_results(self, *, use_group, cancel_after_first=False):
        path = Path(self.enterContext(TemporaryDirectory())) / 'memory.sqlite3'
        store = self.enterContext(MemoryStore(path))
        queue = CaptureQueue(store)
        ThoughtStore(store)
        events = [queue.capture_event('chosen', text, text, source='fixture')
                  for text in ('Kettle boils water.', 'Oven bakes bread.')]
        if use_group:
            events.append(queue.capture_event('chosen', 'fridge', 'Fridge keeps food cold.', source='fixture'))
        other = queue.capture_event('chosen' if use_group else 'other', 'message', 'Other work.', source='fixture')
        untouched = queue.get(other.id)
        tasks = []
        batch = SourceModelBatch(2)
        did_renew = Event()

        def model(request):
            tasks.append(request.task)
            if request.task == 'Construct thoughts for multiple source messages':
                if use_group:
                    with MemoryStore(path) as reader:
                        queue_reader = CaptureQueue(reader)
                        self.assertEqual([queue_reader.get(event.id).status for event in events],
                                         ['running'] * len(events))
                    self.assertTrue(did_renew.wait(2), 'the group did not renew its claims during generation')
                response = {}
                catalog = request.records[0].get('fragment_texts')
                cases = request.records[1:] if catalog is not None else request.records
                for case in cases:
                    record = next(row for row in case['records'] if row['role'] == 'source')
                    fragment = record['fragments'][0]
                    response[case['source']] = {'thoughts': [{
                        'text': (catalog[fragment['text_index']] if catalog is not None else fragment['text']),
                        'question': None, 'support_fragments': [fragment['option']],
                    }], 'needs_detail': None}
                return json.dumps(response)
            self.assertEqual(request.task, 'Verify thought')
            return json.dumps({'verdict': 'accept', 'text': 'Matches the source.'})

        def run(_):
            with MemoryStore(path) as local:
                engine = ThoughtEngine(ThoughtStore(local), model)
                worker = TracedThoughtWorker(CaptureQueue(local), engine, lane='source', lock_owned=True,
                                             processing_session_ids=('chosen',), source_batch=batch)
                return worker.run_once()

        if use_group:
            engine = ThoughtEngine(ThoughtStore(store), model)
            worker = TracedThoughtWorker(queue, engine, lock_owned=True,
                                         processing_session_ids=('chosen',), is_pausing=lambda: False)
            unrelated = engine.thoughts.publish('Previously queued overview')
            with processing_cohort(('chosen',)):
                worker.work.enqueue([unrelated])
            renew = ActiveClaims.renew

            def renew_group(active, db, **kwargs):
                count = renew(active, db, **kwargs)
                if count == len(events):
                    did_renew.set()
                return count

            with (processing_cancellation(lambda: cancel_after_first and
                                          queue.get(events[0].id).status == 'completed'),
                  patch.object(ActiveClaims, 'renew', renew_group),
                  patch('prototypes.assertion_memory.thought_processing.GROUP_HEARTBEAT_SECONDS', 0.01),
                  patch.object(worker, '_generalize') as generalize):
                worker.process_source_group([event.id for event in events], limit=2 * len(events) + 1)
            self.assertEqual(worker.work.get(unrelated.id)['attempts'], 0)
            if cancel_after_first:
                self.assertEqual([queue.get(event.id).status for event in events],
                                 ['completed', 'pending', 'pending'])
                self.assertEqual([queue.get(event.id).attempts for event in events], [1, 0, 0])
                self.assertEqual(queue.get(other.id), untouched)
                generalize.assert_not_called()
                return
            self.assertEqual(generalize.call_count, len(events))
            jobs = [queue.get(event.id) for event in events]
        else:
            with ThreadPoolExecutor(2) as pool:
                jobs = list(pool.map(run, range(2)))
        self.assertEqual({job.event_id for job in jobs}, {event.id for event in events})
        self.assertTrue(all(job.status == 'completed' for job in jobs))
        self.assertCountEqual(tasks, ['Construct thoughts for multiple source messages'] + ['Verify thought'] * len(events))
        thoughts = ThoughtStore(store)
        for event in events:
            job = queue.get(event.id)
            thought = thoughts.get(job.extracted_ids[0])
            self.assertEqual(thought.text, event.prompt)
            self.assertEqual({basis['event_id'] for basis in thought.bases}, {event.id})
        self.assertEqual(queue.get(other.id), untouched)
