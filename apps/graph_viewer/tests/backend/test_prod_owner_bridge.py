import asyncio
from contextlib import contextmanager, nullcontext
from email.message import Message
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase

from prototypes.assertion_memory import processing_control
from prototypes.assertion_memory.queue_pause_proof import make_owner_verifier
from apps.graph_viewer.server.browser_owner_api import configured_browser_api
from apps.graph_viewer.server.common import APIError
from apps.graph_viewer.server.prod_owner_bridge import ProdOwnerBridge, configured_prod_owner_bridge, _CREDENTIALS


class ProdOwnerBridgeTests(TestCase):
    def setUp(self):
        self.keys=(('1'*64),('2'*64),('3'*64),('4'*64))
        self.proof=b'Z'*32
        self.calls=[]
        self.now=1795700000
        self.revision='a'*32

    def bridge(self, *, status=200, payload=None):
        owner=self
        def connect(host,port,timeout):
            self.assertEqual((host,port),('127.0.0.1',8903))
            class Connection:
                def request(self,method,path,*,body,headers):
                    owner.calls.append((method,path,body,headers))
                def getresponse(self):
                    body=json.dumps(payload if payload is not None else dict(stage='prod',revision=owner.revision,
                                      paused=True)).encode()
                    headers=Message(); headers['Content-Type']='application/json'
                    return SimpleNamespace(status=status,headers=headers,read=io.BytesIO(body).read)
                def close(self):
                    pass
            return Connection()
        return ProdOwnerBridge(*self.keys,self.proof,http_connection=connect,clock=lambda:self.now)

    def test_prod_uses_existing_authority_and_does_not_touch_graph_sqlite(self):
        class NoGraphDatabase:
            stage='prod'
            read_fence=staticmethod(nullcontext)
            activation=staticmethod(lambda:('a'*40,'b'*64))
            @property
            def db(self):
                raise AssertionError('Graph must not access the writer DB directly')
        bridge=self.bridge()
        api=configured_browser_api(NoGraphDatabase(),prod_owner_bridge=bridge)
        with self.assertRaises(ValueError):
            configured_browser_api(NoGraphDatabase())
        with self.assertRaises(ValueError):
            configured_browser_api(NoGraphDatabase(),prod_owner_bridge=bridge,clear_enabled=True)
        self.assertEqual([route.methods for route in api.routes() if 'clear' in route.path],[{'POST'}])
        self.assertEqual(self.calls,[])
        self.assertTrue(api.perform('pause_prepare',{})['paused'])
        self.assertTrue(api.perform('pause_set',{'paused':True,'expected_revision':self.revision})['paused'])
        prepared,written=self.calls
        self.assertEqual(prepared[3]['Authorization'],'Bearer '+self.keys[1])
        self.assertEqual(written[3]['Authorization'],'Bearer '+self.keys[2])
        method,path,body,headers=written
        scope={'method':method,'path':path,'headers': [(k.lower().encode(),v.encode()) for k,v in headers.items()]}
        owner=make_owner_verifier(self.proof,self.keys[3],lane='prod',path=path,clock=lambda:self.now)
        self.assertEqual(asyncio.run(owner(scope,body,self.revision)).lane,'prod')
        self.assertEqual(json.loads(body),{'paused':True,'expected_revision':self.revision})
        with self.assertRaises(ValueError):
            api.perform('pause_set',{'paused':True,'expected_revision':self.revision,'origin':'http://localhost'})
        self.assertEqual(len(self.calls),2)

    def test_private_clear_uses_existing_reset_header_and_owner_proof(self):
        from prototypes.assertion_memory.database_clear import PATH, RESET_HEADER, RESET_FORMAT
        payload=dict(stage='prod',revision=self.revision,scope='a'*64,can_clear=True,capture_rearm='not_cleared')
        self.assertTrue(self.bridge(payload=payload).perform('clear_prepare',{})['can_clear'])
        result=dict(stage='prod',revision=self.revision,cleared=True,capture_rearm='not_required')
        self.assertTrue(self.bridge(payload=result).perform('clear_commit',{'expected_revision':self.revision})['cleared'])
        read,write=self.calls
        self.assertEqual((read[0],read[1]),('GET',PATH))
        method,path,body,headers=write
        self.assertEqual((method,path),('PUT',PATH))
        self.assertEqual(headers[RESET_HEADER.decode()],RESET_FORMAT.decode())
        self.assertEqual(read[3][RESET_HEADER.decode()],RESET_FORMAT.decode())
        verifier=make_owner_verifier(self.proof,self.keys[3],lane='prod',path=PATH,clock=lambda:self.now)
        scope={'method':method,'path':path,'headers':[(k.lower().encode(),v.encode()) for k,v in headers.items()]}
        self.assertEqual(asyncio.run(verifier(scope,body,self.revision)).lane,'prod')

    def test_forwarding_holds_fence_and_rejects_revoked_reader_or_transition(self):
        held=False
        condition=None
        @contextmanager
        def fence():
            nonlocal held
            if condition=='busy':
                raise BlockingIOError('transition in progress')
            held=True
            try:
                yield
            finally:
                held=False
        def activation():
            self.assertTrue(held)
            if condition=='revoked':
                raise ValueError('reader revoked or old release')
            return 'a'*40,'b'*64
        from prototypes.assertion_memory.dashboard_projection import release_generation
        release='assertion-memory-v0.1.33'
        value=dict(stage='prod',release_version=release,
                   release_generation=release_generation('a'*40,'b'*64,release,'prod'),
                   global_order=False,snapshot_at='',queues=[],
                   processing_control=dict(paused=False,revision=self.revision,can_change=True))
        dashboard=SimpleNamespace(stage='prod',read_fence=fence,activation=activation)
        def guarded_bridge(payload):
            original=self.bridge(payload=payload)
            def connect(*args, **kwargs):
                self.assertTrue(held)
                return original._connection(*args, **kwargs)
            return ProdOwnerBridge(*self.keys,self.proof,http_connection=connect,clock=lambda:self.now)
        bridge=guarded_bridge(value)
        api=configured_browser_api(dashboard,prod_owner_bridge=bridge)
        self.assertEqual(api.queue_snapshot(limit=2,offset=0),value)
        condition='revoked'
        with self.assertRaises(APIError):
            api.perform('pause_set',{'paused':True,'expected_revision':self.revision})
        condition='busy'
        with self.assertRaises(APIError):
            api.queue_snapshot(limit=2,offset=0)
        self.assertEqual(len(self.calls),1)
        condition=None
        value['release_generation']='d'*64
        with self.assertRaises(APIError):
            api.queue_snapshot(limit=2,offset=0)
        self.assertEqual(len(self.calls),2)
        self.assertFalse(held)

    def test_unavailable_and_wrong_lane_do_not_fallback(self):
        with self.assertRaises(APIError):
            self.bridge(payload={'stage':'test'}).perform('pause_prepare',{})
        with self.assertRaises(APIError):
            self.bridge(payload={'stage':'prod','paused':True}).perform('pause_prepare',{})
        with self.assertRaises(processing_control.Conflict):
            self.bridge(status=409).perform('pause_set',{'paused':True,'expected_revision':self.revision})

    def test_load_only_existing_exact_secure_credential_files(self):
        with TemporaryDirectory() as root:
            folder=Path(root)
            with self.assertRaises(FileNotFoundError):
                configured_prod_owner_bridge(folder)
            for name,val in zip(_CREDENTIALS,(*self.keys,self.proof.hex())):
                file=folder/name; file.write_text(val+'\n'); file.chmod(0o400)
            self.assertIs(type(configured_prod_owner_bridge(folder)),ProdOwnerBridge)
            link=folder/'alias'
            link.symlink_to(folder,target_is_directory=True)
            with self.assertRaises(ValueError):
                configured_prod_owner_bridge(link)
            (folder/_CREDENTIALS[0]).chmod(0o444)
            with self.assertRaises(ValueError):
                configured_prod_owner_bridge(folder)

    def test_real_base_queue_snapshot_wire_uses_only_read_authority(self):
        from prototypes.assertion_memory.queue_rest import snapshot
        from prototypes.assertion_memory.storage import MemoryStore
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'synthetic.sqlite3'
            with MemoryStore(str(path)):
                value=snapshot(SimpleNamespace(provider='none', processing_session_ids=(),auto_process_fresh_mac=False),
                               identity=lambda:('assertion-memory-v0.1.33','d'*64),database=path,
                               interactive_enabled=False,limit=50,offset=0,lane='prod',
                               can_change=True,session_control=True,batch_cards=True,clear_ready=False)
            bridge=self.bridge(payload=value)
            self.assertEqual(bridge.queue_snapshot(limit=50,offset=0),value)
            method,route,body,headers=self.calls[-1]
            self.assertEqual((method,route,body),('GET','/api/v1/queue-status?limit=50&offset=0',None))
            self.assertEqual(headers['Authorization'],'Bearer '+self.keys[0])
            self.assertEqual(headers['X-Memory-Queue-Format'],'batch-cards-v1')
            self.assertFalse(any(key.startswith('x-memory-pause') for key in headers))
            self.assertNotIn('authorization', {key.lower() for key in headers if key!='Authorization'})
