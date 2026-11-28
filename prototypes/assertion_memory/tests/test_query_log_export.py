from io import BytesIO
import json
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch

from prototypes.assertion_memory import query_log_export as out, query_timing as timing, genai_traces


class QueryLogExport(unittest.TestCase):
    def test_disabled_does_not_initialize_or_send_and_journal_unchanged(self):
        journal=[]
        with patch.object(out,'_exporter',None), patch.object(out,'_LogExporter') as make, \
             patch.object(timing,'_write_line',side_effect=journal.append):
            out.configure('test')
            with timing.bind(trace_id='b'*32):
                timing.emit('sufficiency','ok',12)
            make.assert_not_called()
            self.assertEqual(len(journal),1)

    def test_real_producer_routes_whitelisted_logs_both_lanes_without_trace_wire(self):
        for lane,port in [('test',14318),('prod',24318)]:
            journal=[]
            transport=MagicMock()
            with patch.object(out,'_exporter',None), patch.object(out,'HTTPConnection',return_value=transport) as conn, \
                 patch.object(timing,'_write_line',side_effect=journal.append):
                log = object.__new__(out._LogExporter)
                log.lane=lane
                with patch.object(out,'_LogExporter',return_value=log), patch.object(log,'put',side_effect=lambda v:log._send([v])):
                    out.configure(lane,enabled=True)
                    with timing.bind(trace_id='b'*32):
                        timing.emit('sufficiency','ok',12)
                        timing.emit('spoof_secret','ok',12)
                conn.assert_called_once_with('127.0.0.1',port,timeout=1)
                _,path,wire,_=transport.request.call_args.args
                self.assertEqual(path,'/v1/logs')
                result=json.loads(wire)['resourceLogs'][0]
                self.assertNotIn('spoof_secret',wire.decode())
                self.assertEqual(len(result['scopeLogs'][0]['logRecords']),1)
                self.assertEqual(result['resource']['attributes'][1]['value']['stringValue'],lane)
                self.assertEqual(len(journal),1)

    def test_mirror_failure_does_not_break_journal_or_independent_trace_queue(self):
        journal=[]
        trace=MagicMock()
        with patch.object(out,'_exporter',SimpleNamespace(put=MagicMock(side_effect=RuntimeError('synthetic')))), \
             patch.object(genai_traces,'_exporter',trace),patch.object(timing,'_write_line',side_effect=journal.append):
            with timing.bind(trace_id='b'*32), timing.span('sufficiency'):
                pass
            self.assertEqual(len(journal),1)
            trace.put.assert_called_once()

    def test_real_worker_saturated_and_failed_logs_do_not_block_trace(self):
        entered, unblock, recovered = Event(), Event(), Event()
        attempts=[]
        class BlockFirst(out._LogExporter):
            def _send(self, batch):
                attempts.extend(batch)
                if not entered.is_set():
                    entered.set()
                    unblock.wait(5)
                    raise OSError('synthetic unavailable sink')
                recovered.set()
        trace=MagicMock()
        journal=[]
        engine=BlockFirst('test')
        try:
            with patch.object(out,'_exporter',engine), patch.object(genai_traces,'_exporter',trace), \
                 patch.object(timing,'_write_line',side_effect=journal.append):
                with timing.bind(trace_id='b'*32), timing.span('sufficiency'):
                    timing.emit('sufficiency','ok')
                    self.assertTrue(entered.wait(3))
                    for _ in range(1000):
                        timing.emit('sufficiency','ok')
                self.assertEqual(engine._queue.qsize(),out._LogExporter.QUEUE_SIZE)
                trace.put.assert_called_once()
                self.assertEqual(len(journal),1002)
            unblock.set()
            self.assertTrue(recovered.wait(3))
            self.assertLessEqual(len(attempts), 1+out._LogExporter.QUEUE_SIZE)
        finally:
            unblock.set()
