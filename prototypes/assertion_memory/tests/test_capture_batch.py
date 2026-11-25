import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from prototypes.assertion_memory.capture_batch import capture, run_next
from prototypes.assertion_memory.fresh_processing import authenticated_fresh_mac
from prototypes.assertion_memory.mcp_server import ServiceConfig, _eligible_processing_work, run_worker
from prototypes.assertion_memory.processing_control import INITIAL, set_control
from prototypes.assertion_memory.storage import MemoryStore


class CaptureBatchTest(unittest.TestCase):
    def setUp(self):
        directory=self.enterContext(TemporaryDirectory())
        self.db=str(Path(directory)/'fixture.sqlite3')
        self.store=self.enterContext(MemoryStore(self.db))
        self.config=ServiceConfig(db=self.db,provider='subprocess',command=('unused',),
            processing_session_ids=[],auto_process_fresh_mac=True,worker_idle_seconds=.01)
        self.service=self.config.service(self.store)
        self.session='00000000-0000-0000-0000-000000000001'
        with authenticated_fresh_mac():
            self.stream=self.service.ingest_register('jarvis:00000000-0000-0000-0000-000000000002',self.session)
        self.messages=[
            {'session_id':self.session,'message_id':mid,'actor_id':actor,'role':role,
             'text':text,'source':'fixture://generic/'+mid,
             'delivery':{k:self.stream[k] for k in ('stream_id','epoch')}|{'seq':seq}}
            for seq,(mid,actor,role,text) in enumerate([
                ('input','fiction-user','user','The kettle boils water.'),
                ('answer','assistant','assistant','The oven cooks bread.')],1)]
        self.batch_id='b'*64

    def capture(self,messages=None):
        with authenticated_fresh_mac():
            return capture(self.service,self.batch_id,self.messages if messages is None else messages)

    def status(self):
        with self.store.transaction() as db:
            return db.execute('SELECT state,error_code FROM memory_capture_batches').fetchone()

    @staticmethod
    def model(tasks):
        def produce(request):
            tasks.append(request.task)
            if request.task=='Verify thought':
                return json.dumps({'verdict':'accept','text':'Matches the source.'})
            if request.task!='Construct thoughts for multiple source messages':
                raise AssertionError('unexpected individual draft')
            catalog=request.records[0]['fragment_texts']
            response={}
            for case in request.records[1:]:
                source=next(row for row in case['records'] if row['role']=='source')
                fragment=source['fragments'][0]
                response[case['source']]={'thoughts':[{'text':catalog[fragment['text_index']],
                    'question':None,'support_fragments':[fragment['option']]}],'needs_detail':None}
            return json.dumps(response)
        return produce

    def test_atomic_generic_receipt_no_rebundle_and_same_request_replay(self):
        with self.assertRaisesRegex(ValueError,'authenticated machine'):
            capture(self.service,self.batch_id,self.messages)
        invalid=self.messages[:1]+[self.messages[1]|{'role':'invalid'}]
        with self.assertRaises(ValueError):
            self.capture(invalid)
        self.assertFalse(_eligible_processing_work(self.config))
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM memory_raw_events').fetchone()[0],0)
        first=self.capture()
        replay=self.capture()
        self.assertEqual([r['event']['id'] for r in first['receipts']],
                         [r['event']['id'] for r in replay['receipts']])
        self.assertEqual(first['receipts'][-1]['delivery']['acked_through'],2)
        self.assertEqual(len(set(r['event']['id'] for r in first['receipts'])),2)
        self.assertTrue(_eligible_processing_work(self.config))
        with self.assertRaisesRegex(ValueError,'immutable'):
            self.capture(self.messages[:1])
        with authenticated_fresh_mac(),self.assertRaisesRegex(ValueError,'rebundle'):
            capture(self.service,'a'*64,self.messages)

    def test_real_auto_supervisor_one_group_generation_and_reviews_with_pause(self):
        receipt=self.capture()
        with self.store.transaction(write=True) as db:
            _,version=set_control(db,paused=True,expected_revision=INITIAL)
        self.assertFalse(run_next(self.config,self.store,lambda:False))
        self.assertEqual(self.status()[0],'pending')
        with self.store.transaction(write=True) as db:
            set_control(db,paused=False,expected_revision=version)
        tasks=[]
        with patch.object(ServiceConfig,'components',lambda *_:(self.model(tasks),None,'test')):
            run_worker(self.config)
            run_worker(self.config)  # no retry after completion
        self.assertEqual(tasks,['Construct thoughts for multiple source messages','Verify thought','Verify thought'])
        self.assertEqual(self.status()[0],'completed')
        events=[r['event']['id'] for r in receipt['receipts']]
        self.assertTrue(all(self.service.queue.get(e).status=='completed' for e in events))
        self.assertEqual(len(self.service.thoughts.list_thoughts()),2)

    def test_interrupted_dispatch_or_removed_member_never_regenerates_partial_batch(self):
        events=[r['event']['id'] for r in self.capture()['receipts']]
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_capture_batches SET state='dispatching'")
            db.execute("UPDATE memory_capture_jobs SET status='running',lease_token='interrupted',lease_until='later'")
        self.assertTrue(_eligible_processing_work(self.config))
        run_worker(self.config)  # Recover dispatching as blocked, without generating again.
        self.assertEqual([self.service.queue.get(e).status for e in events],['failed','failed'])
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM memory_capture_jobs WHERE lease_token IS NOT NULL').fetchone()[0],0)
        self.assertFalse(run_next(self.config,self.store,lambda:False))
        self.assertEqual(tuple(self.status()),('blocked','provider_outcome_unknown'))
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_capture_batches SET state='pending'")
            db.execute('DELETE FROM memory_capture_batch_members WHERE ordinal=1')
        with self.assertRaisesRegex(ValueError,'member was removed'):
            self.capture()
        self.assertTrue(run_next(self.config,self.store,lambda:False))
        self.assertEqual(tuple(self.status()),('blocked','batch_member_removed'))

    def test_pending_batch_rejects_member_delete_at_storage_invariant(self):
        from sqlite3 import IntegrityError
        events=[r['event']['id'] for r in self.capture()['receipts']]
        with self.assertRaisesRegex(IntegrityError,'cancelled atomically'):
            with self.store.transaction(write=True) as db:
                db.execute('DELETE FROM memory_raw_events WHERE id=?',(events[0],))
        self.assertEqual(self.status()[0],'pending')
        self.assertTrue(all(self.service.queue.get(e).attempts==0 for e in events))

    def test_test_selection_and_owner_removal_cannot_claim_single_live_batch_member(self):
        from prototypes.assertion_memory.processing_control import Conflict
        from prototypes.assertion_memory.queue_item_removal import (
            _state, annotate, remove, revision, source_tombstoned)
        events=[r['event']['id'] for r in self.capture()['receipts']]
        ordinary=ServiceConfig(db=self.db,provider='subprocess',command=('unused',),
            processing_session_ids=[self.session],queue_sessions_revision='1'*32)
        self.assertFalse(_eligible_processing_work(ordinary))
        event=events[0]
        with self.store.transaction() as db:
            item={'id':event}
            annotate(db,{'queues':[{'id':'source','items':[item]}]})
            self.assertNotIn('remove_revision',item)
            self.assertIn('preview_revision',item)
            expected=revision(_state(db,'source',event),'source',event)
            self.assertIsNotNone(expected)
        with self.assertRaisesRegex(Conflict,'cannot be removed one item'):
            with self.store.transaction(write=True) as db:
                remove(db,stage='test',queue_id='source',item_id=event,expected_revision=expected,now='now')
        from contextlib import nullcontext
        import asyncio
        from httpx import ASGITransport, AsyncClient
        from prototypes.assertion_memory.native_pause import PATH, make_native_pause
        from prototypes.assertion_memory.queue_owner_domain import owner_operations
        perform=owner_operations(database=Path(self.db),release_lease=nullcontext,identity=lambda:('r','g'))
        actor,token='a'*64,'b'*64
        app=make_native_pause(actor=actor,token=token,prepare=lambda:None,change=lambda:None,
                             queue_operations=perform,queue_sessions=True)
        async def call():
            async with AsyncClient(transport=ASGITransport(app=app),base_url='https://base.example') as client:
                return await client.post(PATH,json={'stage':'test','actor':actor,'action':'item_remove',
                    'arguments':{'queue_id':'source','item_id':event,'expected_revision':expected}},
                    headers={'Authorization':'Bearer '+token})
        response=asyncio.run(call())
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.json(),{'error':'queue_control_changed'})
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM memory_ingest_receipts').fetchone()[0],2)
            self.assertFalse(source_tombstoned(db,event))
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertEqual(self.status()[0],'pending')
        self.assertTrue(all(self.service.queue.get(e).attempts==0 for e in events))
        self.assertTrue(all(self.service.queue.get_event(e) is not None for e in events))
        self.assertTrue(_eligible_processing_work(self.config))

    def test_live_registry_authorizes_batch_only_for_pinned_machine(self):
        import asyncio,sys
        from hashlib import sha256
        from uuid import uuid4
        from httpx import ASGITransport,AsyncClient
        from prototypes.assertion_memory.operation_registry import create_operations
        from prototypes.assertion_memory.release_catalog import export

        async def exercise():
            tool_dir=Path(__file__).resolve().parents[3]/'tools/assertion-memory-hosted-mcp'
            with patch.object(sys,'path',[str(tool_dir),*sys.path]):
                from compile_functional_release import compile_release
                from vm_relay import make_app
            release='assertion-memory-v9.9.9'
            canonical=await export('a'*40)
            pins={k:canonical[k] for k in ('revision','module_sha256','schema_sha256')}
            pins['registry']='base-typed-operations-v1'
            catalog=compile_release(canonical,pins,effects=False)
            profile={'lane':'prod','base_release':release,'enabled_capabilities':['baseline_read'],
                     'catalog_sha256':catalog['catalog_sha256'],'require_catalog_identity':True}
            mac,other='c'*64,'d'*64
            keys=tuple(sha256(s.encode()).hexdigest() for s in (mac,other))
            ops=create_operations(self.config,self.store,background=False)
            app=make_app(profile,catalog,'b'*64,lambda:(release,'f'*64),operations=ops,
                         machine_digests=keys,fresh_mac_digest=keys[0])
            async with AsyncClient(transport=ASGITransport(app=app),base_url='https://memory.example') as client:
                async def send(token,op,args,expected=None):
                    body={'request_id':str(uuid4()),'operation':op,'arguments':args}
                    if expected is not None:body['expected']=expected
                    return await client.post('/memory-api-prod',json=body,
                                             headers={'Authorization':'Bearer '+token})
                for key in (other,mac):
                    descriptor=(await send(key,'catalog',{})).json()
                    self.assertEqual(any(d['name']=='memory_capture_batch' for d in descriptor['tools']),key==mac)
                    identity={k:descriptor[k] for k in ('release','generation','catalog_sha256')}
                    response=await send(key,'memory_capture_batch',
                        {'batch_id':self.batch_id,'messages':self.messages},identity)
                    self.assertEqual(response.status_code,200 if key==mac else 400)
                    if key==mac:
                        self.assertEqual(response.json()['result']['receipts'][-1]['delivery']['acked_through'],2)

        asyncio.run(exercise())

    def test_claim_bound_extractor_explicitly_has_no_batch_fallback(self):
        from prototypes.assertion_memory.thought_processing import TracedThoughtWorker
        touched=[]
        with patch.object(ServiceConfig,'components',lambda *_:(self.model(touched),None,'test')):
            service=self.config.service(self.store)
        worker=TracedThoughtWorker(service.queue,service.engine,lock_owned=True,
            processing_session_ids=(self.session,),claim_bound_source_extractor_factory=lambda *_:None)
        events=[r['event']['id'] for r in self.capture()['receipts']]
        with self.assertRaisesRegex(ValueError,'no whole-batch'):
            worker.process_source_group(events,len(events),capture_batch_id=self.batch_id)
        self.assertEqual(touched,[])
        self.assertTrue(all(service.queue.get(event).attempts==0 for event in events))
