"""Only changed grounds or confirmed new material refresh an existing thought."""
import json
import unittest
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.thought_worker import ThoughtWorker


class ThoughtChangePropagationTest(unittest.TestCase):
    def test_search_candidates_require_semantic_confirmation_in_existing_model_call(self):
        with MemoryStore(':memory:') as memory:
            store, queue = ThoughtStore(memory), CaptureQueue(memory)
            event = queue.capture_event('s','user','Allow a short swipe only in the deck.',source='fixture')
            new = store.publish(event.prompt, origin={'event_id':event.id, 'kind':'source_interpretation'},
                                bases=[{'event_id':event.id}])
            relevant = store.publish('A swipe is available.',question='When is a short swipe allowed?')
            unrelated = store.publish('Use male gender when speaking.',question='How should the assistant speak?')
            calls = []
            def model(request):
                calls.append(request.task)
                options = [record for record in request.records if 'refresh_option' in record]
                selected = [record['refresh_option'] for record in options if record['text']==relevant.text]
                return json.dumps({'question':None,'text':'Only the swipe condition changed.',
                                   'affected_candidates':selected})
            engine = ThoughtEngine(store,model)
            engine.retrieve = lambda *args,**kwargs: [new,relevant,unrelated]
            worker = ThoughtWorker(queue,engine,lane='maintenance')
            worker.enqueue_changed([new])
            self.assertEqual(worker.run_once()['status'],'completed')
            self.assertEqual(worker.work.get(relevant.id)['action'],'refresh')
            self.assertIsNone(worker.work.get(unrelated.id))
            self.assertEqual(calls,['Find useful shared thought question'])

    def test_same_known_basis_and_old_unconfirmed_search_job_cost_no_model_calls(self):
        with MemoryStore(':memory:') as memory:
            store, queue = ThoughtStore(memory), CaptureQueue(memory)
            child = store.publish('Current child.')
            parent = store.publish('Current parent.',question='Current facts?',
                                   bases=[{'thought_id':child.id,'version':child.version}])
            unrelated = store.publish('Unrelated answer.',question='Unrelated question?')
            engine = ThoughtEngine(store,lambda _: self.fail('unconfirmed maintenance made a model call'))
            engine.retrieve = lambda *args,**kwargs: [unrelated]
            worker = ThoughtWorker(queue,engine,lane='maintenance')
            worker.enqueue_dependents([child])
            self.assertIsNone(worker.work.get(parent.id))
            self.assertIsNone(worker.work.get(unrelated.id))
            worker.work.enqueue_refresh(unrelated,(child.id,child.version))
            self.assertEqual(worker.run_once()['status'],'completed')
