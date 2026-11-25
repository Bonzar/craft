import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from prototypes.assertion_memory.processing_control import INITIAL, set_control
from prototypes.assertion_memory.queue_pause_proof import ENVELOPE, ProvenOwner, canonical, make_owner_verifier, reserve_once
from prototypes.assertion_memory.queue_pause_rest import make_queue_pause_rest
from prototypes.assertion_memory.queue_session_control import PATH, annotate_source, status, update, worker_selection_changed
from prototypes.assertion_memory.thought_processing_selection import processing_deadline

NOW=1795619000


class Sessions(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path=Path(temporary.name).resolve()/'test.sqlite3'
        self.db=sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.db.executescript("""
          CREATE TABLE memory_raw_events(id TEXT,session_id TEXT,metadata TEXT);
          CREATE TABLE memory_capture_jobs(id TEXT,event_id TEXT,status TEXT,available_at TEXT,lease_until TEXT);
          CREATE TABLE memory_event_positions(event_id TEXT,position INTEGER);
          INSERT INTO memory_raw_events VALUES ('e','fallback','{"native_session_id":"native.qa"}'),('new','incomplete.qa','{}');
          INSERT INTO memory_capture_jobs VALUES ('j','e','pending','100',NULL),
                                                 ('p','new','waiting_origin','100',NULL);
          INSERT INTO memory_event_positions VALUES ('e',1);
        """)
        self.owner='a'*64
        self.secret=bytes(range(32))
        self.read_key=b'b'*64
        self.write_key=b'c'*64
        def prepare():
            with sqlite3.connect(self.path) as db:
                return status(db)
        def change(*, stage, selected_session_ids, expected_revision, proof):
            with sqlite3.connect(self.path) as db:
                db.execute('BEGIN IMMEDIATE')
                reserve_once(db,stage=stage,expected_revision=expected_revision,
                             proof=proof,path=PATH,clock=lambda:NOW)
                return update(db,selected_session_ids=selected_session_ids,expected_revision=expected_revision)
        self.app=make_queue_pause_rest(stage='test',enabled=True,path=PATH,port=8893,
            read_sha256=hashlib.sha256(self.read_key).hexdigest(),
            write_sha256=hashlib.sha256(self.write_key).hexdigest(),actor_sha256=self.owner,
            verify_owner=make_owner_verifier(self.secret,self.owner,path=PATH,clock=lambda:NOW),
            prepare=prepare,change=change)

    async def call(self, ids=None, expected=INITIAL, nonce='f'*32, *, method='PUT'):
        wire=json.dumps({'selected_session_ids':ids,'expected_revision':expected}).encode()
        proof=ProvenOwner(self.owner,expected,NOW,NOW+15,nonce,'test',PATH)
        sig=hmac.digest(self.secret,canonical(wire,proof),'sha256').hex()
        token=self.write_key if method=='PUT' else self.read_key
        headers=[(b'authorization',b'Bearer '+token),(b'host',b'127.0.0.1:8893'),
                 (b'content-type',b'application/json')]
        headers+=list(zip(ENVELOPE,map(str.encode,(self.owner,str(NOW),str(NOW+15),nonce,sig))))
        output=[]
        async def receive():return {'type':'http.request','body':wire}
        async def send(event):output.append(event)
        await self.app(dict(type='http',method=method,path=PATH,server=('127.0.0.1',8893),
                            client=('127.0.0.1',34000),headers=headers),receive,send)
        return output[0]['status'],json.loads(output[1]['body'])

    async def test_owner_route_and_admission_revision(self):
        code,initial=await self.call(method='GET')
        self.assertEqual(code,200)
        self.assertEqual(initial['selected_session_ids'],[])
        self.assertEqual(initial['sessions'],[{'id':'native.qa','eligible':True,'queued':1}])
        self.assertEqual((await self.call(['incomplete.qa'],nonce='e'*32))[0],400)
        self.assertEqual((await self.call(None,nonce='e'*32))[0],400)
        code,updated=await self.call(['native.qa'])
        self.assertEqual((code,updated['selected_session_ids']),(200,['native.qa']))
        rev=updated['revision']
        self.assertEqual((await self.call(['native.qa'],expected=rev))[0],403)
        self.assertEqual((await self.call([],nonce='e'*32))[0],409)
        self.assertEqual(processing_deadline(self.db,updated['selected_session_ids'],now='999'),'100')
        worker=SimpleNamespace(db=str(self.path),processing_session_ids=('native.qa',),queue_sessions_revision=rev)
        self.assertFalse(worker_selection_changed(worker))
        self.assertEqual((await self.call([],expected=rev,nonce='e'*32))[0],200)
        self.assertTrue(worker_selection_changed(worker))

    def test_reasons_show_excluded_and_incomplete(self):
        data=dict(queues=[{'id':'source','items':[dict(id='j',status='paused'),dict(id='p',status='waiting_origin')]}],
                  processing_control={'paused':False})
        annotate_source(self.db,data,[])
        self.assertEqual([x['hold_reason'] for x in data['queues'][0]['items']],
                         ['session_excluded','incomplete_source'])
        data['processing_control']['paused']=True
        annotate_source(self.db,data,[])
        self.assertEqual([x['hold_reason'] for x in data['queues'][0]['items']],
                         ['global_pause','incomplete_source'])

    def test_maintenance_attribution_no_text(self):
        from prototypes.assertion_memory.queue_session_control import annotate_maintenance
        self.db.executescript("""
          CREATE TABLE memory_thought_work(thought_id TEXT,completed_generation INT);
          CREATE TABLE memory_thought_work_causes(thought_id TEXT,cause TEXT,generation INT);
          CREATE TABLE memory_thought_cause_sessions(thought_id TEXT,cause TEXT,session_id TEXT);
          INSERT INTO memory_thought_work VALUES ('known',3),('unknown',3);
          INSERT INTO memory_thought_work_causes VALUES ('known','x',4),('unknown','old',2);
          INSERT INTO memory_thought_cause_sessions VALUES ('known','x','native.qa'),('unknown','old','native.qa');
        """)
        items=[dict(id='known',status='paused'),dict(id='unknown',status='paused')]
        data={'queues':[dict(id='maintenance',items=items)],'processing_control':{'paused':False}}
        annotate_maintenance(self.db,data,['native.qa'])
        self.assertEqual([i['hold_reason'] for i in items],[None,'session_excluded'])
        data['processing_control']['paused']=True
        annotate_maintenance(self.db,data,['native.qa'])
        self.assertEqual([i['hold_reason'] for i in items],['global_pause','global_pause'])
