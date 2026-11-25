import json
from contextlib import nullcontext, closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from prototypes.assertion_memory.native_pause import make_native_pause,via_test_effects,EFFECTS_CARRIER
from prototypes.assertion_memory.queue_owner_domain import owner_operations
from prototypes.assertion_memory.queue_item_removal import annotate

OWNER='a'*64
PAUSE='b'*64
EFFECT='c'*64


class NativeQueue(unittest.IsolatedAsyncioTestCase):
    async def test_existing_scope_carries_exact_test_operations_and_no_payload_on_generic_effects(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d).resolve()/'fixture.db'
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript("""
                    CREATE TABLE memory_raw_events(id TEXT PRIMARY KEY,session_id TEXT,prompt TEXT,actor TEXT,metadata TEXT);
                    CREATE TABLE memory_event_positions(event_id TEXT REFERENCES memory_raw_events(id),position INTEGER);
                    CREATE TABLE memory_capture_jobs(id TEXT PRIMARY KEY,event_id TEXT REFERENCES memory_raw_events(id),
                       status TEXT,lease_token TEXT,lease_until TEXT,phase TEXT,attempts INTEGER,updated_at TEXT,available_at TEXT);
                    INSERT INTO memory_raw_events VALUES ('fixture-event','fixture-session','secret synthetic','machine','{"role":"user"}');
                    INSERT INTO memory_event_positions VALUES ('fixture-event',1);
                    INSERT INTO memory_capture_jobs VALUES ('fixture-event','fixture-event','waiting_model',NULL,NULL,'extract',0,'0','0');
                """)
                item={'id':'fixture-event'}
                annotate(db,{'queues':[{'id':'source','items':[item]}]})
            perform=owner_operations(database=path,release_lease=nullcontext,identity=lambda:('r','g'))
            legacy=lambda **_:dict(stage='test',paused=False,revision='0'*32)
            app=make_native_pause(actor=OWNER,token=PAUSE,prepare=legacy,change=legacy,
                                  queue_operations=perform,queue_sessions=True)
            async def old(_,receive,send):
                await send({'type':'http.response.start','status':418})
                await send({'type':'http.response.body','body':b'{}'})
            http=via_test_effects(old,app,stage='test',pause_token=PAUSE,effect_token=EFFECT)
            async def do(action,args,*,bearer=PAUSE,owner=OWNER,stage='test'):
                messages=[]
                wire=json.dumps(dict(stage=stage,action=action,actor=owner,arguments=args)).encode()
                async def receive(): return {'type':'http.request','body':wire}
                async def send(value):messages.append(value)
                await http(dict(type='http',method='POST',path=EFFECTS_CARRIER,
                    headers=((b'authorization',('Bearer '+bearer).encode()),(b'content-type',b'application/json'))),receive,send)
                return messages[0]['status'],json.loads(messages[1]['body'])
            self.assertEqual((await do('prepare',{}))[0],200)
            self.assertEqual((await do('sessions_prepare',{},bearer=EFFECT))[0],418)
            self.assertEqual((await do('sessions_prepare',{},owner='d'*64))[0],403)
            self.assertEqual((await do('sessions_prepare',{},stage='prod'))[0],400)
            code,status=await do('sessions_prepare',{})
            self.assertEqual((code,status['sessions'][0]['id']),(200,'fixture-session'))
            args={'selected_session_ids':['fixture-session'],'expected_revision':status['revision']}
            self.assertEqual((await do('sessions_set',args))[0],200)
            self.assertEqual((await do('sessions_set',args))[0],409)
            base={'queue_id':'source','item_id':'fixture-event'}
            code,response=await do('item_preview',base|{'expected_revision':item['preview_revision']})
            self.assertEqual((code,response['source_preview'],response['source_role']),(200,'secret synthetic','user'))
            code,response=await do('item_remove',base|{'expected_revision':item['remove_revision']})
            self.assertEqual((code,response['effect']),(200,'source_and_queue'))
            self.assertEqual((await do('item_preview',base|{'expected_revision':item['preview_revision']}))[0],409)
