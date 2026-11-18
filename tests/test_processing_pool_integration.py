"""Actual run_worker/source queue with deterministic providers, never external models."""
import json
import fcntl
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from prototypes.assertion_memory.model import CandidateSearch
from prototypes.assertion_memory.mcp_server import ServiceConfig,run_worker
from prototypes.assertion_memory.thought_service import ThoughtService
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.storage import MemoryStore

def source_draft(record):
    fragments=record['fragments']
    return {'thoughts':[{'text':''.join(fragment['text'] for fragment in fragments),
                        'question':None,'support_fragments':[fragment['option'] for fragment in fragments]}]}


class PoolSourceIntegrationTest(unittest.TestCase):
    def test_real_source_jobs_overlap_on_two_isolated_provider_instances(self):
        barrier=threading.Barrier(2);mutex=threading.Lock();active=[0];peak=[0];owners=[];calls=[]
        class Provider:
            def __init__(self):self.owner=threading.get_ident();self.identity=id(self);owners.append((self.owner,self.identity))
            def __call__(self,request):
                if self.owner!=threading.get_ident():raise AssertionError('provider crossed thread boundary')
                calls.append((self.owner,self.identity,request.task))
                if request.task=='Construct thought':
                    target=next(row for row in request.records if row['role']=='source')
                    with mutex:active[0]+=1;peak[0]=max(peak[0],active[0])
                    try:barrier.wait(timeout=3);time.sleep(.01)
                    finally:
                        with mutex:active[0]-=1
                    return json.dumps(source_draft(target))
                if request.task=='Verify thought':
                    return json.dumps({'verdict':'accept','text':'Synthetic literal condition retained.'})
                raise AssertionError('unexpected provider work '+request.task)
        def components(config,store):return Provider(),CandidateSearch(),'pool-fixture'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'memory.sqlite3';config=ServiceConfig(str(path),'none',source_workers=2,worker_idle_seconds=.01)
            texts=['Synthetic office A activates only after trial confirmation.','Synthetic office B retains manual exit until an independent check.']
            with MemoryStore(str(path)) as store:
                service=config.service(store)
                for i,text in enumerate(texts):service.capture_message('fixture-'+str(i),'m','fixture','user',text,0,'fixture://'+str(i),'s')
            with patch.object(ServiceConfig,'components',components):run_worker(config,limit=2)
            with MemoryStore(str(path)) as store:
                self.assertEqual(sorted(row.text for row in ThoughtStore(store).list_thoughts()),sorted(texts))
                statuses=store._connection.execute("SELECT status,phase FROM memory_capture_jobs WHERE event_id IS NOT NULL").fetchall()
                self.assertEqual([row['status'] for row in statuses],['completed','completed'])
            extraction=[row for row in calls if row[2]=='Construct thought']
            self.assertEqual(len(extraction),2);self.assertEqual(len({row[0] for row in extraction}),2)
            self.assertEqual(len({row[1] for row in extraction}),2);self.assertEqual(peak[0],2)
            self.assertNotIn(threading.get_ident(),{row[0] for row in extraction})
    def test_supervisor_heartbeat_keeps_real_claims_alive_during_longer_provider_wait(self):
        from contextlib import contextmanager
        from prototypes.assertion_memory.processing_pool import ProcessingPool,ActiveClaims
        from prototypes.assertion_memory.queue import CaptureQueue
        from prototypes.assertion_memory.thought_worker import ThoughtWorker
        lease=.15;claims=ActiveClaims();renewed=[];barrier=threading.Barrier(2)
        class Provider:
            def __call__(self,request):
                if request.task=='Construct thought':
                    barrier.wait(timeout=3);time.sleep(.35)
                    target=next(row for row in request.records if row['role']=='source')
                    return json.dumps(source_draft(target))
                if request.task=='Verify thought':
                    return json.dumps({'verdict':'accept','text':'Synthetic complete target.'})
                raise AssertionError(request.task)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'memory.sqlite3'
            with MemoryStore(str(path)) as supervisor:
                service=ThoughtService(supervisor)
                for i in range(2):service.capture_message('session-'+str(i),'m','fixture','user','Synthetic conditional report '+str(i),0,'fixture://'+str(i),'s')
                @contextmanager
                def factory(lane,observer):
                    with MemoryStore(str(path)) as store:
                        engine=ThoughtService(store,Provider()).engine
                        yield ThoughtWorker(CaptureQueue(store,lease_seconds=lease),engine,
                            lane=lane,lock_owned=True,claim_observer=observer)
                def heartbeat():
                    if claims.snapshot():
                        with supervisor.transaction(write=True) as db:renewed.append(claims.renew(db,lease_seconds=lease))
                with Path(str(path)+'.worker.lock').open('a') as lock:
                    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    result=ProcessingPool(factory,source_workers=2,limit=2,claims=claims,heartbeat=heartbeat,
                        heartbeat_seconds=.01,poll_seconds=.002).run()
                rows=supervisor._connection.execute("SELECT status,error FROM memory_capture_jobs WHERE event_id IS NOT NULL").fetchall()
                self.assertEqual([row['status'] for row in rows],['completed','completed'],[dict(row) for row in rows])
                self.assertTrue(all(row['error'] is None for row in rows));self.assertGreater(sum(renewed),2)
                self.assertEqual(result['completed'],2);self.assertEqual(claims.snapshot(),())

if __name__=='__main__':unittest.main()
