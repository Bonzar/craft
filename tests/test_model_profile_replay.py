"""Model rollout cannot silently count a previous model's checkpoint as new work."""
import json
import tempfile
import unittest
from pathlib import Path
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.model import ModelRequest
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.thought_worker import ThoughtWorker
from prototypes.assertion_memory.thought_trace import ThoughtTrace
from prototypes.assertion_memory.thought_service import traced_engine
from prototypes.assertion_memory.thought_query_replay import query_replay
from prototypes.assertion_memory.tests.test_thought_worker import Engine


class Provider:
    def __init__(self,profile):
        self.route_profile_key=profile
        self.requested_model='fixture-model'
        self.requests=[]
    def __call__(self,request):
        self.requests.append(request)
        return json.dumps({'verdict':'accept','text':'Fixture approved.'})


class ModelProfileReplayTest(unittest.TestCase):
    def test_worker_reuses_consumed_valid_steps_across_model_changes(self):
        for change in (False,True):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as directory,MemoryStore(Path(directory)/'memory.sqlite') as memory:
                queue=CaptureQueue(memory);store=ThoughtStore(memory);engine=Engine(store)
                provider=Provider('a'*64);engine.model=provider
                event=queue.capture_event('s','m','Only after approval.',source='fixture')
                original=engine.ingest
                def fail_after_consumed(event,context=(),*,consolidate=False):
                    for task in ('fixture','later-step'):
                        request=ModelRequest(task,[{'text':event.prompt}]);engine.gateway(task,request,({'event_id':event.id},))
                    raise RuntimeError('interrupted after first step consumed')
                engine.ingest=fail_after_consumed
                self.assertEqual(ThoughtWorker(queue,engine,lane='source').run_once().status,'pending')
                if change:provider.route_profile_key='b'*64
                engine.ingest=original;queue.retry(event.id)
                self.assertEqual(ThoughtWorker(queue,engine,lane='source').run_once().status,'completed')
                self.assertEqual(len(provider.requests),2)
                reused=[row for row in engine.events if row['stage']=='model_step_reused']
                self.assertEqual(len(reused),1)
                self.assertIsNone(reused[0]['source_call_id'])
                self.assertEqual(reused[0]['cached_bindings'],[{'event_id':event.id}])

    def test_query_reuses_consumed_valid_steps_across_model_changes(self):
        for change in (False,True):
            with self.subTest(change=change),MemoryStore(':memory:') as memory:
                queue=CaptureQueue(memory);store=ThoughtStore(memory)
                from prototypes.assertion_memory.thought_worker_queue import ThoughtWorkQueue
                ThoughtWorkQueue(queue)
                provider=Provider('a'*64);selection={'question':'Fixture question'}
                first=ThoughtTrace(memory,'query',selection);engine=traced_engine(store,provider,None,first)
                with self.assertRaises(RuntimeError):
                    with query_replay(engine,first,selection):
                        engine._call('Verify thought',[{'text':'First'}],[],'Fixture')
                        engine._call('Verify source thought batch',[{'text':'Second'}],[],'Fixture')
                        raise RuntimeError('interrupted after first consumed step')
                first.finish('failed')
                if change:provider.route_profile_key='b'*64
                second=ThoughtTrace(memory,'query',selection);engine=traced_engine(store,provider,None,second)
                with query_replay(engine,second,selection):engine._call('Verify thought',[{'text':'First'}],[],'Fixture')
                second.finish()
                self.assertEqual(len(provider.requests),2)


if __name__=='__main__':unittest.main()
