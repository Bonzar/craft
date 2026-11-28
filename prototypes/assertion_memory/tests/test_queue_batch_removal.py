"""Behavior of one owner cancellation for a whole first-processing batch."""
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from prototypes.assertion_memory.capture_batch import authorized_machine_batch,capture,run_next
from prototypes.assertion_memory.mcp_server import ServiceConfig
from prototypes.assertion_memory.processing_control import Conflict
from prototypes.assertion_memory.queue_owner_domain import owner_operations
from prototypes.assertion_memory.queue_rest import snapshot
from prototypes.assertion_memory.storage import MemoryStore

BAT='b'*64
ITEM='batch:'+BAT
IDENTITY=('assertion-memory-v2.0.0','c'*64)

class PendingBatchRemoval(TestCase):
    def fixture(self,stage):
        path=Path(self.enterContext(TemporaryDirectory()))/'synthetic.sqlite3'
        config=ServiceConfig(db=str(path),provider='none',processing_session_ids=[])
        store=self.enterContext(MemoryStore(path))
        service=config.service(store)
        stream=service.ingest_register('producer-independent',stage+'-fiction')
        packet=[dict(session_id=stage+'-fiction',message_id=f'm{i}',actor_id=role,role=role,text='QA only '+role,
            source=f'fixture://source/{i}',delivery={k:stream[k] for k in ('stream_id','epoch')}|{'seq':i})
            for i,role in enumerate(('user','assistant'),1)]
        with authorized_machine_batch():
            receipt=capture(service,BAT,packet)
        owner=owner_operations(database=path,release_lease=nullcontext,identity=lambda:IDENTITY,stage=stage)
        def card(*,enabled=True):
            state=snapshot(config,identity=lambda:IDENTITY,database=path,interactive_enabled=False,
                limit=50,offset=0,lane=stage,can_change=True,batch_cards=True,batch_remove=enabled)
            return next((i for q in state['queues'] if q['id']=='source' for i in q['items'] if i['id']==ITEM),{})
        return store,service,config,packet,receipt,owner,card

    def test_same_lanes_cancel_whole_pending_source_and_prevent_replay(self):
        for stage in ('test','prod'):
            with self.subTest(stage=stage):
                store,service,config,packet,receipt,owner,card=self.fixture(stage)
                start=card()
                self.assertEqual(start['member_count'],2)
                self.assertNotIn('remove_revision',card(enabled=False))
                self.assertTrue(all('remove_revision' not in m for m in start['members']))
                result=owner('item_remove',dict(queue_id='source',item_id=ITEM,expected_revision=start['remove_revision']))
                self.assertEqual(result,dict(stage=stage,queue_id='source',item_id=ITEM,
                                             removed=True,effect='source_and_queue'))
                self.assertEqual(card(),{})
                self.assertFalse(run_next(config,store,lambda:False))
                with store.transaction() as db:
                    for table in ('memory_raw_events','memory_capture_jobs','memory_capture_batch_members'):
                        self.assertEqual(db.execute('select count(*) from '+table).fetchone()[0],0)
                    self.assertEqual(tuple(db.execute('select state,error_code from memory_capture_batches').fetchone()),
                                     ('cancelled','owner_cancelled'))
                    self.assertEqual(db.execute('select count(*) from memory_ingest_retired_receipts').fetchone()[0],2)
                with authorized_machine_batch(),self.assertRaisesRegex(ValueError,'cannot replay'):
                    capture(service,BAT,packet)
                with authorized_machine_batch(),self.assertRaises(ValueError):
                    capture(service,'a'*64,packet)
                with store.transaction() as db:
                    self.assertEqual(db.execute('select count(*) from memory_raw_events').fetchone()[0],0)
                    self.assertEqual(db.execute('select count(*) from memory_capture_batches').fetchone()[0],1)

    def test_stale_active_member_and_dependency_do_not_partially_delete(self):
        for cause in ('stale','active-member','dependent','dispatching','attempted'):
            with self.subTest(cause=cause):
                store,_,_,_,receipt,owner,card=self.fixture('test')
                token=card()['remove_revision']
                event=receipt['receipts'][-1]['event']['id']
                request=dict(queue_id='source',item_id=ITEM,expected_revision=token)
                with store.transaction(write=True) as db:
                    if cause=='stale':
                        db.execute("update memory_capture_jobs set updated_at='changed' where id=?",(event,))
                    if cause=='active-member':
                        request['item_id']=event
                    if cause=='dependent':
                        db.execute('create table fixture_canonical_dependency(id text references memory_raw_events(id))')
                        db.execute('insert into fixture_canonical_dependency values (?)',(event,))
                    if cause=='dispatching':
                        db.execute("update memory_capture_batches set state='dispatching'")
                    if cause=='attempted':
                        db.execute('update memory_capture_jobs set attempts=1 where id=?',(event,))
                with self.assertRaises(Conflict):
                    owner('item_remove',request)
                self.assertTrue(card())
                if cause in ('dispatching','attempted'):
                    self.assertNotIn('remove_revision',card())
                with store.transaction() as db:
                    self.assertEqual(db.execute('select count(*) from memory_raw_events').fetchone()[0],2)
                    self.assertEqual(db.execute('select count(*) from memory_capture_jobs').fetchone()[0],2)
                    self.assertEqual(db.execute('select count(*) from memory_capture_batch_members').fetchone()[0],2)

    def test_duplicate_confirmations_have_one_transactional_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        store,_,_,_,_,owner,card=self.fixture('test')
        args=dict(queue_id='source',item_id=ITEM,expected_revision=card()['remove_revision'])
        barrier=Barrier(2)
        def invoke():
            barrier.wait(timeout=2)
            try:
                owner('item_remove',args)
                return 'removed'
            except Conflict:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            a,b=(pool.submit(invoke) for _ in range(2))
            self.assertCountEqual((a.result(timeout=5),b.result(timeout=5)),('removed','conflict'))
        with store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM memory_ingest_retired_receipts').fetchone()[0],2)
