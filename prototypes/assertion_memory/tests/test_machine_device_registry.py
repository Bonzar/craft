import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from concurrent.futures import ThreadPoolExecutor
from prototypes.assertion_memory.machine_device_registry import MachineDeviceRegistry as Registry, MachineDeviceError, TTL_MS

class Devices(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/'devices.sqlite3'
        self.fixed='a'*64; self.owner='b'*64; self.fingerprint='c'*64
        self.t=1_000_000
        self.reg=Registry.initialize(self.path,'test',(self.fixed,),clock=lambda:self.t)

    def body(self,action='enroll',fingerprint=None,**kw):
        data={'stage':'test','expected_revision':self.reg.status()['revision'],
              'action':action,'fingerprint':fingerprint or self.fingerprint}
        if action=='enroll':data['label']='Test laptop'
        return data|kw

    def test_single_use_owner_device_and_revocation(self):
        intent=self.body();proof=self.reg.prepare(self.owner,intent)['operation_proof']
        with sqlite3.connect(self.path) as db:
            self.assertNotIn(proof,str(db.execute('SELECT * FROM proofs').fetchall()))
        self.assertEqual(self.reg.machine_digests(),(self.fixed,))
        for other,changed in [('d'*64,intent),(self.owner,intent|{'label':'other'}),
                              (self.owner,intent|{'fingerprint':'e'*64})]:
            with self.assertRaises(MachineDeviceError):self.reg.commit(other,changed,proof)
        result=self.reg.commit(self.owner,intent,proof)
        self.assertIs(result['committed'],True)
        self.assertEqual(self.reg.machine_digests(),(self.fixed,self.fingerprint))
        with self.assertRaises(MachineDeviceError):self.reg.commit(self.owner,intent,proof)
        revoke=self.body('revoke');proof=self.reg.prepare(self.owner,revoke)['operation_proof']
        self.reg.commit(self.owner,revoke,proof)
        self.assertEqual(self.reg.machine_digests(),(self.fixed,))

    def test_foreign_stage_expiry_and_fixed_key(self):
        for body in (self.body(stage='prod'),self.body('revoke',self.fixed),
                     self.body('enroll',self.fixed),self.body(key='must-not-be-carried')):
            with self.assertRaises(MachineDeviceError):self.reg.prepare(self.owner,body)
        intent=self.body();proof=self.reg.prepare(self.owner,intent)['operation_proof']
        self.t+=TTL_MS
        with self.assertRaises(MachineDeviceError):self.reg.commit(self.owner,intent,proof)
        with self.assertRaises(MachineDeviceError):Registry(self.path,'prod',(self.fixed,))

    def test_one_commit_under_concurrency_and_leave_original(self):
        intent=self.body(); proof=self.reg.prepare(self.owner,intent)['operation_proof']
        def attempt():
            try:return self.reg.commit(self.owner,intent,proof)['committed']
            except MachineDeviceError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(lambda n:attempt(),range(2))),[False,True])
        self.assertFalse(self.reg.status()['items'][0]['revocable'])
        with self.assertRaises(FileExistsError):Registry.initialize(self.path,'test',(self.fixed,))
        self.assertEqual(self.reg.machine_digests(),(self.fixed,self.fingerprint))

    def test_registry_path_replacement_and_mode_deny(self):
        os.chmod(self.path,0o644)
        with self.assertRaises(MachineDeviceError):self.reg.machine_digests()
        os.chmod(self.path,0o600)
        new=self.path.with_name('second')
        Registry.initialize(new,'test',(self.fixed,))
        os.replace(new,self.path)
        with self.assertRaises(MachineDeviceError):self.reg.machine_digests()

class DynamicOwnerBoundary(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import sys
        root=Path(__file__).resolve().parents[3]
        path=str(root/'tools'/'assertion-memory-hosted-mcp')
        sys.path.insert(0,path)
        self.addCleanup(sys.path.remove,path)
        from vm_relay import authorized_machine_request
        from private_host import effect_lock
        self.resolver_test=authorized_machine_request
        self.effect_lock=effect_lock

    async def test_revocation_seen_next_request_and_no_late_unlocked_admission(self):
        from hashlib import sha256
        token='a'*64
        header=('Bearer '+token).encode()
        managed=sha256(token.encode()).hexdigest()
        values=[(managed,),()]
        self.assertTrue(self.resolver_test(header,(),lambda:values.pop(0)))
        self.assertFalse(self.resolver_test(header,(),lambda:values.pop(0)))
        self.assertFalse(self.resolver_test(header,('b'*64,),lambda:(managed,)))
        calls=[]
        async def application(scope,receive,send):
            calls.append('downstream')
        async def send(event):
            calls.append(event['status'] if event['type']=='http.response.start' else 'body')
        async def receive():
            raise AssertionError('denial may not read/application-dispatch')
        class Race:
            n=0
            def read(self):
                self.n+=1
                return () if self.n==1 else (managed,)
        race=Race()
        locked=self.effect_lock(application,Path('/nonexistent'),'test',None,(),machine_resolver=race.read)
        await locked({'type':'http','path':'/memory-api-test','headers':[(b'authorization',header)]},receive,send)
        self.assertEqual(calls,[401,'body'])
        self.assertEqual(race.n,1)
