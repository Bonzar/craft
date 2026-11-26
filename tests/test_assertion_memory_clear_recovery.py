"""Disposable native worker transcript + owner-clear transport contract."""
from datetime import datetime, timezone
import io,json
from types import SimpleNamespace
import unittest
from uuid import uuid4
from . import test_assertion_memory_capture as capture_fixture
from .test_assertion_memory_frontier import FakeRemote


class ResetRemote(FakeRemote):
    def __init__(self, client):
        super().__init__()
        self.current_reset=None
        self.marker_change={}
        self.ready=True
        self.events=[]
        self.client=client

    def call(self,name,args):
        if name=='memory_ingest_report' and 'reset_generation' in args:
            got=super().call(name,args)
            if self.ready:
                got['database_clear_ready_generation']=args['reset_generation']
            return got
        got=super().call(name,args)
        if name=='memory_capture_batch':
            self.events.extend(r['event']['message_id'] for r in got['receipts'])
        return got

    def clear(self,value):
        old=self.streams.pop(value['session_id'])
        self.receipts.clear()
        self.events.clear()
        new={**old,'stream_id':str(uuid4()),'epoch':str(uuid4()),'report_seq':0,'observed_seq':0,'acked_through':0}
        self.streams[value['session_id']]=new
        self.current_reset={'previous_stream_id':old['stream_id'],'previous_epoch':old['epoch'],
            'producer_id':value['producer_id'],'session_id':value['session_id'],
            'protocol':'owner-clear-v1','generation':'a'*32,'cleared_at':'2026-01-01T00:00:00.000000+00:00'}

    def register_stream(self,args,*,previous):
        current=self.call('memory_ingest_register',args) | args
        if (current['stream_id'],current['epoch'])!=previous:
            current['database_clear_reset']=self.current_reset | self.marker_change
        return current


class ResetTest(unittest.TestCase):
    turn=capture_fixture.CaptureTest.turn
    pair=capture_fixture.CaptureTest.pair
    assistant=capture_fixture.CaptureTest.assistant
    poll=capture_fixture.CaptureTest.poll
    def append(self,value):
        past=datetime.fromtimestamp(self.now,timezone.utc).isoformat()
        capture_fixture.CaptureTest.append(self,{'timestamp':past,**value})

    def setUp(self):
        self.now=1700000000
        capture_fixture.CaptureTest.setUp(self)
        self.config['capture_batch']=True
        from synthetic_memory_adapter.frontier import Coordinator
        self.capture.register(self.store,self.raw,fresh_since='2000-01-01T00:00:00Z')
        self.remote=ResetRemote(self.store)
        self.coord=Coordinator(self.store,self.remote,clock=lambda:self.now)

    def complete(self, turn):
        self.turn('fictional '+turn,turn,'u-'+turn)
        self.assistant('reply '+turn,'a-'+turn)

    def check_and_poll(self):
        self.coord.prepare_source_scan('physical')
        observation=self.poll()
        self.coord.observe('physical',observation)
        return self.coord.tick()

    def seed(self):
        self.complete('old')
        self.assertEqual(self.check_and_poll()['acknowledged'],2)
        value=self.coord.local('physical')
        self.remote.clear(value)
        self.now+=12
        return value

    def test_reset_proactive_rearm_and_first_new_complete_batch_once(self):
        old=self.seed()
        self.assertEqual(self.store.summary('physical')['queued_pending'],0)
        self.assertEqual(self.check_and_poll()['acknowledged'],0)
        new=self.coord.local('physical')
        self.assertEqual(new['database_clear_ready_generation'],'a'*32)
        self.assertNotEqual(new['stream_id'],old['stream_id'])
        self.now+=1
        self.complete('new')
        self.assertEqual(self.check_and_poll()['acknowledged'],2)
        self.assertEqual(self.remote.events,['codex:user:new:response-u-new','codex:assistant:new:a-new'])
        frames=[args for name,args in self.remote.calls if name=='memory_capture_batch']
        self.assertEqual([m['delivery']['seq'] for m in frames[-1]['messages']],[1,2])
        self.assertEqual(self.check_and_poll()['acknowledged'],0)
        self.assertEqual(len([n for n,_ in self.remote.calls if n=='memory_capture_batch']),2)

    def test_new_before_first_observation_is_explicitly_uncertain_and_never_uploaded(self):
        self.seed()
        self.complete('unarmed')
        self.assertEqual(self.check_and_poll()['acknowledged'],0)
        self.assertEqual(self.remote.events,[])
        self.assertTrue(self.store.session('physical')['pre_reset_turn_discarded'])
        self.now+=1
        self.complete('armed')
        self.assertEqual(self.check_and_poll()['acknowledged'],2)
        self.assertEqual(self.remote.events,['codex:user:armed:response-u-armed','codex:assistant:armed:a-armed'])

    def test_foreign_reset_and_unacknowledged_report_never_deliver(self):
        old=self.seed()
        self.remote.marker_change={'previous_epoch':'foreign-epoch'}
        self.assertEqual(self.check_and_poll()['acknowledged'],0)
        self.assertEqual(self.coord.local('physical')['stream_id'],old['stream_id'])
        self.remote.marker_change={}
        self.remote.ready=False
        self.now+=12
        self.assertEqual(self.check_and_poll()['acknowledged'],0)
        self.assertIsNone(self.coord.local('physical')['database_clear_ready_generation'])
        # A complete turn after the local fence waits until server confirms it.
        self.now+=1
        self.complete('awaiting')
        self.check_and_poll()
        self.assertEqual(self.remote.events,[])
        self.remote.ready=True
        self.now+=12
        self.assertEqual(self.check_and_poll()['acknowledged'],2)
        self.assertEqual(self.remote.events,['codex:user:awaiting:response-u-awaiting','codex:assistant:awaiting:a-awaiting'])


class MachineRegistrationTest(unittest.TestCase):
    def test_pair_only_on_trusted_advertised_catalog_and_in_same_release(self):
        capture_fixture.load_capture()
        from synthetic_memory_adapter.private_http import MachineClient
        from synthetic_memory_adapter.remote_client import TransportError
        observed={}
        def run(req,timeout):
            call=json.loads(req.data)
            info={'environment':'prod','request_id':call['request_id'],
                  'generation':'b'*64,'catalog_sha256':'c'*64,'release':'assertion-memory-v0.1.28'}
            if call['operation']=='catalog':
                info.update(capability='machine',tools=[{'name':'memory_ingest_register',
                    'inputSchema':{'properties':{k:{'type':'string'} for k in props}}}])
            else:
                self.assertEqual(call['expected'],{k:info[k] for k in call['expected']})
                observed.clear()
                observed.update(call['arguments'])
                info.update(status='ok',result={'accepted':True})
            return io.BytesIO(json.dumps(info).encode())
        client=MachineClient({'url':'https://example.test/memory-api-prod'},
            opener=SimpleNamespace(open=run),credential=lambda:'a'*64)
        args={'session_id':'fiction','producer_id':'fiction'}
        props=[]
        client.register_stream(args,previous=('old-stream','old-epoch'))
        self.assertEqual(observed,args)
        props=['previous_stream_id']
        with self.assertRaises(TransportError):
            client.register_stream(args,previous=('old-stream','old-epoch'))
        self.assertEqual(observed,args)
        props+=['previous_epoch']
        client.register_stream(args,previous=('old-stream','old-epoch'))
        self.assertEqual(observed,args|{'previous_stream_id':'old-stream','previous_epoch':'old-epoch'})
