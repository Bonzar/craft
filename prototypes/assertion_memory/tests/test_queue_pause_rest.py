import asyncio
from contextlib import closing
from hashlib import sha256
import hmac
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from prototypes.assertion_memory.queue_pause_rest import PATH, make_queue_pause_rest
from prototypes.assertion_memory.processing_control import INITIAL, read_control, set_control


class TestREST(unittest.IsolatedAsyncioTestCase):
    read, write, owner = b'a'*64, b'b'*64, b'c'*64
    sha = staticmethod(lambda token:sha256(token).hexdigest())

    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db=Path(temp.name)/'control.db'
        self.db.touch()
        self.calls=[]
        self.settings=dict(stage='test',enabled=True,read_sha256=self.sha(self.read),
            write_sha256=self.sha(self.write),actor_sha256=self.owner.decode(), verify_owner=self.verify_owner,
            prepare=self.prepare,change=self.change,port=8893)
        self.app=make_queue_pause_rest(**self.settings)

    def prepare(self):
        self.calls.append('read')
        with closing(sqlite3.connect(self.db)) as conn:
            paused,revision=read_control(conn)
        return dict(stage='test',paused=paused,revision=revision)

    def change(self, *, stage, paused, expected_revision):
        self.calls.append('write')
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            state,revision=set_control(conn,paused=paused,expected_revision=expected_revision)
        return dict(stage=stage,paused=state,revision=revision)

    async def verify_owner(self, scope, body):
        # Test-double of a separately authenticated service proof. Browser
        # actor hints are never used, and a forged header cannot authorize.
        proofs=[v for k,v in scope['headers'] if k==b'x-test-signed-owner']
        valid=hmac.digest(b'test-signer',body,'sha256')
        if len(proofs)==1 and hmac.compare_digest(proofs[0],valid):
            return self.owner.decode()
        return None

    async def request(self, method, key, *, who=None, peer='127.0.0.1',
                      path=PATH, data=b'', headers=()):
        base=[(b'host',b'127.0.0.1:8893'),(b'authorization',b'Bearer '+key)]
        if who == self.owner:
            base.append((b'x-test-signed-owner',hmac.digest(b'test-signer',data,'sha256')))
        if method=='PUT':
            base.append((b'content-type',b'application/json'))
        result=[]
        async def receive():
            return {'type':'http.request','body':data}
        async def send(event):
            result.append(event)
        await self.app(dict(type='http',path=path,raw_path=path.encode(),method=method,
                           headers=base+list(headers),client=(peer,1234)),receive,send)
        return result[0]['status'],json.loads(result[1]['body'])

    def payload(self, revision=INITIAL, paused=True):
        return json.dumps(dict(paused=paused,expected_revision=revision)).encode()

    async def test_real_sqlite_cas_without_new_state_on_read(self):
        code,state=await self.request('GET',self.read)
        self.assertEqual((code,state),(200,dict(stage='test',revision=INITIAL,paused=False)))
        code,state=await self.request('PUT',self.write,who=self.owner,data=self.payload())
        self.assertEqual(code,200)
        self.assertTrue(state['paused'])
        self.assertNotEqual(state['revision'],INITIAL)
        code,_=await self.request('PUT',self.write,who=self.owner,data=self.payload(paused=False))
        self.assertEqual(code,409)
        code,state=await self.request('PUT',self.write,who=self.owner,data=self.payload(state['revision'],False))
        self.assertEqual((code,state['paused']),(200,False))

    async def test_separate_authorities_and_network(self):
        for method,key,actor,peer in [
            ('PUT',self.read,self.owner,'127.0.0.1'),
            ('PUT',self.owner,self.owner,'127.0.0.1'),
            ('PUT',self.write,None,'127.0.0.1'),
            ('PUT',self.write,b'd'*64,'127.0.0.1'),
            ('PUT',self.write,self.owner,'192.0.2.2'),
            ('GET',self.write,None,'127.0.0.1'),
        ]:
            self.assertEqual((await self.request(method,key,who=actor,peer=peer,data=self.payload()))[0],403)
        self.assertEqual(self.calls,[])

    async def test_reject_duplicate_actual_owner_and_effect_relay(self):
        self.assertEqual((await self.request('PUT',self.write,who=self.owner,data=self.payload(),
            headers=[(b'x-test-signed-owner',b'forged')]))[0],403)
        self.assertEqual((await self.request('POST',self.write,path='/memory-mcp-test-effects'))[0],404)
        self.assertEqual((await self.request('PUT',self.write,who=self.owner,data=b'{"paused":true,"paused":false}'))[0],400)
        self.assertEqual((await self.request('PUT',self.write,data=self.payload(),
            headers=[(b'x-memory-verified-owner-sha256',self.owner)]))[0],403)
        self.assertEqual((await self.request('PUT',self.write,data=self.payload(),
            headers=[(b'x-test-signed-owner',hmac.digest(b'test-signer',self.payload(paused=False),'sha256'))]))[0],403)
        self.assertEqual(self.calls,[])

    async def test_prod_and_default_dark(self):
        self.assertIsNone(make_queue_pause_rest(**(self.settings|{'stage':'prod'})))
        self.assertIsNone(make_queue_pause_rest(**(self.settings|{'enabled':False})))
        with self.assertRaises(ValueError):
            make_queue_pause_rest(**(self.settings|{'write_sha256':self.sha(self.read)}))

        with self.assertRaises(ValueError):
            make_queue_pause_rest(**(self.settings|{'verify_owner':None}))
