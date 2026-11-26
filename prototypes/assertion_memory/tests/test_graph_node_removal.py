from tempfile import TemporaryDirectory
from pathlib import Path
import unittest

from prototypes.assertion_memory import graph_node_removal as nodes
from prototypes.assertion_memory.database_clear import INITIAL, STATE
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_graph import export_graph
from prototypes.assertion_memory.thought_store import ThoughtStore


class RemovalTest(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.dbpath=Path(folder).resolve()/'db'
        self.store = MemoryStore(self.dbpath)
        self.addCleanup(self.store.close)
        self.thoughts = ThoughtStore(self.store)
        self.a = self.thoughts.publish('synthetic alpha')
        self.b = self.thoughts.publish('synthetic beta')
        self.kwargs = dict(stage='test', binding=[('123','456'),('protected-deployment',)],
                           thought_id=self.a.id, thought_version=self.a.version)

    def preview(self, **overrides):
        with self.store.transaction() as db:
            return nodes.preview(db, **self.kwargs|overrides)

    def remove(self, revision, **overrides):
        with self.store.transaction(write=True) as db:
            return nodes.remove(db, expected_revision=revision, **self.kwargs|overrides)

    def test_withdraw_one_keeps_raw_original_historical_version_and_unrelated_node(self):
        self.thoughts.connect(self.a.id, self.b.id, 'synthetic relation')
        with self.store.transaction(write=True) as db:
            db.execute('CREATE TABLE memory_raw_events (id TEXT PRIMARY KEY, prompt TEXT)')
            db.execute('INSERT INTO memory_raw_events VALUES (?,?)', ('synthetic-origin','original stays'))
        initial = self.preview()
        removed = self.remove(initial['revision'])
        self.assertTrue(removed['removed'])
        self.assertIsNone(self.thoughts.get(self.a.id))
        self.assertEqual(self.thoughts.get(self.a.id, self.a.version), self.a)
        self.assertEqual(self.thoughts.get(self.b.id), self.b)
        actual = export_graph(self.thoughts)
        self.assertEqual(removed['source_revision'], actual['source_revision'])
        self.assertNotEqual(removed['source_revision'], initial['source_revision'])
        self.assertEqual([n['thought_ref']['thought_id'] for n in actual['nodes']], [self.b.id])
        self.assertEqual(actual['edges'], [])
        self.assertEqual(len(self.thoughts.links(include_inactive=True)),1)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT prompt FROM memory_raw_events').fetchall()[0][0], 'original stays')
            self.assertEqual(db.execute('SELECT count(*) FROM memory_thought_retirements').fetchone()[0],1)
        with self.assertRaisesRegex(nodes.NodeConflict,'thought_changed_or_missing'):
            self.remove(initial['revision'])

    def test_new_graph_revision_or_other_lane_refuses_stale_confirmation(self):
        preview=self.preview()
        with self.assertRaisesRegex(nodes.NodeConflict,'node_confirmation_stale'):
            self.remove(preview['revision'], stage='prod')
        self.thoughts.publish('unrelated graph change')
        with self.assertRaisesRegex(nodes.NodeConflict,'node_confirmation_stale'):
            self.remove(preview['revision'])
        self.assertEqual(self.thoughts.get(self.a.id), self.a)

    def test_old_version_wrong_generation_and_dependent_thought_refused(self):
        self.thoughts.publish('synthetic alpha updated',thought_id=self.a.id,expected_version=1)
        with self.assertRaisesRegex(nodes.NodeConflict,'thought_changed_or_missing'):
            self.preview()
        self.kwargs['thought_version']=2
        current=self.preview()
        with self.store.transaction(write=True) as db:
            db.execute(f'CREATE TABLE {STATE} (singleton INTEGER PRIMARY KEY,revision TEXT)')
            db.execute(f'INSERT INTO {STATE} VALUES (1,?)', ('f'*32 if INITIAL!='f'*32 else 'e'*32,))
        with self.assertRaisesRegex(nodes.NodeConflict,'node_confirmation_stale'):
            self.remove(current['revision'])
        self.thoughts.publish('another particular meaning',bases=[{'thought_id':self.a.id}])
        with self.assertRaisesRegex(nodes.NodeConflict,'dependent_thoughts'):
            self.preview()

    def test_running_or_pending_work_denied(self):
        with self.store.transaction(write=True) as db:
            db.execute('CREATE TABLE memory_thought_work (thought_id TEXT, lease_token TEXT,lease_until TEXT,'
                       'desired_generation INTEGER,completed_generation INTEGER,status TEXT)')
            db.execute('INSERT INTO memory_thought_work VALUES (?,?,?,?,?,?)',
                       (self.a.id,'lease','future',1,0,'running'))
        with self.assertRaisesRegex(nodes.NodeConflict,'thought_work_in_progress'):
            self.preview()

    def test_private_asgi_real_path_proof_revision_and_replay(self):
        import asyncio
        from contextlib import nullcontext
        import hashlib
        import hmac
        import json
        import time
        from prototypes.assertion_memory.http_server import AuthenticatedMemoryApp
        from prototypes.assertion_memory.queue_owner_domain import owner_operations
        from prototypes.assertion_memory.queue_pause_proof import (
            ENVELOPE, ProvenOwner, canonical, make_owner_verifier, reserve_once)
        from prototypes.assertion_memory.queue_pause_rest import make_queue_pause_rest

        port = 8893
        owner, secret = 'a'*64, b'b'*32
        read, write = 'c'*64, 'd'*64
        digest = lambda token: hashlib.sha256(token.encode('ascii')).hexdigest()
        perform = owner_operations(database=self.dbpath, release_lease=nullcontext,
                                   identity=lambda: ('tag','gen'), stage='test')
        routes={}
        for path in (nodes.PREVIEW_PATH,nodes.PATH):
            def change(*,stage,proof,expected_revision,_path=path,**args):
                if _path==nodes.PREVIEW_PATH:
                    return perform('graph_node_preview',args)
                return perform('graph_node_remove',dict(args,expected_revision=expected_revision),
                    authorize=lambda db:reserve_once(db,stage=stage,expected_revision=expected_revision,
                        proof=proof,path=_path))
            routes[path] = make_queue_pause_rest(stage='test', enabled=True, port=port, path=path,
                read_sha256=digest(read), write_sha256=digest(write), actor_sha256=owner,
                verify_owner=make_owner_verifier(secret, owner, lane='test', path=path),
                prepare=lambda:None, change=change)
        noop = lambda *_: self.fail('Graph route escaped to legacy MCP')
        from types import SimpleNamespace
        app=AuthenticatedMemoryApp(noop,'disabled',queue_pause=SimpleNamespace(queue_item_routes=routes))
        async def request(path, revision, *, nonce, signing_lane='test', sign=True, token=write,
                          peer='127.0.0.1', extra=None):
            data=dict(thought_id=self.a.id,thought_version=self.a.version,expected_revision=revision)
            data.update(extra or {})
            body=json.dumps(data).encode()
            now=int(time.time())
            proof=ProvenOwner(owner,revision,now,now+15,nonce,signing_lane,path)
            signature=hmac.digest(secret,canonical(body,proof),'sha256').hex()
            headers=[(b'host',f'127.0.0.1:{port}'.encode()),(b'content-type',b'application/json'),
                     (b'authorization',('Bearer '+token).encode())]
            if sign:
                headers.extend(zip(ENVELOPE,[v.encode() for v in [owner,str(now),str(now+15),nonce,signature]]))
            seen=[]
            async def receive():return {'type':'http.request','body':body}
            async def send(value):seen.append(value)
            await app(dict(type='http',method='PUT',path=path,raw_path=path.encode(),query_string=b'',
                headers=headers,client=(peer,1234),server=('127.0.0.1',port)),receive,send)
            return seen[0]['status'],json.loads(seen[1]['body'])
        async def test():
            mark='0'*32
            self.assertEqual((await request(nodes.PREVIEW_PATH,mark,nonce='1'*32,sign=False))[0],403)
            self.assertEqual((await request(nodes.PREVIEW_PATH,mark,nonce='1'*32,token=read))[0],403)
            self.assertEqual((await request(nodes.PREVIEW_PATH,mark,nonce='1'*32,peer='192.0.2.1'))[0],403)
            self.assertEqual((await request(nodes.PREVIEW_PATH,mark,nonce='1'*32,extra={'force':True}))[0],400)
            status,preview=await request(nodes.PREVIEW_PATH,mark,nonce='1'*32)
            self.assertEqual(status,200)
            with self.store.transaction() as db:
                self.assertFalse(db.execute("SELECT 1 FROM sqlite_schema WHERE name='memory_queue_pause_nonce'").fetchone())
            key=preview['revision']
            self.assertEqual((await request(nodes.PATH,key,nonce='2'*32,signing_lane='prod'))[0],403)
            self.assertEqual((await request(nodes.PATH,'f'*32,nonce='2'*32)),
                             (409,{'error':'node_confirmation_stale'}))
            self.assertIsNotNone(self.thoughts.get(self.a.id))
            status,receipt=await request(nodes.PATH,key,nonce='2'*32)
            self.assertEqual(status,200)
            self.assertTrue(receipt['removed'])
            self.assertEqual(receipt['source_revision'],export_graph(self.thoughts)['source_revision'])
            self.assertEqual((await request(nodes.PATH,key,nonce='2'*32))[0],403)
        asyncio.run(test())


if __name__ == '__main__':
    unittest.main()
