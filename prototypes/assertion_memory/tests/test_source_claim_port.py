"""A trusted composition hook gains identity + read checkpoint, not write authority."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import json
import unittest

from prototypes.assertion_memory.queue import CaptureQueue, LeaseLost
from prototypes.assertion_memory.source_extraction import CanonicalSourceExtractor
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.thought_worker import ThoughtWorker
from prototypes.assertion_memory.tests.prepared_support_helpers import drafts


class SourceClaimPortTest(unittest.TestCase):
    def fixture(self,memory):
        queue=CaptureQueue(memory)
        store=ThoughtStore(memory)
        raw=queue.capture_event('capture','t','Synthetic fixture input.',source='fixture',scope='narrow',
                               metadata={'native_session_id':'canonical'})
        def provider(request):
            if request.task=='Construct thought':
                return json.dumps(drafts(request,(raw.prompt,[raw.prompt])))
            return json.dumps({'verdict':'accept','text':'Exact fixture evidence.'})
        return queue,store,raw,ThoughtEngine(store,provider)

    def test_only_immutable_routing_and_live_check_inside_attempt(self):
        with MemoryStore(':memory:') as memory:
            queue,store,raw,engine=self.fixture(memory)
            saved=[]
            def bind(active,request):
                self.assertIs(active,engine)
                self.assertEqual((request.event_id,request.capture_session_id,request.canonical_session_id,request.scope),
                    (raw.id,'capture','canonical','narrow'))
                self.assertIsNone(request.lease.ensure_active())
                with self.assertRaises(FrozenInstanceError):request.scope='broader'
                with ThreadPoolExecutor(max_workers=1) as pool:
                    with self.assertRaises(LeaseLost):pool.submit(request.lease.ensure_active).result()
                saved.append(request)
                return CanonicalSourceExtractor(active)
            worker=ThoughtWorker(queue,engine,lane='source',claim_bound_source_extractor_factory=bind)
            finished=worker.run_once()
            self.assertEqual(finished.status,'completed',finished.error)
            self.assertEqual(len(finished.extracted_ids),1)
            self.assertEqual(len(saved),1)
            with self.assertRaises(LeaseLost):saved[0].lease.ensure_active()

    def test_revoked_after_extraction_prevents_publish_and_expires_capability(self):
        with MemoryStore(':memory:') as memory:
            queue,store,raw,engine=self.fixture(memory)
            saved=[]
            class Revoked(CanonicalSourceExtractor):
                def extract(self,event,context):
                    accepted=super().extract(event,context)
                    with memory.transaction(write=True) as db:
                        db.execute("UPDATE memory_capture_jobs SET lease_token='other' WHERE id=?",(event.id,))
                    return accepted
            def bind(active,request):
                saved.append(request)
                return Revoked(active)
            worker=ThoughtWorker(queue,engine,lane='source',claim_bound_source_extractor_factory=bind)
            result=worker.run_once()
            self.assertEqual(result.extracted_ids,[])
            self.assertEqual(store.list_thoughts(),[])
            with self.assertRaises(LeaseLost):saved[0].lease.ensure_active()

    def test_cannot_install_two_custom_factories(self):
        with MemoryStore(':memory:') as memory:
            queue,store,raw,engine=self.fixture(memory)
            with self.assertRaisesRegex(ValueError,'one custom'):
                ThoughtWorker(queue,engine,source_extractor_factory=lambda active:CanonicalSourceExtractor(active),
                    claim_bound_source_extractor_factory=lambda active,request:CanonicalSourceExtractor(active))

if __name__=='__main__':unittest.main()
