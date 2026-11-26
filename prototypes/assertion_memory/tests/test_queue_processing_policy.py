from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase

from prototypes.assertion_memory.capture_batch import authorized_machine_batch, capture, needs_wake
from prototypes.assertion_memory.mcp_server import ServiceConfig, _processing_selector
from prototypes.assertion_memory.processing_control import Conflict, INITIAL, set_control
from prototypes.assertion_memory.queue_processing_policy import describe, update
from prototypes.assertion_memory.queue_session_control import update as legacy_update
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_processing_selection import (
    FRESH_MAC_ONLY, OWNER_POLICY, processing_cohort, processing_deadline)
from prototypes.assertion_memory.thought_worker import ThoughtWorker

DAY='2026-11-26T08:00:00.000000+00:00'
OLD='2026-11-25T08:00:00.000000+00:00'
SOON='2026-11-26T08:00:00.000001+00:00'

def policy(*, mode='all', include=(), exclude=(), lower=None, upper=None):
    return dict(mode=mode,included_session_ids=list(include),excluded_session_ids=list(exclude),
                added_from=lower,added_through=upper)


class ProcessingPolicy(TestCase):
    def setUp(self):
        self.position=0
        path=Path(self.enterContext(TemporaryDirectory()))/'memory.sqlite3'
        self.store=self.enterContext(MemoryStore(path))
        self.config=ServiceConfig(db=str(path),provider='subprocess',command=('test',),processing_session_ids=())
        self.service=self.config.service(self.store)
        self.worker=ThoughtWorker(self.service.queue,self.service.engine,processing_session_ids=OWNER_POLICY)

    def event(self, session, message, added):
        self.position+=1
        event_id=self.service.capture_message(session,message,'actor','user','fixture message',self.position,
                                              source='fixture://message/'+message)['event']['id']
        with self.store.transaction(write=True) as db:
            db.execute('UPDATE memory_capture_jobs SET created_at=? WHERE event_id=?',(added,event_id))
        return event_id

    def set_scope(self, *, stage='test', **kw):
        with self.store.transaction(write=True) as db:
            now=describe(db,stage=stage)
            return update(db,stage=stage,expected_revision=now['revision'],policy=policy(**kw))

    def test_default_and_legacy_empty_choice_is_not_silently_removed(self):
        with self.store.transaction(write=True) as db:
            self.assertEqual(describe(db,stage='test')['policy']['mode'],'all')
            self.assertEqual(_processing_selector(ServiceConfig(db=self.config.db,provider='none'),db),None)
        self.event('session-a','one',DAY)
        with self.store.transaction(write=True) as db:
            legacy_update(db,selected_session_ids=['session-a'],expected_revision=INITIAL)
            selected=describe(db,stage='test')
            self.assertEqual(selected['policy']['included_session_ids'],('session-a',))
            legacy_update(db,selected_session_ids=[],expected_revision=selected['revision'])
            held=describe(db,stage='test')
            self.assertEqual((held['policy']['mode'],held['policy']['included_session_ids']),('include',()))
            with self.assertRaises(Conflict):
                update(db,stage='test',expected_revision=INITIAL,policy=policy())
            changed=update(db,stage='test',expected_revision=held['revision'],policy=policy())
            self.assertEqual(changed['policy']['mode'],'all')
            self.assertIs(_processing_selector(self.config,db),OWNER_POLICY)

    def test_worker_claims_source_only_if_session_and_enqueue_time_match(self):
        self.event('one','earlier',OLD)
        wanted=self.event('one','exact',DAY)
        self.event('two','excluded',DAY)
        self.event('one','later',SOON)
        self.set_scope(exclude=('two',),lower='2026-11-26T11:00:00+03:00',upper=DAY)
        with self.store.transaction() as db:
            self.assertIsNotNone(processing_deadline(db,OWNER_POLICY,now=DAY,lane='source'))
        self.assertEqual(self.worker._claim_source().job.event_id,wanted)
        self.assertIsNone(self.worker._claim_source())
        self.set_scope(mode='include',include=('two',),lower=DAY,upper=DAY)
        self.assertIsNotNone(self.worker._claim_source())
        self.assertIsNone(self.worker._claim_source())

    def test_startup_recovery_alias_and_prod_projection_handle_active_dates(self):
        from prototypes.assertion_memory.thought_processing import _recover_selected
        from prototypes.assertion_memory.queue_status import build_queue_status
        recover=self.event('session-a','one',DAY)
        isolate=self.event('session-b','two',OLD)
        self.set_scope(stage='prod',lower=DAY,upper=DAY)
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_capture_jobs SET status='running',lease_token='fixture'")
        _recover_selected(self.service.queue,OWNER_POLICY)
        with self.store.transaction() as db:
            states={tuple(row) for row in db.execute('SELECT event_id,status FROM memory_capture_jobs')}
            self.assertEqual(states,{(recover,'pending'),(isolate,'running')})
            dashboard=build_queue_status(db,release_version='fixture',stage='prod',processing_session_ids=(),
                                         model_configured=True,interactive_enabled=False,auto_process_fresh_mac=True)
            self.assertEqual([q['id'] for q in dashboard['queues']],['source','maintenance','interactive'])

    def test_maintenance_uses_cause_enqueue_time_and_keeps_composite_atomic(self):
        work=self.worker.work
        for thought,session,cause,stamp in [
            ('accepted','one','new',DAY),('mixed','one','new',DAY),('mixed','two','also',DAY),
            ('old','one','old',OLD),('future','one','future',SOON),('unknown',None,'missing',DAY)]:
            with processing_cohort([session] if session else []):
                obj=SimpleNamespace(id=thought,version=1)
                work.request(obj,'consolidate',(cause,))
            import json
            with self.store.transaction(write=True) as db:
                db.execute('UPDATE memory_thought_work_causes SET added_at=? WHERE thought_id=? AND cause=?',
                           (stamp,thought,json.dumps((cause,))))
        self.set_scope(exclude=('two',),lower=DAY,upper=DAY)
        self.assertEqual(work.claim()['thought_id'],'accepted')
        self.assertIsNone(work.claim())
        self.set_scope()
        remaining={work.claim()['thought_id'] for _ in range(4)}
        self.assertEqual(remaining,{'mixed','old','future','unknown'})

    def test_neutral_packet_is_only_dispatched_when_all_jobs_match_and_not_paused(self):
        stream=self.service.ingest_register('machine-owner','session-b')
        messages=[{'session_id':'session-b','message_id':str(i),'actor_id':'actor','role':'user',
                   'source':'fixture://batch/'+str(i),'text':'fixture',
                   'delivery':{k:stream[k] for k in ('stream_id','epoch')}|{'seq':i}} for i in (1,2)]
        with authorized_machine_batch():
            receipt=capture(self.service,'b'*64,messages)['receipts']
        self.set_scope(mode='include',include=('session-b',),lower=DAY,upper=SOON)
        with self.store.transaction(write=True) as db:
            ids=[r['event']['id'] for r in receipt]
            db.executemany('UPDATE memory_capture_jobs SET created_at=? WHERE event_id=?',zip((DAY,OLD),ids))
            self.assertFalse(needs_wake(db))
            db.execute('UPDATE memory_capture_jobs SET created_at=? WHERE event_id=?',(SOON,ids[1]))
            self.assertTrue(needs_wake(db))
            set_control(db,paused=True,expected_revision=INITIAL)
            self.assertIsNone(processing_deadline(db,FRESH_MAC_ONLY,now=DAY,include_batches=True))

    def test_unknown_and_historical_prod_source_are_not_admitted_by_owner_opt_in(self):
        self.event('historical','one',DAY)
        self.set_scope(stage='prod')
        config=ServiceConfig(db=self.config.db,provider='subprocess',command=('test',),
                             auto_process_fresh_mac=True,processing_session_ids=())
        with self.store.transaction() as db:
            self.assertIs(_processing_selector(config,db),FRESH_MAC_ONLY)
            self.assertIsNone(processing_deadline(db,FRESH_MAC_ONLY,now=DAY))


class OwnerPolicyTransport(TestCase):
    def test_same_signed_contract_on_each_lane_and_stale_revision(self):
        import asyncio
        from contextlib import nullcontext
        import hashlib
        import hmac
        import json
        from prototypes.assertion_memory.queue_owner_domain import owner_operations
        from prototypes.assertion_memory.queue_pause_rest import make_queue_pause_rest
        from prototypes.assertion_memory.queue_pause_proof import ENVELOPE, ProvenOwner, canonical, make_owner_verifier
        from prototypes.assertion_memory.queue_processing_policy import PATH

        async def verify(stage,port):
            path=Path(self.enterContext(TemporaryDirectory()))/'memory.sqlite3'
            self.enterContext(MemoryStore(path))
            actor='a'*64
            key=bytes(range(32))
            read,write=b'b'*64,b'c'*64
            now=1795619000
            perform=owner_operations(database=path,release_lease=nullcontext,
                                     identity=lambda:('fixture','base'),stage=stage)
            endpoint=make_queue_pause_rest(stage=stage,enabled=True,path=PATH,port=port,
                read_sha256=hashlib.sha256(read).hexdigest(),write_sha256=hashlib.sha256(write).hexdigest(),
                actor_sha256=actor,verify_owner=make_owner_verifier(key,actor,lane=stage,path=PATH,clock=lambda:now),
                prepare=lambda:perform('policy_prepare',{}),
                change=lambda *,stage,proof,**args:perform('policy_set',args))
            async def call(method, *, proof_lane=stage, revision=INITIAL):
                document=json.dumps(dict(policy=policy(exclude=('excluded',),lower=DAY),
                                         expected_revision=revision)).encode()
                proof=ProvenOwner(actor,revision,now,now+15,'1'*32,proof_lane,PATH)
                signature=hmac.digest(key,canonical(document,proof),'sha256').hex()
                headers=[(b'host',f'127.0.0.1:{port}'.encode()),
                         (b'authorization',b'Bearer '+(read if method=='GET' else write)),
                         (b'content-type',b'application/json')]
                headers.extend(zip(ENVELOPE,map(str.encode,(actor,str(now),str(now+15),'1'*32,signature))))
                output=[]
                async def receive():return {'type':'http.request','body':document}
                async def send(event):output.append(event)
                await endpoint(dict(type='http',path=PATH,method=method,client=('127.0.0.1',12345),
                                    server=('127.0.0.1',port),headers=headers),receive,send)
                return output[0]['status'],json.loads(output[1]['body'])
            code,value=await call('GET')
            self.assertEqual((code,value['policy']['mode']),(200,'all'))
            self.assertEqual((await call('PUT',proof_lane='test' if stage=='prod' else 'prod'))[0],403)
            code,value=await call('PUT')
            self.assertEqual((code,value['stage'],value['policy']['excluded_session_ids']),(200,stage,['excluded']))
            self.assertEqual((await call('PUT'))[0],409)

        asyncio.run(verify('test',8893))
        asyncio.run(verify('prod',8903))
