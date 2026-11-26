from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from prototypes.assertion_memory.fresh_processing import authenticated_fresh_mac
from prototypes.assertion_memory.mcp_server import ServiceConfig, _eligible_processing_work, _processing_selector
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_processing_selection import maintenance_predicate, source_predicate


OLD = '00000000-0000-0000-0000-000000000001'
FRESH = '00000000-0000-0000-0000-000000000002'
OTHER = '00000000-0000-0000-0000-000000000003'
NEXT = '00000000-0000-0000-0000-000000000004'
PRODUCER = 'jarvis:00000000-0000-0000-0000-000000000000'


class FreshMacProcessingTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.db = str(Path(directory)/'fixture.sqlite3')
        self.store = self.enterContext(MemoryStore(self.db))
        self.config = ServiceConfig(db=self.db,provider='subprocess',command=('unused',),
                                    processing_session_ids=[],auto_process_fresh_mac=True)
        self.service = self.config.service(self.store)

    def record(self, session, message, stream=None, seq=1):
        delivery = None if stream is None else {k:stream[k] for k in ('stream_id','epoch')} | {'seq':seq}
        return self.service.capture_message(session,message,'synthetic-owner','user',
            'Only invented proof data',None,'fixture://fresh-auto/'+message,delivery=delivery)['event']['id']

    def eligible(self):
        with self.store.transaction() as db:
            query,args = source_predicate(_processing_selector(self.config),'e',db=db)
            from prototypes.assertion_memory.fresh_processing import ensure_selector
            if not ensure_selector(db):
                return set()
            return {r[0] for r in db.execute('SELECT e.id FROM memory_raw_events e WHERE '+query,args)}

    def test_no_old_backlog_or_non_mac_and_multiple_new_streams(self):
        self.record(OLD,'old')
        self.assertFalse(_eligible_processing_work(self.config))
        with authenticated_fresh_mac():
            old = self.service.ingest_register(PRODUCER,OLD)
            self.record(OLD,'late',old)
        other = self.service.ingest_register(PRODUCER,OTHER)
        with authenticated_fresh_mac():
            self.service.ingest_register(PRODUCER,OTHER)
            self.record(OTHER,'non-mac-created',other)
            first = self.service.ingest_register(PRODUCER,FRESH)
            second = self.service.ingest_register(PRODUCER,NEXT)
            first_event = self.record(FRESH,'new-one',first)
            second_event = self.record(NEXT,'new-two',second)
        untrusted = self.record(FRESH,'spoof',first,2)
        with authenticated_fresh_mac():
            self.record(FRESH,'spoof',first,2)  # Cannot retrofit an old raw receipt.
            self.record(FRESH,'no-delivery')
        self.assertNotIn(untrusted,self.eligible())
        self.assertEqual(self.eligible(),{first_event,second_event})
        self.assertTrue(_eligible_processing_work(ServiceConfig(**asdict(self.config))))
        with self.store.transaction() as db:
            self.assertEqual(maintenance_predicate(db,_processing_selector(self.config)),('0',[]))

    def test_bad_delivery_rolls_back_raw_and_may_be_retried(self):
        with authenticated_fresh_mac():
            stream = self.service.ingest_register(PRODUCER,FRESH)
            with self.assertRaises(ValueError):
                self.record(NEXT,'out-of-stream',stream)
            self.assertEqual(self.eligible(),set())
            corrected = self.record(FRESH,'out-of-stream',stream)
        self.assertEqual(self.eligible(),{corrected})

    def test_worker_wake_and_claim_select_only_committed_mac_event(self):
        from prototypes.assertion_memory.mcp_server import WorkerLauncher
        from prototypes.assertion_memory.thought_worker import ThoughtWorker

        old_event = self.record(OLD,'old')
        with patch('prototypes.assertion_memory.mcp_server.subprocess.Popen') as spawn:
            self.assertFalse(WorkerLauncher(self.config).start()['scheduled'])
            with authenticated_fresh_mac():
                registration = self.service.ingest_register(PRODUCER,FRESH)
                fresh_event = self.record(FRESH,'admitted',registration)
            self.assertTrue(WorkerLauncher(self.config).start()['scheduled'])
            spawn.assert_called_once()
        worker = ThoughtWorker(self.service.queue,self.service.engine,lock_owned=True,
                               processing_session_ids=_processing_selector(self.config))
        self.assertIsNone(worker._claim_source(event_id=old_event))
        claim = worker._claim_source()
        self.assertEqual(claim.job.event_id,fresh_event)
        self.assertIsNone(worker._claim_source())
        self.assertEqual(self.service.queue.get(old_event).attempts,0)

    def test_owner_selection_can_only_narrow_fresh_admission(self):
        from prototypes.assertion_memory.processing_control import INITIAL
        from prototypes.assertion_memory.queue_session_control import status, update

        legacy = self.record(OLD,'older')
        with authenticated_fresh_mac():
            r1=self.service.ingest_register(PRODUCER,FRESH)
            r2=self.service.ingest_register(PRODUCER,NEXT)
            first=self.record(FRESH,'fresh-one',r1)
            second=self.record(NEXT,'fresh-two',r2)
        spoof=self.record(FRESH,'spoof',r1,2)
        with self.store.transaction(write=True) as db:
            db.executemany('INSERT OR IGNORE INTO memory_event_positions VALUES (?,1)',
                           [(i,) for i in (legacy,first,second,spoof)])
            state=status(db,stage='prod',fresh_only=True)
            self.assertEqual(state['revision'],INITIAL)
            self.assertEqual({x['id'] for x in state['sessions']},{FRESH,NEXT})
            with self.assertRaises(ValueError):
                update(db,selected_session_ids=[OLD],expected_revision=INITIAL,stage='prod',fresh_only=True)
            state=update(db,selected_session_ids=[FRESH],expected_revision=INITIAL,stage='prod',fresh_only=True)
        self.assertEqual(self.eligible(),{first})
        with self.store.transaction(write=True) as db:
            update(db,selected_session_ids=[],expected_revision=state['revision'],stage='prod',fresh_only=True)
        self.assertFalse(_eligible_processing_work(self.config))
        self.assertEqual(self.eligible(),set())

    def test_no_union_with_legacy_selection(self):
        for legacy in (None,[OLD]):
            with self.assertRaises(ValueError):
                ServiceConfig(db=self.db,provider='none',processing_session_ids=legacy,
                              auto_process_fresh_mac=True)

    def test_only_pinned_machine_route_can_mark_events(self):
        import asyncio
        import sys
        from hashlib import sha256
        from uuid import uuid4
        from httpx import AsyncClient, ASGITransport
        from prototypes.assertion_memory.operation_registry import create_operations
        from prototypes.assertion_memory.release_catalog import export

        async def exercise():
            tools = Path(__file__).resolve().parents[3]/'tools/assertion-memory-hosted-mcp'
            with patch.object(sys,'path',[str(tools),*sys.path]):
                from compile_functional_release import compile_release
                from vm_relay import make_app
            release='assertion-memory-v9.9.9'
            canonical=await export('a'*40)
            pins={k:canonical[k] for k in ('revision','module_sha256','schema_sha256')}
            pins['registry']='base-typed-operations-v1'
            catalog=compile_release(canonical,pins,effects=False)
            profile={'lane':'prod','base_release':release,'enabled_capabilities':['baseline_read'],
                     'catalog_sha256':catalog['catalog_sha256'],'require_catalog_identity':True}
            site,mac,other=('b'*64,'c'*64,'d'*64)
            mac_digest,other_digest=(sha256(t.encode()).hexdigest() for t in (mac,other))
            ops=create_operations(self.config,self.store)
            app=make_app(profile,catalog,site,lambda:(release,'f'*64),operations=ops,
                         machine_digests=(mac_digest,other_digest),fresh_mac_digest=mac_digest)
            async with AsyncClient(transport=ASGITransport(app=app),base_url='https://memory.test') as client:
                async def call(token,op,args,expected=None):
                    body={'request_id':str(uuid4()),'operation':op,'arguments':args}
                    if expected is not None:body['expected']=expected
                    result=await client.post('/memory-api-prod',json=body,headers={'authorization':'Bearer '+token})
                    return result.status_code,result.json()

                pairs={}
                for key,session in ((other,OTHER),(mac,FRESH)):
                    status,descriptor=await call(key,'catalog',{})
                    self.assertEqual(status,200)
                    pins={k:descriptor[k] for k in ('release','generation','catalog_sha256')}
                    stream_args={'producer_id':PRODUCER,'session_id':session}
                    status,wire=await call(key,'memory_ingest_register',stream_args,pins)
                    self.assertEqual(status,200)
                    receipt=wire['result'];pairs[session]=receipt
                    delivery={k:receipt[k] for k in ('stream_id','epoch')} | {'seq':1}
                    capture={'session_id':session,'message_id':'one','actor_id':'fixture-owner',
                             'role':'user','text':'Only invented test data','source':'fixture://http','delivery':delivery}
                    status,wire=await call(key,'memory_capture_message',capture,pins)
                    self.assertEqual(status,200)
                    self.assertEqual(wire['result']['delivery']['acked_through'],1)
                    self.assertEqual(wire['result']['event']['id'] in self.eligible(),key==mac)

                from prototypes.assertion_memory.database_clear import clear,request_revision
                with self.store.transaction(write=True) as db:
                    clear(db,stage='prod',expected_revision=request_revision(db,stage='prod'))
                old=pairs[FRESH]
                previous={'producer_id':PRODUCER,'session_id':FRESH,
                          'previous_stream_id':old['stream_id'],'previous_epoch':old['epoch']}
                self.assertNotEqual((await call(other,'memory_ingest_register',previous,pins))[0],200)
                status,wire=await call(mac,'memory_ingest_register',previous,pins)
                self.assertEqual(status,200)
                successor=wire['result'];witness=successor['database_clear_reset']
                report={k:successor[k] for k in ('stream_id','epoch')}|{'report_seq':1,
                    'observed_seq':0,'scan_state':'current','gap_count':0,'reset_generation':witness['generation']}
                self.assertNotEqual((await call(other,'memory_ingest_report',report,pins))[0],200)
                fresh={**capture,'session_id':FRESH,'message_id':'new-after-ack',
                       'delivery':{k:successor[k] for k in ('stream_id','epoch')}|{'seq':1}}
                self.assertNotEqual((await call(mac,'memory_capture_message',fresh,pins))[0],200)
                status,wire=await call(mac,'memory_ingest_report',report,pins)
                self.assertEqual(status,200)
                self.assertEqual(wire['result']['database_clear_ready_generation'],witness['generation'])
                self.assertNotEqual((await call(mac,'memory_capture_message',fresh|{'message_id':'one'},pins))[0],200)
                status,wire=await call(mac,'memory_capture_message',fresh,pins)
                self.assertEqual(status,200)
                self.assertEqual(wire['result']['delivery']['acked_through'],1)
                self.assertEqual(self.eligible(),{wire['result']['event']['id']})

        asyncio.run(exercise())

    def test_reconcile_recovers_wake_missed_while_manual_worker_holds_lock(self):
        import asyncio
        import fcntl
        from prototypes.assertion_memory.fresh_processing import reconcile_fresh_mac
        from prototypes.assertion_memory.mcp_server import WorkerLauncher

        async def exercise():
            with authenticated_fresh_mac():
                stream = self.service.ingest_register(PRODUCER,FRESH)
                self.record(FRESH,'admitted',stream)
            launcher = WorkerLauncher(self.config)
            with patch('prototypes.assertion_memory.mcp_server.subprocess.Popen') as spawn:
                lock = self.enterContext(open(self.db+'.worker.lock','a'))
                fcntl.flock(lock,fcntl.LOCK_EX)
                self.assertFalse(launcher.start()['scheduled'])
                spawn.assert_not_called()
                fcntl.flock(lock,fcntl.LOCK_UN)
                launched = asyncio.Event()
                parent = asyncio.get_running_loop()

                def launch():
                    if launcher.start()['scheduled']:
                        parent.call_soon_threadsafe(launched.set)

                async with reconcile_fresh_mac(launch,interval=0.01):
                    await asyncio.wait_for(launched.wait(),timeout=2)
                spawn.assert_called_once()

        asyncio.run(exercise())
