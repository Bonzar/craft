"""Privacy, hierarchy and nonblocking loopback export for metadata-only GenAI tracing."""
import json
from pathlib import Path
import subprocess
import tempfile
from threading import Event
import unittest
from unittest.mock import MagicMock, Mock, patch

from prototypes.assertion_memory import genai_traces as traces
from prototypes.assertion_memory import query_timing as timing
from prototypes.assertion_memory.model import CodexModel, ModelProviderUnavailable
from prototypes.assertion_memory.model_usage import UsageLedger
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_service import ThoughtService

from .test_thought_query_graph import navigation


def attrs(item):
    return {a['key']: next(iter(a['value'].values())) for a in item['attributes']}


class ExporterTest(unittest.TestCase):
    def test_opt_in_requires_matching_trusted_lane(self):
        with patch.object(traces,'_exporter',None), patch.object(traces,'_Exporter') as make:
            with patch.dict(traces.os.environ,{},clear=True):
                traces.configure('test')
                traces.configure('prod')
                self.assertFalse(make.called)
            with patch.dict(traces.os.environ,{traces.ENABLE:'test'},clear=True):
                traces.configure('prod')
                self.assertFalse(make.called)
                traces.configure('test')
                traces.configure('test')
                make.assert_called_once_with('test')

    def test_wire_has_exact_loopback_destination_and_metadata(self):
        transport = MagicMock()
        transport.getresponse.return_value.__enter__.return_value = Mock()
        for lane,port in [('test',14318),('prod',24318)]:
            with patch.object(traces,'HTTPConnection',return_value=transport) as conn:
                producer = object.__new__(traces._Exporter)
                producer.lane = lane
                span = {'traceId':'a'*32, 'spanId':'b'*16, 'kind':1}
                producer._send([span])
                conn.assert_called_once_with('127.0.0.1',port,timeout=1)
                verb,path,payload,headers = transport.request.call_args.args
                self.assertEqual((verb,path,headers),('POST','/v1/traces',{'Content-Type':'application/json'}))
                body = json.loads(payload)['resourceSpans'][0]
                self.assertEqual(attrs(body['resource']),{'service.name':'assertion-memory-'+lane,
                    'deployment.environment.name':lane})
                self.assertEqual(body['scopeSpans'][0]['spans'], [span])
                transport.reset_mock()

    def test_queue_saturation_does_not_wait_for_collector(self):
        busy,recover = Event(),Event()
        class BlockedCollector(traces._Exporter):
            QUEUE_SIZE=3
            BATCH_SIZE=1
            def _send(self,batch):
                busy.set()
                recover.wait(2)
        producer=BlockedCollector('test')
        producer.put({})
        self.assertTrue(busy.wait(1))
        try:
            for _ in range(100):
                producer.put({})
            self.assertFalse(recover.is_set())
            self.assertEqual(producer._queue.qsize(),3)
        finally:
            recover.set()


class GenAIQueryTest(unittest.TestCase):
    def test_actual_pipeline_exports_correlated_spans_and_only_known_model_usage(self):
        secret='REDACTED_TEST_CREDENTIAL_AND_THOUGHT_TEXT'
        thought='The test group chose the 19:40 showing. '+secret
        question='What did the test group choose? '+secret
        rows = []
        writer = Mock(put=rows.append)
        with tempfile.TemporaryDirectory() as directory, MemoryStore(':memory:') as store:
            model=CodexModel(binary='test-codex',model='gpt-6.2-sol',
                             usage_ledger=UsageLedger(Path(directory)/'usage.sqlite3'))
            service=ThoughtService(store,model)
            service.thoughts.publish(thought,metadata={'verification':'accepted'})
            def completed(text,usage=None,model=None):
                item={'type':'item.completed','item':{'type':'agent_message','text':json.dumps(text)}}
                turn={'type':'turn.completed','usage':usage}
                if model is not None:
                    turn['model']=model
                return subprocess.CompletedProcess(['test-codex'],0,json.dumps(item)+'\n'+json.dumps(turn),'')
            replies=[
                completed(navigation('focus',thought), {'input_tokens':101,'output_tokens':17,
                    'cached_input_tokens':80,'reasoning_output_tokens':3}, 'gpt-6.2-sol-20261101'),
                completed({'verdict':'ready','question':None,'text':'The evidence answers fully.'}),
            ]
            with patch.object(traces,'_exporter',writer), patch.object(timing,'_write_line'), \
                    patch('prototypes.assertion_memory.model.SubprocessModel._run_command',side_effect=replies):
                result=service.query(question)
        self.assertTrue(result['answer_accepted'])
        self.assertNotIn(secret,json.dumps(rows))
        self.assertTrue(rows)
        self.assertEqual(len({s['traceId'] for s in rows}),1)
        root, = [s for s in rows if 'parentSpanId' not in s]
        self.assertEqual(root['name'],'memory_query.query_total')
        by_id = {s['spanId']:s for s in rows}
        for row in rows:
            self.assertGreaterEqual(int(row['endTimeUnixNano']),int(row['startTimeUnixNano']))
            if 'parentSpanId' in row:
                self.assertIn(row['parentSpanId'],by_id)
        genai=[s for s in rows if s['name']=='openai.chat']
        self.assertEqual(len(genai),2)
        phases={attrs(s)['memory.model_phase']:s for s in genai}
        nav=attrs(phases['navigation'])
        self.assertEqual(nav['gen_ai.request.model'],'gpt-6.2-sol')
        self.assertEqual(nav['gen_ai.response.model'],'gpt-6.2-sol-20261101')
        self.assertEqual(nav['gen_ai.usage.input_tokens'],'101')
        self.assertEqual(nav['gen_ai.usage.output_tokens'],'17')
        self.assertEqual(nav['gen_ai.usage.cache_read.input_tokens'],'80')
        self.assertEqual(nav['gen_ai.usage.reasoning.output_tokens'],'3')
        check=attrs(phases['readiness_check'])
        self.assertFalse(any(k.startswith('gen_ai.usage.') for k in check))
        self.assertNotIn('gen_ai.response.model',check)
        self.assertEqual(nav['gen_ai.operation.name'],'chat')
        self.assertEqual(nav['gen_ai.provider.name'],'openai')
        self.assertEqual(nav['memory.run_id'],result['run_id'])
        for item in genai:
            self.assertEqual(item['kind'],3)
            forbidden=('messages','instructions','definitions','arguments','result')
            self.assertFalse(any(any(part in key for part in forbidden) for key in attrs(item)))
            self.assertEqual(by_id[item['parentSpanId']]['name'],
                             'memory_query.'+attrs(item)['memory.model_phase'])

    def test_failure_spans_never_copy_exception_message(self):
        rows=[]
        with patch.object(traces,'_exporter',Mock(put=rows.append)), patch.object(timing,'_write_line'), \
                timing.bind(trace_id='a'*32):
            with self.assertRaisesRegex(ModelProviderUnavailable,'Codex timed out'):
                with timing.span('provider_attempt'):
                    traces.mark_codex('gpt-6.2-sol')
                    timing.emit('provider_retry_signal','retry_signal',signal_count=3)
                    raise ModelProviderUnavailable(timed_out=True)
        self.assertEqual(len(rows),1)
        row=rows[0]
        self.assertEqual(row['status']['code'],2)
        self.assertEqual(attrs(row)['memory.provider_retry_signals'],'3')
        self.assertNotIn('Codex timed out',json.dumps(row))
