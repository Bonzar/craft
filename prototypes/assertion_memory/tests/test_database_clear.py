from contextlib import nullcontext
from hashlib import sha256
import hmac
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from prototypes.assertion_memory.queue_pause_proof import ENVELOPE, ProvenOwner, canonical, make_owner_verifier, reserve_once
from prototypes.assertion_memory.queue_pause_rest import make_queue_pause_rest

from prototypes.assertion_memory import database_clear as reset,processing_control
from prototypes.assertion_memory.capture_batch import schema as batch_schema
from prototypes.assertion_memory.ingest_streams import register
from prototypes.assertion_memory.queue_owner_domain import owner_operations
from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_runtime import initialize_empty


class DatabaseClearTest(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path=Path(temp.name).resolve()/'fixture.sqlite3'
        self.store=MemoryStore(self.path)
        self.addCleanup(self.store.close)
        self.assertTrue(initialize_empty(self.store))
        self.service=MemoryService(self.store)

    def capture(self, ident, delivery=None):
        return self.service.capture_message('fixture-session',ident,'fixture-user','user',
            'deletable synthetic words',1,'fixture',delivery=delivery)

    def signed_callbacks(self, stage):
        return owner_operations(database=self.path,identity=lambda:('release','generation'),
            release_lease=nullcontext,stage=stage)

    def test_both_lanes_clear_held_batch_graph_artifacts_streams_and_preserve_controls(self):
        for lane in ('test','prod'):
            with self.subTest(stage=lane):
                with self.store.transaction(write=True) as db:
                    stream=register(db,'fictional-device','fixture-session')
                delivery=dict(stream_id=stream['stream_id'],epoch=stream['epoch'],seq=1)
                ident='first-'+lane
                event=self.capture(ident,delivery)['event']['id']
                with self.store.transaction(write=True) as db:
                    batch_schema(db)
                    db.execute("INSERT INTO memory_capture_batches VALUES(?,?,?,?,?,?,NULL)",
                        ('e'*64,stream['stream_id'],'fixture-session','m','r','pending'))
                    db.execute('INSERT INTO memory_capture_batch_members VALUES (?,?,?)',('e'*64,0,event))
                    db.execute("INSERT INTO memory_assertions VALUES(?,?,?,?,?,?,?,?,?)",
                               ('private-a','erase-search-marker','a','s','e',None,'time','s','{}'))
                    db.execute("INSERT INTO memory_edges VALUES(?,?,?,?,?,?)",
                               ('private-edge','private-a','private-a','rel','time','{}'))
                    db.execute("INSERT INTO memory_thoughts VALUES('private-thought',1)")
                    db.execute("INSERT INTO memory_thought_fts VALUES('private-thought','erase-search-marker','')")
                    db.execute('INSERT INTO memory_artifacts VALUES(?,?,?,?,?,?)',
                        ('artifact-'+'f'*64,'f'*64,'image/png',4,b'xxxx','stamp'))
                    paused,rev=processing_control.read_control(db)
                    if not paused:
                        processing_control.set_control(db,paused=True,expected_revision=rev)
                    db.execute('CREATE TABLE IF NOT EXISTS memory_test_queue_sessions(singleton INT,selected TEXT,revision TEXT)')
                    db.execute("INSERT OR REPLACE INTO memory_test_queue_sessions VALUES(1,'[\"fixture-session\"]','choice')")
                    version=db.execute('SELECT version FROM memory_state').fetchone()[0]
                    keep=processing_control.read_control(db)
                    pristine=reset.revision(db)
                self.assertEqual(reset.describe(self.store._connection,stage=lane),
                    {'stage':lane,'revision':pristine,'can_clear':True,
                     'capture_rearm':'not_cleared' if pristine==reset.INITIAL else 'not_required'})
                action=self.signed_callbacks(lane)
                request_revision=action('clear_prepare',{})['revision']
                got=action('clear_commit',{'expected_revision':request_revision})
                self.assertTrue(got['cleared'])
                self.assertEqual(got['stage'],lane)
                self.assertNotEqual(got['revision'],request_revision)
                self.assertEqual(got['revision'],action('clear_prepare',{})['revision'])
                with self.store.transaction() as db:
                    for name in reset._inventory(db) & (reset.DATA | reset.VIRTUAL) - {'memory_source_admission_clock'}:
                        self.assertEqual(db.execute('SELECT count(*) FROM '+name).fetchone()[0],0,name)
                    self.assertEqual(db.execute("SELECT count(*) FROM memory_fts WHERE memory_fts MATCH 'marker'").fetchone()[0],0)
                    self.assertEqual(db.execute("SELECT count(*) FROM memory_thought_fts WHERE memory_thought_fts MATCH 'marker'").fetchone()[0],0)
                    self.assertEqual(processing_control.read_control(db),keep)
                    self.assertEqual(db.execute('SELECT count(*) FROM memory_thought_runtime').fetchone()[0],1)
                    self.assertEqual(db.execute('SELECT version FROM memory_state').fetchone()[0],version+1)
                    self.assertEqual(db.execute('SELECT selected FROM memory_test_queue_sessions').fetchone()[0],'["fixture-session"]')
                with self.assertRaises(processing_control.Conflict):
                    action('clear_commit',{'expected_revision':request_revision})
                with self.assertRaisesRegex(ValueError,'cleared memory generation'):
                    self.capture(ident)
                with self.assertRaisesRegex(ValueError,'unknown ingest stream'):
                    self.capture('new-write-stale-delivery',delivery)

    def test_live_ingest_and_fresh_worker_share_a_new_admission_clock_after_reset(self):
        from prototypes.assertion_memory.source_admission import stamp,unchanged
        old=self.capture('old-id')['event']['id']
        with self.store.transaction() as db:
            previous=stamp(db)
            self.assertTrue(unchanged(db,previous[0],'raw',old))
        action=self.signed_callbacks('test')
        action('clear_commit',{'expected_revision':action('clear_prepare',{})['revision']})
        with self.store.transaction() as db:
            fresh=stamp(db)
            self.assertEqual(fresh[0],0)
            self.assertNotEqual(fresh[1],previous[1])
            self.assertFalse(unchanged(db,previous[0],'raw',old))
        # The same live instance receives a fresh message without a restart.
        with self.store.transaction(write=True) as db:
            stream=register(db,'fictional-device','fixture-session')
        delivery=dict(stream_id=stream['stream_id'],epoch=stream['epoch'],seq=1)
        captured=self.capture('fresh-id',delivery)
        with MemoryStore(self.path) as worker:
            MemoryService(worker)
            with worker.transaction() as db:
                current=stamp(db)
                self.assertEqual(current[1],fresh[1])
                self.assertGreater(current[0],0)
                self.assertTrue(unchanged(db,current[0],'raw',captured['event']['id']))

    def test_same_enrolled_producer_can_resume_only_after_exact_clear_witness(self):
        from prototypes.assertion_memory.fresh_processing import authenticated_fresh_mac
        from prototypes.assertion_memory.stream_clear_transition import TRANSITIONS
        producer='jarvis:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        unrelated='jarvis:bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
        session='11111111-1111-1111-1111-111111111111'
        foreign='22222222-2222-2222-2222-222222222222'
        def capture(session,message,pair):
            return self.service.capture_message(session,message,'synthetic-author','user',
                'private fixture to erase',None,'fixture://native-clear',
                delivery={'stream_id':pair['stream_id'],'epoch':pair['epoch'],'seq':1})
        def previous(pair):
            return {'previous_stream_id':pair['stream_id'],'previous_epoch':pair['epoch']}
        with authenticated_fresh_mac():
            old=self.service.ingest_register(producer,session)
            self.assertNotIn('database_clear_reset',self.service.ingest_register(producer,session,**previous(old)))
            first=capture(session,'before-clear',old)
        self.assertEqual(first['delivery']['acked_through'],1)
        # Same user-level Base API, but no evidence this producer was enrolled.
        ordinary=self.service.ingest_register(producer,foreign)
        action=self.signed_callbacks('prod')
        clear_receipt=action('clear_commit',{'expected_revision':action('clear_prepare',{})['revision']})
        self.assertEqual(clear_receipt['capture_rearm'],'waiting')
        self.assertEqual(action('clear_prepare',{})['capture_rearm'],'waiting')
        with self.assertRaisesRegex(ValueError,'No authenticated cleared stream witness'):
            self.service.ingest_register(producer,session,**previous(old))
        with authenticated_fresh_mac():
            for wrong_producer,wrong_session,wrong in (
                (unrelated,session,old),(producer,foreign,ordinary),
                (producer,session,old|{'epoch':'33333333-3333-3333-3333-333333333333'})):
                with self.assertRaises(ValueError):
                    self.service.ingest_register(wrong_producer,wrong_session,**previous(wrong))
            with self.assertRaises(ValueError):
                self.service.ingest_register(producer,session,previous_epoch=old['epoch'])
            with self.store.transaction() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM memory_ingest_streams').fetchone()[0],0)
            recovered=self.service.ingest_register(producer,session,**previous(old))
            witness=recovered['database_clear_reset']
            self.assertEqual(witness['protocol'],'owner-clear-v1')
            self.assertEqual(witness['producer_id'],producer)
            self.assertEqual(witness['session_id'],session)
            for old_key in ('stream_id','epoch'):
                self.assertEqual(witness['previous_'+old_key],old[old_key])
                self.assertNotEqual(recovered[old_key],old[old_key])
            self.assertTrue(witness['cleared_at'].endswith('+00:00'))
            self.assertEqual(recovered['acked_through'],0)
            self.assertEqual(self.service.ingest_register(producer,session,**previous(old)),recovered)
            with self.assertRaisesRegex(ValueError,'not re-armed'):
                capture(session,'before-witness-ack',recovered)
            with self.assertRaisesRegex(ValueError,'not re-armed'):
                self.service.ingest_register(producer,session,replace_epoch=recovered['epoch'])
            def ack(gen):
                return self.service.ingest_report(recovered['stream_id'],recovered['epoch'],1,0,'current',0,
                                                  reset_generation=gen)
            with self.assertRaisesRegex(ValueError,'No authenticated current'):
                ack('f'*32)
            ready=ack(witness['generation'])
            self.assertEqual(ready['database_clear_ready_generation'],witness['generation'])
            self.assertEqual(ack(witness['generation']),ready)
            self.assertEqual(action('clear_prepare',{})['capture_rearm'],'confirmed')
            with self.assertRaisesRegex(ValueError,'cleared memory generation'):
                capture(session,'before-clear',recovered)
            with self.assertRaisesRegex(ValueError,'unknown ingest stream'):
                capture(session,'old-epoch-replay',old)
            after=capture(session,'after-clear',recovered)
            self.assertEqual(after['delivery']['acked_through'],1)
            self.assertEqual(self.service.ingest_register(producer,session,**previous(old))['acked_through'],1)
            with self.store.transaction() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM memory_raw_events').fetchone()[0],1)
                proof=db.execute('SELECT prior_digest,successor_digest FROM '+TRANSITIONS).fetchall()
                self.assertEqual(len(proof),1)
                self.assertTrue(all(type(v) is bytes and len(v)==32 for v in proof[0]))
            replaced=self.service.ingest_register(producer,session,replace_epoch=recovered['epoch'])
            self.assertNotEqual(replaced['epoch'],recovered['epoch'])
            with self.assertRaisesRegex(ValueError,'superseded'):
                self.service.ingest_register(producer,session,**previous(old))

    def test_older_witness_cannot_count_as_a_current_expected_rearm(self):
        from prototypes.assertion_memory.fresh_processing import authenticated_fresh_mac
        producer='jarvis:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        older='11111111-1111-1111-1111-111111111111'
        first='22222222-2222-2222-2222-222222222222'
        second='33333333-3333-3333-3333-333333333333'
        action=self.signed_callbacks('prod')
        def clear():
            view=action('clear_prepare',{})
            return action('clear_commit',{'expected_revision':view['revision']})
        def recover(session,pair):
            result=self.service.ingest_register(producer,session,previous_stream_id=pair['stream_id'],
                                                previous_epoch=pair['epoch'])
            generation=result['database_clear_reset']['generation']
            self.service.ingest_report(result['stream_id'],result['epoch'],1,0,'current',0,
                                       reset_generation=generation)
        with authenticated_fresh_mac():
            historic=self.service.ingest_register(producer,older)
            self.assertEqual(clear()['capture_rearm'],'waiting')
            a=self.service.ingest_register(producer,first)
            b=self.service.ingest_register(producer,second)
            self.assertEqual(clear()['capture_rearm'],'waiting')
            recover(older,historic)
            self.assertEqual(action('clear_prepare',{})['capture_rearm'],'waiting')
            recover(first,a)
            self.assertEqual(action('clear_prepare',{})['capture_rearm'],'waiting')
            recover(second,b)
            self.assertEqual(action('clear_prepare',{})['capture_rearm'],'confirmed')

    def test_replace_or_release_change_invalidates_a_prepared_revision(self):
        self.capture('seeded-old')
        original=self.signed_callbacks('test')('clear_prepare',{})['revision']
        new=self.path.with_name('replacement.sqlite3')
        with MemoryStore(new) as store:
            service=MemoryService(store)
            service.capture_message('fresh','fresh-msg','actor','user','fixture-only',1,'fixture')
        # Exercise the binding directly with a separate temporary DB. These
        # immutable release/version identities stand in for trusted deployment metadata.
        with sqlite3.connect(new) as conn:
            self.assertNotEqual(reset.request_revision(conn,stage='test',binding=[
                (new.stat().st_dev,new.stat().st_ino),('release','generation')]),original)
        with self.store.transaction() as db:
            self.assertNotEqual(reset.request_revision(db,stage='test',binding=[
                (self.path.stat().st_dev,self.path.stat().st_ino),('other-release','generation')]),original)

    def test_unknown_schema_and_busy_fences_leave_data_untouched(self):
        self.capture('no-delete')
        action=self.signed_callbacks('test')
        revision=action('clear_prepare',{})['revision']
        self.assertNotEqual(revision,self.signed_callbacks('prod')('clear_prepare',{})['revision'])
        for worker in (False,True):
            with reset.database_lease(self.path,exclusive=worker,worker=worker):
                with self.assertRaises(BlockingIOError):
                    action('clear_commit',{'expected_revision':revision})
        with self.store.transaction(write=True) as db:
            db.execute('CREATE TABLE other_auth_data(x)')
        self.assertEqual(action('clear_prepare',{})['can_clear'],False)
        with self.assertRaisesRegex(ValueError,'unclassified'):
            action('clear_commit',{'expected_revision':revision})
        self.assertEqual(self.store._connection.execute('SELECT count(*) FROM memory_raw_events').fetchone()[0],1)
        self.assertEqual(reset.revision(self.store._connection),reset.INITIAL)


class DatabaseClearWireTest(unittest.IsolatedAsyncioTestCase):
    async def test_exact_path_lane_proof_and_nonce(self):
        secret=b'K'*32
        actor='c'*64
        read,write='a'*64,'b'*64
        clock=lambda:1780000000
        with tempfile.TemporaryDirectory() as tmp:
            db_path=Path(tmp).resolve()/'synthetic.sqlite3'
            with MemoryStore(db_path) as store:
                service=MemoryService(store)
                service.capture_message('session','old','actor','user','synthetic',1,'fixture')
                perform=owner_operations(database=db_path,release_lease=nullcontext,identity=lambda:'active',stage='prod')
                def change(*,stage,proof,expected_revision):
                    return perform('clear_commit',{'expected_revision':expected_revision},
                        authorize=lambda db:reserve_once(db,stage=stage,path=reset.PATH,
                            proof=proof,expected_revision=expected_revision,clock=clock))
                app=make_queue_pause_rest(stage='prod',enabled=True,port=8903,path=reset.PATH,
                    read_sha256=sha256(read.encode()).hexdigest(),write_sha256=sha256(write.encode()).hexdigest(),
                    actor_sha256=actor,verify_owner=make_owner_verifier(secret,actor,clock=clock,lane='prod',path=reset.PATH),
                    prepare=lambda:perform('clear_prepare',{}),change=change)
                revision=perform('clear_prepare',{})['revision']
                body=json.dumps({'expected_revision':revision}).encode()
                def proof(lane,path,nonce):
                    envelope=ProvenOwner(actor,revision,clock(),clock()+15,nonce,lane=lane,path=path)
                    mac=hmac.digest(secret,canonical(body,envelope),'sha256').hex()
                    return [(k,v.encode()) for k,v in zip(ENVELOPE,[actor,str(clock()),str(clock()+15),nonce,mac])]
                async def request(method,key,*,extra=(),outer=reset.PATH,negotiated=True):
                    observed=[]
                    async def recv(): return {'type':'http.request','body':body}
                    async def send(event): observed.append(event)
                    await app({'type':'http','path':outer,'raw_path':outer.encode(),'method':method,
                        'client':('127.0.0.1',1),'server':('127.0.0.1',8903),'headers':[
                        (b'authorization',f'Bearer {key}'.encode()),(b'host',b'127.0.0.1:8903'),
                        (b'content-type',b'application/json'),
                        *([(reset.RESET_HEADER,reset.RESET_FORMAT)] if negotiated else []),*extra]},recv,send)
                    return observed[0]['status'],json.loads(observed[1]['body'])
                self.assertEqual((await request('GET',read,negotiated=False))[0],404)
                self.assertEqual((await request('PUT',write,negotiated=False,extra=proof('prod',reset.PATH,'3'*32)))[0],404)
                self.assertEqual(await request('GET',read),(200,perform('clear_prepare',{})))
                self.assertEqual((await request('PUT',write))[0],403)
                self.assertEqual((await request('PUT',write,extra=proof('test',reset.PATH,'1'*32)))[0],403)
                self.assertEqual((await request('PUT',write,extra=proof('prod','/api/v1/queue-pause','2'*32)))[0],403)
                self.assertEqual(store._connection.execute('SELECT count(*) FROM memory_raw_events').fetchone()[0],1)
                valid=proof('prod',reset.PATH,'3'*32)
                status,result=await request('PUT',write,extra=valid)
                self.assertEqual(status,200)
                self.assertTrue(result['cleared'])
                self.assertEqual((await request('PUT',write,extra=valid))[0],403)
                self.assertEqual((await request('PUT',write,extra=proof('prod',reset.PATH,'4'*32)))[0],409)
                self.assertEqual((await request('GET',read,outer=reset.PATH+'/'))[0],404)

    async def test_disconnect_does_not_allow_reset_ahead_of_ongoing_handler(self):
        import asyncio
        from types import SimpleNamespace
        from prototypes.assertion_memory.http_server import AuthenticatedMemoryApp
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp).resolve()/'fixture.sqlite3'
            with MemoryStore(path) as store:
                MemoryService(store)
                enter,release,finished=asyncio.Event(),asyncio.Event(),asyncio.Event()
                async def old(scope,receive,send):
                    enter.set()
                    await release.wait()
                    # Representative completed work after a client disconnect.
                    await send({'type':'http.response.start','status':200,'headers':[]})
                    finished.set()
                token='q'*64
                app=AuthenticatedMemoryApp(old,token,queue_pause=SimpleNamespace(queue_clear_database=path))
                async def recv(): return {'type':'http.request','body':b''}
                async def send(_): pass
                scope={'type':'http','path':'/mcp','method':'POST',
                       'headers':[(b'authorization',f'Bearer {token}'.encode())]}
                request=asyncio.create_task(app(scope,recv,send))
                await asyncio.wait_for(enter.wait(),1)
                request.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await request
                perform=owner_operations(database=path,release_lease=nullcontext,identity=lambda:'active')
                rev=perform('clear_prepare',{})['revision']
                with self.assertRaises(BlockingIOError):
                    perform('clear_commit',{'expected_revision':rev})
                release.set()
                await asyncio.wait_for(finished.wait(),1)
                self.assertTrue(perform('clear_commit',{'expected_revision':rev})['cleared'])
