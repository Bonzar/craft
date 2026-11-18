"""Synthetic service/MCP integration for global operational delivery limits."""
import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from prototypes.assertion_memory.service import MemoryService, _fits
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory import ingest_streams
from prototypes.assertion_memory.tests import test_answer_contract_v21 as fixture


class IngestCaptureServiceTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.service = MemoryService(self.store)
        self.args = dict(session_id='session', message_id='message', actor_id='user', role='user',
                         text='Synthetic attributed message.', position=None, source='fixture://message', scope='scope-a')

    def stream(self, device='laptop-a'):
        return self.service.ingest_register(device, 'session')

    def test_two_device_envelopes_do_not_change_canonical_message(self):
        a, b = self.stream(), self.stream('laptop-b')
        first = self.service.capture_message(**self.args, delivery={'stream_id':a['stream_id'],'epoch':a['epoch'],'seq':1})
        before = self.service.source(first['event']['id'])
        second = self.service.capture_message(**self.args, delivery={'stream_id':b['stream_id'],'epoch':b['epoch'],'seq':1})
        self.assertEqual(first['event'], second['event'])
        self.assertEqual(self.service.source(first['event']['id']), before)
        self.assertEqual(second['delivery']['acked_through'], 1)
        self.assertNotEqual(first['delivery']['stream_id'], second['delivery']['stream_id'])
        self.assertEqual(self.store._connection.execute('SELECT count(*) FROM memory_raw_events').fetchone()[0], 1)

    def test_invalid_delivery_rolls_back_raw_job_and_canonical_receipt(self):
        stream = self.stream()
        for delivery in ({}, {'stream_id':stream['stream_id'],'epoch':'stale','seq':1},
                         {'stream_id':stream['stream_id'],'epoch':stream['epoch'],'seq':True}):
            with self.assertRaises(ValueError): self.service.capture_message(**self.args, delivery=delivery)
            for table in ('memory_raw_events','memory_message_receipts','memory_capture_jobs','memory_ingest_receipts'):
                self.assertEqual(self.store._connection.execute('SELECT count(*) FROM '+table).fetchone()[0], 0, table)

    def test_global_status_is_read_only_and_never_invokes_model(self):
        self.stream()
        before = self.store._connection.total_changes
        with patch.object(self.service.engine,'model',side_effect=AssertionError('no model')):
            result = self.service.status(section='transport')
            overview = self.service.status(scope='unrelated')
        self.assertTrue(result['transport_status']['transport_limit'])
        self.assertTrue(overview['transport_status']['transport_limit'])
        self.assertEqual(before, self.store._connection.total_changes)
        with self.assertRaises(ValueError): self.service.status(section='transport',scope='unrelated')

    def test_token_meter_section_contract_is_unchanged(self):
        expected = {'usage': {'input_tokens': 17, 'output_tokens': 3}}
        with patch('prototypes.assertion_memory.model_usage.usage_status',return_value=expected) as usage:
            self.assertEqual(self.service.status(section='model_usage'),expected)
        usage.assert_called_once_with(self.store.path)

    def test_closed_or_clean_expired_stream_is_not_an_eternal_blocker(self):
        stream = self.stream()
        self.service.ingest_report(stream['stream_id'],stream['epoch'],1,0,'current',0)
        future = datetime.now(timezone.utc) + timedelta(days=1)
        with patch.object(ingest_streams,'_time',return_value=future):
            self.assertFalse(self.service.status(section='transport')['transport_status']['transport_limit'])
        self.service.ingest_report(stream['stream_id'],stream['epoch'],2,0,'current',0,lifecycle='closed')
        with patch.object(ingest_streams,'_time',return_value=future):
            status = self.service.status(section='transport')['transport_status']
            self.assertFalse(status['transport_limit'])
            self.assertEqual(status['disconnected_stream_count'],0)


class IngestQueryHealthTest(unittest.TestCase):
    setUp = fixture.AnswerContractTest.setUp
    provider = fixture.AnswerContractTest.provider
    build = fixture.AnswerContractTest.build
    result = fixture.AnswerContractTest.result

    def pending(self):
        stream = self.service.ingest_register('other-laptop','other-session')
        self.service.ingest_report(stream['stream_id'],stream['epoch'],1,2,'current',0)

    def test_known_incoming_globally_limits_even_previously_accepted_scoped_answer(self):
        view,check = self.build()
        prior = self.result(view,check)
        self.assertTrue(prior['answer_accepted'])
        self.assertFalse(prior['refresh_required'])
        state = self.service.status(scope='s')
        self.pending()
        with patch.object(self.engine,'model',side_effect=AssertionError('health cannot call provider')):
            result = self.service._query_result(view,{},state,check,'s',None)
        self.assertTrue(result['answer_accepted'])
        self.assertTrue(result['refresh_required'])
        self.assertTrue(result['transport_status']['transport_limit'])
        self.assertEqual(result['transport_status']['known_pending_count'],2)
        self.assertEqual(result['completeness'],'incomplete')
        self.assertFalse(check['freshness']['refresh_required'], 'immutable source check must not be rewritten')

    def test_current_transport_limit_survives_historical_answer_and_pagination(self):
        view,check = self.build()
        self.pending()
        state = self.service.status(scope='s')
        state['pending_jobs'] = [{'oversized':'x'*50000}]
        result = self.service._query_result(view,{},state,check,'s','2026-01-01')
        self.assertTrue(result['refresh_required'])
        self.assertTrue(result['transport_status']['transport_limit'])
        self.assertTrue(_fits(result))
        self.assertFalse(result['response_complete'])
        self.assertIn('answer_continuations',result)

    def test_history_pages_retain_exact_immutable_content_with_separate_current_health(self):
        view,check = self.build()
        before = deepcopy(self.engine.get_version(view.id).__dict__)
        first = self.service.history(view.view_id,version_id=view.id,section='metadata',limit=160)
        self.pending()
        contents = []
        page = self.service.history(view.view_id,version_id=view.id,section='metadata',limit=160,
            presentation_cutoff=first['presentation_cutoff'],name_watermark=first['name_watermark'])
        while True:
            self.assertTrue(page['current_transport_status']['transport_limit'])
            self.assertTrue(_fits(page))
            contents.append(page['content'])
            if page['continuation'] is None:break
            page = self.service.history(**page['continuation']['arguments'])
        metadata = json.loads(''.join(contents))
        self.assertNotIn('transport_status',metadata)
        self.assertNotIn('current_transport_status',metadata)
        self.assertEqual(self.engine.get_version(view.id).__dict__,before)
        summary = self.service.history(view.view_id,version_id=view.id,section='summary')
        self.assertEqual(summary['content'],view.summary)
        self.assertTrue(summary['current_transport_status']['transport_limit'])


class IngestMCPToolsTest(unittest.IsolatedAsyncioTestCase):
    async def test_public_tools_keep_exact_names_and_receipt_shape_without_network(self):
        from prototypes.assertion_memory.mcp_server import ServiceConfig, create_server
        with tempfile.TemporaryDirectory() as temp:
            store = MemoryStore(Path(temp)/'memory.sqlite3')
            try:
                server = create_server(ServiceConfig(str(Path(temp)/'memory.sqlite3'),'none'),background=False,store=store)
                names = {tool.name for tool in await server.list_tools()}
                self.assertTrue({'memory_ingest_register','memory_ingest_report','memory_capture_message','memory_status'} <= names)
                async def call(name,args):
                    result = await server.call_tool(name,args)
                    blocks = result[0] if isinstance(result,tuple) else result
                    return json.loads(next(block.text for block in blocks if block.type=='text'))
                stream = await call('memory_ingest_register',{'producer_id':'laptop','session_id':'s'})
                await call('memory_ingest_report',{'stream_id':stream['stream_id'],'epoch':stream['epoch'],
                    'report_seq':1,'observed_seq':1,'scan_state':'current','gap_count':0})
                receipt = await call('memory_capture_message',{'session_id':'s','message_id':'m','actor_id':'user',
                    'role':'user','text':'Synthetic source.','source':'fixture://s/m',
                    'delivery':{'stream_id':stream['stream_id'],'epoch':stream['epoch'],'seq':1}})
                self.assertEqual(receipt['delivery']['acked_through'],1)
                health = await call('memory_status',{'section':'transport'})
                self.assertFalse(health['transport_status']['transport_limit'])
            finally:store.close()


if __name__ == '__main__': unittest.main()
