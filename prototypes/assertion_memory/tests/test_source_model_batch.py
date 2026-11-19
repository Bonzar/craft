"""Individual source requests share a generation call, retaining their responses."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from prototypes.assertion_memory.model import ModelRequest
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.source_model_batch import SourceModelBatch
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
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

    def check_worker_results(self, *, use_group):
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

        def model(request):
            tasks.append(request.task)
            if request.task == 'Construct thoughts for multiple source messages':
                response = {}
                for case in request.records:
                    record = next(row for row in case['records'] if row['role'] == 'source')
                    fragment = record['fragments'][0]
                    response[case['source']] = {'thoughts': [{
                        'text': fragment['text'], 'question': None, 'support_fragments': [fragment['option']],
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
            worker.process_source_group([event.id for event in events], limit=len(events))
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
