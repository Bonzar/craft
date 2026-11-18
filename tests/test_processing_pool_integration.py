"""Actual run_worker/source queue with deterministic providers, never external models."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.mcp_server import ServiceConfig,run_worker
from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.storage import MemoryStore

class PoolSourceIntegrationTest(unittest.TestCase):
    def test_real_source_jobs_overlap_on_two_isolated_provider_instances(self):
        barrier=threading.Barrier(2);mutex=threading.Lock();active=[0];peak=[0];owners=[];calls=[]
        class Provider:
            def __init__(self):self.owner=threading.get_ident();self.identity=id(self);owners.append((self.owner,self.identity))
            def __call__(self,request):
                if self.owner!=threading.get_ident():raise AssertionError('provider crossed thread boundary')
                calls.append((self.owner,self.identity,request.task))
                if request.task.startswith('Extract'):
                    target=request.records[0]
                    with mutex:active[0]+=1;peak[0]=max(peak[0],active[0])
                    try:barrier.wait(timeout=3);time.sleep(.01)
                    finally:
                        with mutex:active[0]-=1
                    return json.dumps({'assertions':[{'text':target['text'],'quote':target['text']}]})
                if request.task=='Verify contextual assertion interpretations':
                    return json.dumps({'verdicts':[{'index':row['index'],'status':'accepted','reason':'Synthetic literal condition retained.'} for row in request.records if row.get('role')=='interpretation']})
                if request.task=='Review source material coverage':
                    return json.dumps({'source_coverage':{'status':'accepted','reason':'Synthetic target preserved.','omissions':[]}})
                raise AssertionError('unexpected provider work '+request.task)
        def engine(config,store):return MemoryEngine(store,model=Provider(),model_key='pool-fixture',verify_overviews=False)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'memory.sqlite3';config=ServiceConfig(str(path),'none',source_workers=2,worker_idle_seconds=.01)
            texts=['Synthetic office A activates only after trial confirmation.','Synthetic office B retains manual exit until an independent check.']
            with MemoryStore(str(path)) as store:
                service=MemoryService(store)
                for i,text in enumerate(texts):service.capture_message('fixture-'+str(i),'m','fixture','user',text,0,'fixture://'+str(i),'s')
            with patch.object(ServiceConfig,'engine',engine):run_worker(config,limit=2)
            with MemoryStore(str(path)) as store:
                self.assertEqual(sorted(row.text for row in store.list_assertions()),sorted(texts))
                statuses=store._connection.execute("SELECT status,phase FROM memory_capture_jobs WHERE event_id IS NOT NULL").fetchall()
                self.assertEqual([row['status'] for row in statuses],['completed','completed'])
            extraction=[row for row in calls if row[2].startswith('Extract')]
            self.assertEqual(len(extraction),2);self.assertEqual(len({row[0] for row in extraction}),2)
            self.assertEqual(len({row[1] for row in extraction}),2);self.assertEqual(peak[0],2)
            self.assertNotIn(threading.get_ident(),{row[0] for row in extraction})
    def test_supervisor_heartbeat_keeps_real_claims_alive_during_longer_provider_wait(self):
        from contextlib import contextmanager
        from prototypes.assertion_memory.processing_pool import ProcessingPool,ActiveClaims
        from prototypes.assertion_memory.queue import CaptureQueue
        from prototypes.assertion_memory.worker import LinkWorker
        lease=.15;claims=ActiveClaims();renewed=[]
        class Provider:
            def __call__(self,request):
                if request.task.startswith('Extract'):
                    time.sleep(.35);target=request.records[0]
                    return json.dumps({'assertions':[{'text':target['text'],'quote':target['text']}]})
                if request.task=='Verify contextual assertion interpretations':
                    return json.dumps({'verdicts':[{'index':r['index'],'status':'accepted','reason':'Synthetic exact source.'} for r in request.records if r.get('role')=='interpretation']})
                if request.task=='Review source material coverage':
                    return json.dumps({'source_coverage':{'status':'accepted','reason':'Synthetic complete target.','omissions':[]}})
                raise AssertionError(request.task)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'memory.sqlite3'
            with MemoryStore(str(path)) as supervisor:
                service=MemoryService(supervisor)
                for i in range(2):service.capture_message('session-'+str(i),'m','fixture','user','Synthetic conditional report '+str(i),0,'fixture://'+str(i),'s')
                @contextmanager
                def factory(lane,observer):
                    with MemoryStore(str(path)) as store:
                        engine=MemoryEngine(store,model=Provider(),model_key='heartbeat-fixture')
                        yield LinkWorker(CaptureQueue(store,lease_seconds=lease),engine,
                            source_first=True,lane=lane,claim_observer=observer)
                def heartbeat():
                    if claims.snapshot():
                        with supervisor.transaction(write=True) as db:renewed.append(claims.renew(db,lease_seconds=lease))
                result=ProcessingPool(factory,source_workers=2,limit=2,claims=claims,heartbeat=heartbeat,
                    heartbeat_seconds=.01,poll_seconds=.002).run()
                rows=supervisor._connection.execute("SELECT status,error FROM memory_capture_jobs WHERE event_id IS NOT NULL").fetchall()
                self.assertEqual([row['status'] for row in rows],['completed','completed'])
                self.assertTrue(all(row['error'] is None for row in rows));self.assertGreater(sum(renewed),2)
                self.assertEqual(result['completed'],2);self.assertEqual(claims.snapshot(),())

if __name__=='__main__':unittest.main()
