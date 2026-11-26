import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from prototypes.assertion_memory.capture_batch import authorized_machine_batch, capture, run_next
from prototypes.assertion_memory.mcp_server import ServiceConfig, _eligible_processing_work, run_worker
from prototypes.assertion_memory.processing_control import INITIAL, set_control
from prototypes.assertion_memory.storage import MemoryStore


class CaptureBatchTest(unittest.TestCase):
    def setUp(self):
        directory=self.enterContext(TemporaryDirectory())
        self.db=str(Path(directory)/'fixture.sqlite3')
        self.store=self.enterContext(MemoryStore(self.db))
        self.config=ServiceConfig(db=self.db,provider='subprocess',command=('unused',),
            processing_session_ids=[],worker_idle_seconds=.01)
        self.service=self.config.service(self.store)
        self.session='00000000-0000-0000-0000-000000000001'
        self.stream=self.service.ingest_register('fixture-supplier',self.session)
        self.messages=[
            {'session_id':self.session,'message_id':mid,'actor_id':actor,'role':role,
             'text':text,'source':'fixture://generic/'+mid,
             'delivery':{k:self.stream[k] for k in ('stream_id','epoch')}|{'seq':seq}}
            for seq,(mid,actor,role,text) in enumerate([
                ('input','fiction-user','user','The kettle boils water.'),
                ('answer','assistant','assistant','The oven cooks bread.')],1)]
        self.batch_id='b'*64

    def capture(self,messages=None):
        with authorized_machine_batch():
            return capture(self.service,self.batch_id,self.messages if messages is None else messages)

    def status(self):
        with self.store.transaction() as db:
            return db.execute('SELECT state,error_code FROM memory_capture_batches').fetchone()

    @staticmethod
    def model(tasks):
        def produce(request):
            tasks.append(request.task)
            if request.task in ('Verify thought','Verify source thought batch'):
                return json.dumps({'verdict':'accept','text':'Matches the source.',
                    **({'discard_drafts': []} if request.task=='Verify source thought batch' else {})})
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
        with authorized_machine_batch(),self.assertRaisesRegex(ValueError,'rebundle'):
            capture(self.service,'a'*64,self.messages)

    def test_registered_stream_epoch_session_and_existing_individual_receipts(self):
        other=self.service.ingest_register('another-api-producer','another-session')
        for messages,reason in (
            ([row|{'delivery': row['delivery']|{'epoch':other['epoch']}} for row in self.messages],'stale'),
            ([row|{'delivery': row['delivery']|{'stream_id':other['stream_id'],'epoch':other['epoch']}}
              for row in self.messages],'registered session'),
        ):
            with self.assertRaisesRegex(ValueError,reason):
                self.capture(messages)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM memory_raw_events').fetchone()[0],0)
        self.service.capture_message(position=1,**self.messages[0])
        with self.assertRaisesRegex(ValueError,'rebundle'):
            self.capture()
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM memory_raw_events').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM memory_ingest_receipts').fetchone()[0],1)

    def test_real_auto_supervisor_one_group_generation_and_reviews_with_pause(self):
        old_event=self.service.capture_message('legacy-session','legacy-msg','user','user',
                                              'Not selected for automatic processing.',1,source='fixture://legacy')['event']['id']
        self.assertFalse(_eligible_processing_work(self.config))
        prior_job=self.service.queue.get(old_event)
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
        self.assertEqual(tasks,['Construct thoughts for multiple source messages','Verify source thought batch',
                                'Verify thought','Verify thought'])
        self.assertEqual(self.status()[0],'completed')
        events=[r['event']['id'] for r in receipt['receipts']]
        self.assertTrue(all(self.service.queue.get(e).status=='completed' for e in events))
        self.assertEqual(len(self.service.thoughts.list_thoughts()),2)
        self.assertEqual(self.service.queue.get(old_event).attempts,0)
        self.assertEqual(self.service.queue.get(old_event),prior_job)

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

    def test_complete_package_coverage_ignores_repetition_but_not_independent_omission(self):
        for repeated,expected in ((True,'completed'),(False,'blocked')):
            with self.subTest(repeated=repeated), TemporaryDirectory() as root:
                config=ServiceConfig(db=str(Path(root)/'fixture.sqlite3'),provider='subprocess',
                                     command=('unused',),processing_session_ids=[],worker_idle_seconds=.01)
                with MemoryStore(config.db) as store:
                    service=config.service(store)
                    stream=service.ingest_register('coverage-test',self.session)
                    messages=[row|{'delivery':{k:stream[k] for k in ('stream_id','epoch')}|{'seq':i+1},
                                   'actor_id':'fiction-user','role':'user','text':
                                   ('I prefer black coffee without sugar.' if i==0 or repeated else
                                    'I work from the office on Wednesdays.')}
                              for i,row in enumerate(self.messages)]
                    with authorized_machine_batch():
                        receipts=capture(service,self.batch_id,messages)['receipts']
                    calls=[]
                    def model(request):
                        calls.append(request.task)
                        if request.task=='Construct thoughts for multiple source messages':
                            return json.dumps({'source_1':{'thoughts':[{'text':messages[0]['text'],
                                'question':None,'support_fragments':[0]}],'needs_detail':None},
                                'source_2':{'thoughts':[],'needs_detail':None}})
                        if request.task=='Verify source thought batch':
                            self.assertIn('coverage across the complete',request.instructions)
                            return json.dumps({'verdict':'accept' if repeated else 'reject',
                                               'text':'Equivalent' if repeated else 'Office fact omitted',
                                               'discard_drafts': []})
                        self.assertEqual(request.task,'Verify thought')
                        self.assertIn('Your scope here is local entailment',request.instructions)
                        self.assertNotIn('proposed_thoughts',request.records[0])
                        return json.dumps({'verdict':'accept','text':'Supported locally'})
                    with patch.object(ServiceConfig,'components',lambda *_:(model,None,'fixture')):
                        run_worker(config)
                    with store.transaction() as db:
                        self.assertEqual(db.execute('SELECT state FROM memory_capture_batches').fetchone()[0],expected)
                        self.assertEqual(db.execute('SELECT count(*) FROM memory_raw_events').fetchone()[0],2)
                    self.assertEqual(calls,['Construct thoughts for multiple source messages',
                                            'Verify source thought batch',*(['Verify thought'] if repeated else [])])
                    thoughts=service.thoughts.list_thoughts()
                    self.assertEqual(len(thoughts),int(repeated))
                    if repeated:
                        self.assertEqual({row['event_id'] for row in thoughts[0].bases},
                                         {receipts[0]['event']['id']})

    def test_pending_batch_rejects_member_delete_at_storage_invariant(self):
        from sqlite3 import IntegrityError
        events=[r['event']['id'] for r in self.capture()['receipts']]
        with self.assertRaisesRegex(IntegrityError,'cancelled atomically'):
            with self.store.transaction(write=True) as db:
                db.execute('DELETE FROM memory_raw_events WHERE id=?',(events[0],))
        self.assertEqual(self.status()[0],'pending')
        self.assertTrue(all(self.service.queue.get(e).attempts==0 for e in events))

    def test_local_subset_rejection_rechecks_once_and_never_reconstructs(self):
        first, second = self.messages
        receipts=self.capture([first|{'text':'Only comedy for the current outing.'},second|{'text':'Understood.'}])['receipts']
        calls=[]
        def model(request):
            calls.append(request.task)
            if request.task=='Construct thoughts for multiple source messages':
                return json.dumps({'source_1':{'thoughts':[
                    {'text':'User chose comedy for the current outing.','question':None,'support_fragments':[0]},
                    {'text':'User chose theatre as well.','question':None,'support_fragments':[0]}],
                    'needs_detail':None},'source_2':{'thoughts':[],'needs_detail':None}})
            if request.task=='Verify thought':
                return json.dumps({'verdict':'reject','text':'one draft unsupported',
                                   'rejected_drafts':[{'draft':1,'text':'No theatre in source'}],
                                   'missing_meaning':None})
            if calls.count('Verify source thought batch')==2:
                self.assertIn('coverage-only recheck',request.instructions)
                self.assertEqual(len(request.records[1]['proposed_thoughts']['thoughts']),1)
            return json.dumps({'verdict':'accept','text':'complete','discard_drafts':[]})
        with patch.object(ServiceConfig,'components',lambda *_:(model,None,'fixture')):
            run_worker(self.config)
        self.assertEqual(calls,['Construct thoughts for multiple source messages',
                               'Verify source thought batch','Verify thought','Verify source thought batch'])
        self.assertEqual(self.status()[0],'completed')
        thoughts=self.service.thoughts.list_thoughts()
        self.assertEqual(len(thoughts),1)
        self.assertEqual({row['event_id'] for row in thoughts[0].bases},{receipts[0]['event']['id']})

    def test_batch_rolls_back_first_publish_if_second_cannot_finish(self):
        from prototypes.assertion_memory.thought_processing import TracedThoughtWorker
        receipts=self.capture()['receipts']
        calls=[]
        ordinary=TracedThoughtWorker._source
        def fail_second(worker, claim):
            if claim.job.event_id==receipts[1]['event']['id']:
                raise RuntimeError('synthetic publication failure')
            value=ordinary(worker,claim)
            self.assertEqual(value.status,'completed')
            return value
        with (patch.object(ServiceConfig,'components',lambda *_:(self.model(calls),None,'fixture')),
              patch.object(TracedThoughtWorker,'_source',fail_second)):
            run_worker(self.config)
        self.assertEqual(self.status()[0],'blocked')
        self.assertEqual(self.service.thoughts.list_thoughts(),[])
        self.assertTrue(all(self.service.queue.get(r['event']['id']).status=='failed' for r in receipts))

    def test_fresh_mode_owner_can_discover_neutral_batch_after_explicit_selection(self):
        from prototypes.assertion_memory.capture_batch import needs_wake
        from prototypes.assertion_memory.queue_session_control import annotate_source, status, update
        for stage in ('test','prod'):
            with self.subTest(stage=stage):
                with self.store.transaction(write=True) as db:
                    previous=status(db,stage=stage,fresh_only=True)
                    update(db, stage=stage, fresh_only=True,
                           selected_session_ids=[],expected_revision=previous['revision'])
                events=[r['event']['id'] for r in self.capture()['receipts']]
                with self.store.transaction(write=True) as db:
                    current=status(db,stage=stage,fresh_only=True)
                    self.assertEqual(current['selected_session_ids'],[])
                    self.assertEqual(current['sessions'],[{'id':self.session,'queued':2,'eligible':True}])
                    self.assertFalse(needs_wake(db))
                    items=[dict(id=event,status='pending') for event in events]
                    data=dict(queues=[dict(id='source',items=items)],processing_control={'paused':False})
                    annotate_source(db,data,[],fresh_only=True)
                    self.assertEqual([item['hold_reason'] for item in items],['session_excluded']*2)
                    update(db,stage=stage,fresh_only=True,
                           selected_session_ids=[self.session],expected_revision=current['revision'])
                    self.assertTrue(needs_wake(db))
                    annotate_source(db,data,[],fresh_only=True)
                    self.assertEqual([item['hold_reason'] for item in items],[None,None])

    def test_test_selection_and_owner_removal_cannot_claim_single_live_batch_member(self):
        from prototypes.assertion_memory.processing_control import Conflict
        from prototypes.assertion_memory.queue_item_removal import (
            _state, annotate, remove, revision, source_tombstoned)
        events=[r['event']['id'] for r in self.capture()['receipts']]
        ordinary=ServiceConfig(db=self.db,provider='subprocess',command=('unused',),
            processing_session_ids=[self.session],queue_sessions_revision='1'*32)
        self.assertTrue(_eligible_processing_work(ordinary))
        self.assertFalse(_eligible_processing_work(ordinary,include_batches=False))
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

    def test_live_registry_authorizes_any_machine_key_in_its_own_lane(self):
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
            keys=('c'*64,'d'*64)
            for lane,lane_key,foreign in (('test',keys[0],keys[1]),('prod',keys[1],keys[0])):
                extra='e'*64
                profile={'lane':lane,'base_release':release,'enabled_capabilities':['baseline_read'],
                         'catalog_sha256':catalog['catalog_sha256'],'require_catalog_identity':True}
                ops=create_operations(self.config,self.store,background=False)
                app=make_app(profile,catalog,'b'*64,lambda:(release,'f'*64),operations=ops,
                             machine_digests=tuple(sha256(k.encode()).hexdigest() for k in (lane_key,extra)))
                async with AsyncClient(transport=ASGITransport(app=app),base_url='https://memory.example') as client:
                    async def send(token,op,args,expected=None,path=None):
                        body={'request_id':str(uuid4()),'operation':op,'arguments':args}
                        if expected is not None:body['expected']=expected
                        return await client.post(path or '/memory-api-'+lane,json=body,
                                                 headers={'Authorization':'Bearer '+token})
                    for key in (lane_key,extra):
                        descriptor=(await send(key,'catalog',{})).json()
                        self.assertEqual(descriptor['environment'],lane)
                        self.assertEqual(descriptor['capability'],'machine')
                        self.assertIn('memory_capture_batch',{d['name'] for d in descriptor['tools']})
                        identity={k:descriptor[k] for k in ('release','generation','catalog_sha256')}
                        body={'batch_id':self.batch_id,'messages':self.messages}
                        response=await send(key,'memory_capture_batch',body,identity)
                        self.assertEqual(response.status_code,200,response.text)
                        self.assertEqual(response.json()['result']['receipts'][-1]['delivery']['acked_through'],2)
                    self.assertEqual((await send(foreign,'memory_capture_batch',body,identity)).status_code,401)
                    self.assertEqual((await send('b'*64,'memory_capture_batch',body,identity,
                                                 '/memory-mcp-'+lane+'-tools')).status_code,400)

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
