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
from prototypes.assertion_memory.processing_control import INITIAL, read_control
from prototypes.assertion_memory.queue_pause_proof import ENVELOPE, ProvenOwner, apply_once, canonical, make_owner_verifier


class TestREST(unittest.IsolatedAsyncioTestCase):
    read, write, owner = b'a'*64, b'b'*64, b'c'*64
    sha = staticmethod(lambda token:sha256(token).hexdigest())

    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db=Path(temp.name).resolve()/'control.db'
        self.db.touch()
        self.calls=[]
        self.clock=1795619000
        self.seq=0
        self.secret=bytes(range(32))
        self.settings=dict(stage='test',enabled=True,read_sha256=self.sha(self.read),
            write_sha256=self.sha(self.write),actor_sha256=self.owner.decode(), verify_owner=make_owner_verifier(self.secret,self.owner.decode(),clock=lambda:self.clock),
            prepare=self.prepare,change=self.change,port=8893)
        self.app=make_queue_pause_rest(**self.settings)

    def prepare(self):
        self.calls.append('read')
        with closing(sqlite3.connect(self.db)) as conn:
            paused,revision=read_control(conn)
        return dict(stage='test',paused=paused,revision=revision)

    def change(self, *, stage, paused, expected_revision, proof):
        self.calls.append('write')
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            return apply_once(conn,stage=stage,paused=paused,expected_revision=expected_revision,
                              proof=proof,clock=lambda:self.clock)

    def proof(self, body, *, nonce=None, issued=1795619000):
        if nonce is None:
            self.seq+=1
            nonce=f'{self.seq:032x}'
        revision=json.loads(body)['expected_revision']
        proof=ProvenOwner(self.owner.decode(),revision,issued,issued+15,nonce)
        mac=hmac.digest(self.secret,canonical(body,proof),'sha256').hex()
        return list(zip(ENVELOPE,[x.encode() for x in (proof.owner,str(issued),str(issued+15),nonce,mac)]))

    async def request(self, method, key, *, who=None, peer='127.0.0.1',
                      path=PATH, data=b'', headers=(), port=8893):
        base=[(b'host',f'127.0.0.1:{port}'.encode()),(b'authorization',b'Bearer '+key)]
        if who == self.owner:
            base.extend(self.proof(data))
        if method=='PUT':
            base.append((b'content-type',b'application/json'))
        result=[]
        async def receive():
            return {'type':'http.request','body':data}
        async def send(event):
            result.append(event)
        await self.app(dict(type='http',path=path,raw_path=path.encode(),method=method,
                           headers=base+list(headers),client=(peer,1234),server=('127.0.0.1',port)),receive,send)
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
            headers=[(ENVELOPE[-1],b'forged')]))[0],403)
        self.assertEqual((await self.request('POST',self.write,path='/memory-mcp-test-effects'))[0],404)
        self.assertEqual((await self.request('PUT',self.write,data=b'{"paused":true,"paused":false}'))[0],400)
        self.assertEqual((await self.request('PUT',self.write,data=self.payload(),
            headers=[(b'x-memory-verified-owner-sha256',self.owner)]))[0],403)
        self.assertEqual((await self.request('PUT',self.write,data=self.payload(),
            headers=self.proof(self.payload(paused=False))))[0],403)
        self.assertEqual(self.calls,[])

    async def test_prod_and_default_dark(self):
        self.assertIsNone(make_queue_pause_rest(**(self.settings|{'stage':'unknown'})))
        self.assertIsNone(make_queue_pause_rest(**(self.settings|{'enabled':False})))
        with self.assertRaises(ValueError):
            make_queue_pause_rest(**(self.settings|{'write_sha256':self.sha(self.read)}))

        with self.assertRaises(ValueError):
            make_queue_pause_rest(**(self.settings|{'verify_owner':None}))

    async def test_durable_replay_even_if_noop_and_expiration(self):
        body=self.payload(paused=False)
        proof=self.proof(body,nonce='d'*32)
        self.assertEqual((await self.request('PUT',self.write,data=body,headers=proof))[0],200)
        self.assertEqual((await self.request('PUT',self.write,data=body,headers=proof))[0],403)
        proof=self.proof(body,nonce='a'*32)
        self.clock+=15
        self.assertEqual((await self.request('PUT',self.write,data=body,headers=proof))[0],403)

    async def test_fixed_shared_vector(self):
        body=b'{"paused":true,"expected_revision":"00000000000000000000000000000000"}'
        self.assertEqual(self.proof(body,nonce='d'*32)[-1][1].decode(),
            'a83aa192bbe8f2a2b492e1a35ee0460bd7338b9bcee822fac59d4afa69c571d7')
        self.assertEqual((await self.request('PUT',self.write,data=body,headers=self.proof(body,nonce='d'*32)))[0],200)

    async def test_actual_release_lease_and_transaction_entrypoint(self):
        import fcntl
        import os
        from prototypes.assertion_memory.queue_pause_binding import Profile, callbacks
        root=self.db.parent
        lock=root/'deploy.lock'
        lock.touch(mode=0o600)
        profile=Profile('test',root,root,'fixture',8893,self.db)
        identity=('fixture','v1')
        read,write=callbacks(profile,uid=os.getuid(),identity=lambda:identity,
                             established=identity, clock=lambda:self.clock)
        self.app=make_queue_pause_rest(**(self.settings|{'prepare':read,'change':write}))
        body=self.payload(paused=False)
        proof=self.proof(body,nonce='d'*32)
        with lock.open('rb') as held:
            fcntl.flock(held,fcntl.LOCK_EX)
            self.assertEqual((await self.request('PUT',self.write,data=body,headers=proof))[0],503)
        self.assertEqual((await self.request('PUT',self.write,data=body,headers=proof))[0],200)
        self.assertEqual((await self.request('PUT',self.write,data=body,headers=proof))[0],403)

    async def test_wrong_lane_proof_is_rejected_even_with_valid_key(self):
        body=self.payload()
        proof=ProvenOwner(self.owner.decode(),INITIAL,self.clock,self.clock+15,'9'*32,'prod')
        envelope=self.proof(body,nonce=proof.nonce)
        envelope[-1]=(envelope[-1][0],hmac.digest(self.secret,canonical(body,proof),'sha256').hex().encode())
        self.assertEqual((await self.request('PUT',self.write,data=body,headers=envelope))[0],403)
        self.assertEqual(self.calls,[])


    async def test_prod_exact_lane_authority_and_shared_source_pause(self):
        import os
        from prototypes.assertion_memory.queue_pause_binding import Profile, callbacks
        from prototypes.assertion_memory.thought_processing_selection import processing_deadline
        root=self.db.parent
        (root/'deploy.lock').touch(mode=0o600)
        p=Profile('prod',root,root,'fixture',8903,self.db)
        identity=('prod','v1')
        read,write=callbacks(p,uid=os.getuid(),identity=lambda:identity,
                             established=identity,clock=lambda:self.clock)
        self.app=make_queue_pause_rest(**(self.settings|{'stage':'prod','port':8903,
            'prepare':read,'change':write,
            'verify_owner':make_owner_verifier(self.secret,self.owner.decode(),lane='prod',
                                              clock=lambda:self.clock)}))
        body=self.payload()
        envelope=self.proof(body,nonce='9'*32)
        self.assertEqual((await self.request('PUT',self.write,data=body,headers=envelope,port=8903))[0],403)
        proof=ProvenOwner(self.owner.decode(),INITIAL,self.clock,self.clock+15,'9'*32,'prod')
        envelope[-1]=(envelope[-1][0],hmac.digest(self.secret,canonical(body,proof),'sha256').hex().encode())
        status,state=await self.request('PUT',self.write,data=body,headers=envelope,port=8903)
        self.assertEqual((status,state['stage'],state['paused']),(200,'prod',True))
        self.assertEqual((await self.request('PUT',self.write,data=body,headers=envelope,port=8903))[0],403)
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(read_control(db),(True,state['revision']))
            self.assertIsNone(processing_deadline(db,None,now=0,lane='source'))
            self.assertIsNone(processing_deadline(db,None,now=0,lane='maintenance'))
