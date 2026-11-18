"""Retaining delivery facts is independent of retaining excluded raw bodies."""
import unittest
from prototypes.assertion_memory.ingest_streams import register, record_delivery, report, transport_status
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.thought_purge import purge_batch
import prototypes.assertion_memory.ingest_streams as streams


class IngestReceiptRetirementTest(unittest.TestCase):
    def setUp(self):
        self.memory = MemoryStore(':memory:')
        self.addCleanup(self.memory.close)
        self.queue, self.thoughts = CaptureQueue(self.memory), ThoughtStore(self.memory)
        self.tool = self.queue.capture_event('s','tool','SECRET_EXEC_BODY',source='codex://session/s/tool/exec',
            actor='tool:exec_command',metadata={'role':'tool','message_id':'tool'})
        self.chat = self.queue.capture_event('s','user','Useful choice.',source='codex://session/s/user')
        with self.memory.transaction(write=True) as c:
            self.stream = register(c,'producer','s')
            self.tool_ack = record_delivery(c,{'stream_id':self.stream['stream_id'],'epoch':self.stream['epoch'],'seq':1},self.tool.id,'s')
            record_delivery(c,{'stream_id':self.stream['stream_id'],'epoch':self.stream['epoch'],'seq':2},self.chat.id,'s')
            report(c,self.stream['stream_id'],self.stream['epoch'],1,2,'current',0)

    def purge(self):
        result = purge_batch(self.thoughts,limit=10)
        while not result['complete']:
            result = purge_batch(self.thoughts,limit=10)
        self.assertEqual(result['blockers'],[])

    def proof(self):
        return {'status':'acknowledged','session_id':'s','message_id':'tool','delivery_seq':1,
            'receipt':{'event':{'id':self.tool.id,'session_id':'s','message_id':'tool','source':self.tool.source,
                'actor':self.tool.actor,'metadata':{'role':'tool','message_id':'tool'}},'delivery':self.tool_ack}}

    def test_purging_excluded_body_keeps_acknowledged_prefix_and_pending_count(self):
        with self.memory.transaction() as c:
            before = transport_status(c)
        self.purge()
        self.assertIsNone(self.queue.get_event(self.tool.id))
        with self.memory.transaction() as c:
            self.assertEqual(transport_status(c)['known_pending_count'],before['known_pending_count'])
            self.assertEqual(c.execute('SELECT acked_through FROM memory_ingest_streams').fetchone()[0],2)
            receipt = c.execute('SELECT * FROM memory_ingest_retired_receipts').fetchone()
            self.assertEqual(receipt['event_id'],self.tool.id)
            self.assertNotIn('SECRET_EXEC_BODY',receipt['proof'])
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_original_outbox_ack_repairs_lost_receipt_without_inventing_new_frontier(self):
        self.purge()
        with self.memory.transaction(write=True) as c:
            c.execute('DELETE FROM memory_ingest_retired_receipts')
            self.assertEqual(transport_status(c)['known_pending_count'],1)
            fixed = streams.restore_retired_deliveries(c,[self.proof()])
            self.assertEqual(fixed['restored'],1)
            self.assertEqual(fixed['rejected'],[])
            self.assertEqual(transport_status(c)['known_pending_count'],0)
            invalid = self.proof(); invalid['delivery_seq']=3
            rejected = streams.restore_retired_deliveries(c,[invalid])
            self.assertEqual(len(rejected['rejected']),1)
            self.assertEqual(c.execute('SELECT observed_seq,acked_through FROM memory_ingest_streams').fetchone()[:],(2,2))


if __name__ == '__main__':
    unittest.main()
