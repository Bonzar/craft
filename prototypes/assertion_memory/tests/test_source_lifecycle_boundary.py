"""Characterization of exact source-job outcomes before replacing the extractor seam."""
import json
from pathlib import Path
import tempfile
import unittest

from prototypes.assertion_memory.model import ModelError
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.thought_worker import ThoughtWorker
from prototypes.assertion_memory.tests.prepared_support_helpers import drafts


class SourceLifecycleTest(unittest.TestCase):
    def test_partially_verified_source_survives_reopen_without_duplicate_or_rewrite(self):
        first='Первый вымышленный факт.'
        second='Второй вымышленный факт.'
        failed=True
        calls=[]
        def provider(request):
            calls.append(request.task)
            if request.task=='Construct thought':
                repair=any(row['role']=='accepted_coverage' for row in request.records)
                if repair and failed:
                    raise ModelError('temporary fixture error after accepting part of the source')
                return json.dumps(drafts(request,(second,[second])) if repair else
                                  drafts(request,(first,[first]),(second,[second])))
            rejected=[{'draft':1,'text':'Recheck the second clearly distinct fact.'}] if failed else []
            result=dict(verdict='reject' if rejected else 'accept', text='Review fictional facts.',
                       rejected_drafts=rejected,missing_meaning=None)
            if 'open_support' in request.response_schema['properties']:result['open_support']=None
            return json.dumps(result)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'fixture.sqlite'
            with MemoryStore(path) as db:
                queue=CaptureQueue(db);thoughts=ThoughtStore(db)
                raw=queue.capture_event('fixture','m',first+' '+second,source='fixture')
                worker=ThoughtWorker(queue,ThoughtEngine(thoughts,provider),lane='source')
                partial=worker.run_once()
                self.assertEqual((partial.status,partial.phase),('pending','partial_source'),partial.error)
                kept,=thoughts.list_thoughts()
                self.assertEqual(kept.text,first)
                self.assertEqual(partial.extracted_ids,[kept.id])
                with db.transaction() as c:
                    saved=c.execute('SELECT progress FROM memory_source_draft_checkpoints WHERE job_id=?',(raw.id,)).fetchone()
                    self.assertEqual(json.loads(saved[0])['accepted'][0]['thought_id'],kept.id)
            failed=False
            with MemoryStore(path) as db:
                queue=CaptureQueue(db);thoughts=ThoughtStore(db);queue.retry(raw.id)
                done=ThoughtWorker(queue,ThoughtEngine(thoughts,provider),lane='source').run_once()
                self.assertEqual((done.status,done.phase),('completed','done'),done.error)
                self.assertEqual(done.extracted_ids[0],kept.id)
                self.assertEqual({row.text for row in thoughts.list_thoughts()},{first,second})
                self.assertEqual(thoughts.get(kept.id),kept)
                self.assertEqual(len(done.extracted_ids),2)
                with db.transaction() as c:
                    self.assertIsNone(c.execute('SELECT progress FROM memory_source_draft_checkpoints WHERE job_id=?',(raw.id,)).fetchone())
            self.assertEqual(calls,['Construct thought','Verify thought','Construct thought',
                                   'Construct thought','Verify thought'])

    def test_existing_exact_source_replays_without_inference(self):
        calls=[]
        with MemoryStore(':memory:') as db:
            queue=CaptureQueue(db);thoughts=ThoughtStore(db)
            raw=queue.capture_event('fixture','m','Факт.',source='fixture')
            def provider(request):
                calls.append(request.task)
                if request.task=='Construct thought':
                    return json.dumps(drafts(request,('Факт.',[raw.prompt])))
                return json.dumps({'verdict':'accept','text':'Matches.'})
            engine=ThoughtEngine(thoughts,provider)
            original,=engine.ingest(raw,consolidate=False)
            # The worker-runner normally initializes scheduler/replay tables;
            # this fixture creates only a bare Queue + direct ingest.
            with db.transaction(write=True) as c:queue._scheduler_schema(c)
            before=list(calls)
            done=ThoughtWorker(queue,engine,lane='source').run_once()
            self.assertEqual(done.extracted_ids,[original.id],done.error)
            self.assertEqual(thoughts.list_thoughts(),[original])
            self.assertEqual(calls,before)
    def test_custom_extractor_receives_active_engine_but_worker_owns_publication(self):
        from prototypes.assertion_memory.source_extraction import CanonicalSourceExtractor
        observations=[]
        with MemoryStore(':memory:') as db:
            queue=CaptureQueue(db);thoughts=ThoughtStore(db)
            old=queue.capture_event('fixture','old','Earlier fact.',source='fixture')
            raw=queue.capture_event('fixture','new','New fact.',source='fixture')
            def provider(request):
                if request.task=='Construct thought':
                    return json.dumps(drafts(request,(raw.prompt,[raw.prompt])))
                outcome={'verdict':'accept','text':'Matches fixture.'}
                if 'open_support' in request.response_schema['properties']:outcome['open_support']=None
                return json.dumps(outcome)
            engine=ThoughtEngine(thoughts,provider)
            class FixtureExtractor(CanonicalSourceExtractor):
                def extract(self,event,context):
                    self.assertion(event,context)
                    prepared=super().extract(event,context)
                    observations.append((queue.get(event.id).status,thoughts.list_thoughts()))
                    return prepared
                def assertion(self,event,context):
                    if event.id!=raw.id or [row.id for row in context]!=[old.id]:
                        raise AssertionError('Extractor not given exact claimed input')
            def factory(active):
                self.assertIs(active,engine)
                self.assertIsNotNone(active.gateway)
                return FixtureExtractor(active)
            worker=ThoughtWorker(queue,engine,lane='source',source_extractor_factory=factory)
            done=worker._source(worker._claim_source(event_id=raw.id))
            self.assertEqual(observations,[('running',[])])
            extracted,=thoughts.list_thoughts()
            self.assertEqual((done.status,done.extracted_ids,extracted.text),
                             ('completed',[extracted.id],raw.prompt))


if __name__=='__main__':unittest.main()
