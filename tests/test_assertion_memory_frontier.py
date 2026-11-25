"""Operational frontier uses synthetic remote receipts; no model or network."""
import json
from pathlib import Path
import tempfile
import unittest
from .test_assertion_memory_capture import load_capture


class FakeRemote:
    def __init__(self):
        self.calls=[];self.streams={};self.receipts={};self.fail_report=False;self.fail_capture=False;self.missing_delivery=False;self.fail_batch=False
    def call(self,name,args):
        self.calls.append((name,json.loads(json.dumps(args))))
        if name=='memory_ingest_register':
            key=args['session_id']
            if key not in self.streams:
                self.streams[key]={'stream_id':'stream-'+key,'epoch':'epoch-'+key,'report_seq':0,
                    'observed_seq':0,'acked_through':0,'scan_state':'unknown','gap_count':0,'lifecycle':'active'}
            return dict(self.streams[key])
        if name=='memory_capture_batch':
            boundary=len(self.calls)
            receipts=[self.call('memory_capture_message',row) for row in args['messages']]
            # Simulated service commits raw rows inside this single transport operation.
            del self.calls[boundary:]
            if self.fail_batch:self.fail_batch=False;raise OSError('lost atomic ACK')
            return {'batch_id':args['batch_id'],'receipts':receipts}
        stream=next(s for s in self.streams.values() if s['stream_id']==args.get('stream_id',args.get('delivery',{}).get('stream_id')))
        if name=='memory_ingest_report':
            if args['report_seq'] < stream['report_seq']: raise ValueError('old report')
            stream.update({k:args[k] for k in ('report_seq','observed_seq','scan_state','gap_count','lifecycle')})
            if self.fail_report: self.fail_report=False;raise OSError('lost report ACK')
            return dict(stream)
        if name=='memory_capture_message':
            delivery=args['delivery'];self.receipts[(stream['stream_id'],delivery['seq'])]=args['message_id']
            while (stream['stream_id'],stream['acked_through']+1) in self.receipts: stream['acked_through']+=1
            if self.fail_capture:self.fail_capture=False;raise OSError('lost capture ACK')
            result={'event':{'id':'event-'+args['message_id'],'session_id':args['session_id'],
                'message_id':args['message_id'],'source':args['source']}}
            if not self.missing_delivery:result['delivery']=dict(stream)
            return result
        raise AssertionError(name)


class FrontierTest(unittest.TestCase):
    def setUp(self):
        load_capture()
        from synthetic_memory_adapter.outbox import Outbox
        from synthetic_memory_adapter.frontier import Coordinator
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=Outbox(self.root/'outbox.sqlite3');self.addCleanup(self.store.close)
        self.remote=FakeRemote();self.now=1000.0
        self.coordinator=Coordinator(self.store,self.remote,clock=lambda:self.now)
        self.store.register_session({'session_id':'physical','path':'/fiction','cwd':'/fiction','gaps':[],
            'turn':None,'cursor':0,'skip_partial':False})
        self.observe()
    def observe(self,**extra):
        self.coordinator.observe('physical',{'status':'registered','backlog':False,**extra})
    def enqueue(self,mid='one'):
        with self.store.db:self.store.enqueue({'session_id':'physical','message_id':mid,'actor_id':'user','role':'user',
            'text':'fiction '+mid,'source':'fiction://'+mid,'scope':'fiction'},local_position=len(self.store.messages('physical')))
    def tick(self):return self.coordinator.tick()

    def test_register_and_report_observed_before_capture_then_server_ack_frontier(self):
        self.enqueue();self.tick()
        names=[name for name,_ in self.remote.calls]
        self.assertEqual(names[:3],['memory_ingest_register','memory_ingest_report','memory_capture_message'])
        report=self.remote.calls[1][1];self.assertEqual(report['observed_seq'],1)
        capture=self.remote.calls[2][1];self.assertEqual(capture['delivery']['seq'],1)
        self.assertNotIn('position',capture)
        self.assertEqual(self.store.messages('physical')[0]['status'],'acknowledged')
        self.assertEqual(self.coordinator.local('physical')['server_acked_through'],1)

    def test_lost_report_ack_replays_exact_durable_report_before_any_capture(self):
        self.enqueue();self.remote.fail_report=True;self.tick()
        self.assertNotIn('memory_capture_message',[name for name,_ in self.remote.calls])
        first=next(args for name,args in self.remote.calls if name=='memory_ingest_report')
        self.now+=65;self.tick()
        reports=[args for name,args in self.remote.calls if name=='memory_ingest_report']
        self.assertEqual(first,reports[1])
        self.assertEqual(self.store.messages('physical')[0]['status'],'acknowledged')

    def test_lost_capture_ack_reuses_same_stream_epoch_and_sequence(self):
        self.enqueue();self.remote.fail_capture=True;self.tick();self.now+=65;self.tick()
        calls=[args for name,args in self.remote.calls if name=='memory_capture_message']
        self.assertEqual(calls[0],calls[1])
        self.assertEqual(len(self.remote.receipts),1)
        self.assertEqual(self.store.messages('physical')[0]['status'],'acknowledged')

    def test_capture_batch_is_one_call_and_lost_ack_replays_exact_members(self):
        ids=['user-fixture','assistant-fixture']
        marker={'id':'b'*64,'message_ids':ids}
        with self.store.db:
            for ordinal,mid in enumerate(ids):
                role='user' if ordinal==0 else 'assistant'
                self.store.enqueue({'session_id':'physical','message_id':mid,'actor_id':role,
                    'role':role,'text':'fixture '+mid,'source':'fiction://'+mid,
                    '_capture_batch':marker},local_position=ordinal)
        # Even a scan budget of one must not split the atomically staged group.
        self.remote.fail_batch=True
        self.coordinator.tick(limit=1)
        self.assertTrue(all(m['status']=='pending' for m in self.store.messages('physical')))
        self.now+=65
        self.coordinator.tick(limit=1)
        calls=[args for name,args in self.remote.calls if name=='memory_capture_batch']
        self.assertEqual(calls[0],calls[1])
        self.assertEqual([row['message_id'] for row in calls[0]['messages']],ids)
        self.assertTrue(all('_capture_batch' not in row for row in calls[0]['messages']))
        self.assertFalse(any(name=='memory_capture_message' for name,_ in self.remote.calls))
        self.assertEqual(len(self.remote.receipts),2)
        self.assertTrue(all(m['status']=='acknowledged' for m in self.store.messages('physical')))

    def test_missing_delivery_receipt_never_acknowledges_local_raw_message(self):
        self.enqueue();self.remote.missing_delivery=True;self.tick()
        self.assertEqual(self.store.messages('physical')[0]['status'],'pending')
        self.assertEqual(self.coordinator.local('physical')['server_acked_through'],0)
        self.remote.missing_delivery=False;self.now+=65;self.tick()
        self.assertEqual(self.store.messages('physical')[0]['status'],'acknowledged')

    def test_local_gaps_and_pending_proof_are_reported_and_never_clean_closed(self):
        state=self.store.session('physical');state['gaps']=['unsupported_user_schema']
        with self.store.db:self.store.save_session(state)
        self.observe();self.tick()
        stream=self.remote.streams['physical'];self.assertEqual((stream['scan_state'],stream['gap_count']),('gapped',1))
        with self.assertRaises(ValueError):self.coordinator.close('physical')
        state['gaps']=[];state['turn']={'pending':{'text':'unverified','emitted':False}}
        with self.store.db:self.store.save_session(state)
        self.observe();self.now+=21;self.tick()
        self.assertEqual(self.remote.streams['physical']['scan_state'],'catching_up')

    def test_heartbeat_renews_active_current_without_auto_close_and_no_per_second_reports(self):
        self.tick();before=len(self.remote.calls);self.now+=1;self.tick();self.assertEqual(len(self.remote.calls),before)
        self.now+=20;self.tick()
        self.assertEqual(self.remote.streams['physical']['lifecycle'],'active')
        self.assertGreater(self.remote.streams['physical']['report_seq'],1)
        self.coordinator.close('physical')
        self.assertEqual(self.remote.streams['physical']['lifecycle'],'closed')

    def test_delivery_sequence_rolls_back_with_message_and_survives_restart(self):
        self.enqueue()
        try:
            with self.store.db:
                self.store.enqueue({'session_id':'physical','message_id':'rolled-back','actor_id':'user','role':'user',
                    'text':'fiction','source':'fiction://rollback','scope':'fiction'})
                raise RuntimeError('synthetic crash')
        except RuntimeError:pass
        self.enqueue('two');self.tick()
        seqs=[args['delivery']['seq'] for name,args in self.remote.calls if name=='memory_capture_message']
        self.assertEqual(seqs,[1,2])
        old=self.coordinator.local('physical');self.coordinator=type(self.coordinator)(self.store,self.remote,clock=lambda:self.now)
        self.now+=21;self.tick();new=self.coordinator.local('physical')
        self.assertEqual((old['stream_id'],old['epoch']),(new['stream_id'],new['epoch']))
        self.assertGreater(new['report_seq'],old['report_seq'])

    def test_local_receipt_commit_failure_rolls_back_ack_then_replays_remote_commit(self):
        self.enqueue()
        self.store.db.execute("CREATE TRIGGER reject_ack BEFORE UPDATE OF status ON messages WHEN NEW.status='acknowledged' BEGIN SELECT RAISE(ABORT,'synthetic receipt failure'); END")
        self.tick()
        self.assertEqual(self.store.messages('physical')[0]['status'],'pending')
        self.assertIsNone(self.store.db.execute('SELECT receipt FROM delivery_sequences').fetchone()[0])
        self.assertEqual(self.coordinator.local('physical')['server_acked_through'],0)
        self.store.db.execute('DROP TRIGGER reject_ack');self.now+=65;self.tick()
        self.assertEqual(self.store.messages('physical')[0]['status'],'acknowledged')
        self.assertEqual(len(self.remote.receipts),1)

    def test_regressed_remote_prefix_does_not_erase_saved_receipts_or_allow_close(self):
        self.enqueue();self.tick()
        before=self.store.messages('physical')[0]['receipt']
        self.remote.streams['physical']['acked_through']=0
        self.coordinator.reconciled.clear();self.now+=21;self.tick()
        self.assertEqual(self.coordinator.local('physical')['server_acked_through'],1)
        self.assertEqual(before,self.store.messages('physical')[0]['receipt'])
        with self.assertRaisesRegex(ValueError,'regressed'):self.coordinator.close('physical')

    def test_matching_out_of_order_receipt_is_accepted_without_inventing_ack_prefix(self):
        for mid in ('one','two','three'):self.enqueue(mid)
        value=self.coordinator.local('physical');self.coordinator._register(value);self.coordinator._report(value)
        rows=self.store.db.execute('SELECT m.*,d.seq FROM messages m JOIN delivery_sequences d USING(session_id,message_id) ORDER BY d.seq').fetchall()
        self.coordinator._capture(value,rows[0]);self.coordinator._capture(value,rows[2])
        self.assertEqual(self.coordinator.local('physical')['server_acked_through'],1)
        self.assertEqual(self.store.messages('physical')[2]['status'],'acknowledged')
        with self.assertRaises(ValueError):self.coordinator.close('physical')

    def test_foreign_delivery_owner_is_not_a_durable_receipt(self):
        self.enqueue();original=self.remote.call
        def changed(name,args):
            result=original(name,args)
            if name=='memory_capture_message':result['delivery']['epoch']='other-epoch'
            return result
        self.remote.call=changed;self.tick()
        self.assertEqual(self.store.messages('physical')[0]['status'],'pending')
        self.assertIsNone(self.store.db.execute('SELECT receipt FROM delivery_sequences').fetchone()[0])
