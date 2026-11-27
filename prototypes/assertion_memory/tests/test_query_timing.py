"""Payload-free timing records distinguish model latency from local pipeline work."""
import asyncio
import json
from pathlib import Path
import subprocess
import sqlite3
import tempfile
import time
import threading
import unittest
from unittest.mock import patch

from prototypes.assertion_memory import genai_traces, query_timing as timing
from prototypes.assertion_memory.model import ModelProviderUnavailable, SubprocessModel
from prototypes.assertion_memory.query_jobs import QueryJobs
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_service import ThoughtService

from .test_thought_engine import ACCEPT, ScriptedModel
from .test_thought_query_graph import navigation


class QueryTimingTest(unittest.TestCase):
    def test_query_trace_times_canonical_stages_and_hides_every_payload(self):
        marker = 'SENSITIVE_PAYLOAD_SHOULD_NOT_BE_IN_THE_JOURNAL'
        candidate_text = 'The group chose the 19:40 showing. ' + marker
        confirmation = 'Vlad said he personally confirmed the 19:40 showing.'
        question = 'Which showing did the group choose, and did Vlad explicitly confirm it?'
        gap = 'Did Vlad confirm the 19:40 showing? ' + marker
        logs = []
        class SlowReadiness(ScriptedModel):
            def __call__(self, request):
                if request.task == 'Check ready thought':
                    time.sleep(0.021)
                return super().__call__(request)
        model = SlowReadiness(navigation('focus',candidate_text),
            {'verdict':'lookup','question':gap,'text':'Ask for separate confirmation.'},
            {'verdict':'build','question':None,'text':'The newly opened source confirms it.'},
            {'thought':{'text':candidate_text+' '+confirmation,'question':None,'support_fragments':[0,1]},
             'needs_detail':None},ACCEPT)
        with MemoryStore(':memory:') as memory, patch.object(timing,'_write_line',logs.append):
            service = ThoughtService(memory,model)
            service.thoughts.publish(candidate_text,metadata={'verification':'accepted'})
            service.thoughts.publish(confirmation,metadata={'verification':'accepted'})
            result = service.query(question)
        self.assertTrue(result['answer_accepted'])
        self.assertNotIn(marker.encode(), b''.join(logs))
        rows = [json.loads(log) for log in logs]
        self.assertEqual(len({r['trace_id'] for r in rows}), 1)
        self.assertEqual({r['run_id'] for r in rows if 'run_id' in r},{result['run_id']})
        completed = [r for r in rows if r['status'] == 'ok' and r['stage'] != 'query_outcome']
        required = {'query_total', 'snapshot', 'search', 'raw_search', 'navigation',
                    'answer_neighborhood', 'readiness_check', 'gap_search',
                    'draft_generation', 'answer_verification', 'diagnostics', 'usage_accounting',
                    'answer_persist', 'answer_check_write'}
        self.assertTrue(required <= {r['stage'] for r in completed})
        self.assertTrue(all(type(r['duration_ms']) is int and r['duration_ms'] >= 0 for r in completed))
        attempts = [r for r in completed if r['stage'] == 'provider_attempt']
        self.assertEqual(len({r['call_id'] for r in attempts}),5)
        readiness = [r['duration_ms'] for r in attempts if r['model_phase']=='readiness_check']
        self.assertEqual(len(readiness),2)
        self.assertTrue(all(n >= 20 for n in readiness))

    def test_cli_timeout_and_retry_signals_are_bounded_enums(self):
        marker = 'AUTH_SECRET_AND_RAW_COMMAND_NOT_FOR_LOGGING'
        logs = []
        with patch.object(timing,'_write_line',logs.append), timing.bind(trace_id='a'*32):
            # Keep a complete retry event followed by truncated UTF-8/JSON.
            partial = json.dumps({'type':'error','message':'Reconnecting '+marker}).encode()
            error = subprocess.TimeoutExpired([marker], 3,
                output=partial + b'\n{"type":"error","message":"\xff',
                stderr=('connection refused '+marker).encode() + b'\nretrying request')
            model = SubprocessModel([marker], usage_protocol='codex')
            with patch.object(model,'_run_command',side_effect=error):
                with self.assertRaisesRegex(ModelProviderUnavailable,'Codex timed out'):
                    model._generate(marker,None)
            timing.emit(marker, 'failed')
            timing.emit('provider_cli', marker)
        self.assertNotIn(marker.encode(), b''.join(logs))
        rows = [json.loads(log) for log in logs]
        self.assertEqual([(r['stage'],r['status']) for r in rows], [
            ('provider_cli','start'),('provider_cli','timeout'),
            ('provider_retry_signal','connection_signal'),('provider_retry_signal','retry_signal')])
        self.assertEqual(rows[3]['signal_count'],2)

    def test_telemetry_scanner_cannot_break_a_successful_response(self):
        model = SubprocessModel(['fixture'], usage_protocol='codex')
        complete = subprocess.CompletedProcess(['fixture'], 0, 'answer', '')
        with timing.bind(trace_id='b'*32), patch.object(timing,'_write_line'), \
                patch.object(model,'_run_command',return_value=complete), \
                patch.object(SubprocessModel,'_scan_cli_signals',side_effect=ValueError('broken observation')) as scanner:
            self.assertEqual(model._generate('question',None),'answer')
            scanner.assert_called_once_with('answer','')

    def test_warm_query_trace_is_internal_and_reuses_durable_id(self):
        async def run():
            spans = []
            class Exporter:
                def put(self, span):
                    spans.append(span)
            def no_worker(**args):
                self.fail('warm path must not generate')
            with tempfile.TemporaryDirectory() as tmp, patch.object(timing,'_write_line'), \
                 patch.object(genai_traces,'_exporter',Exporter()):
                jobs = QueryJobs(Path(tmp)/'jobs.sqlite3',no_worker,
                                 lambda **_: {'answer_accepted':True, 'answer':'ready'})
                async with jobs.lifespan():
                    def record(receipt):
                        self.assertEqual(len(receipt),1)
                        with timing.bind(trace_id=receipt[0]):
                            timing.emit('vm_http_submission','ok',5)
                    with timing.transport_trace() as anonymous:
                        result = await jobs(query='private-input-never-log')
                        self.assertNotIn('query_job_id',result)
                        record(anonymous)
                    with timing.transport_trace() as durable:
                        result = await jobs(query='private-input-never-log',query_request_id='safe-fixture')
                        self.assertEqual(durable,[result['query_job_id']])
                        record(durable)
                    with timing.transport_trace() as replay:
                        self.assertEqual(await jobs(query='private-input-never-log',query_request_id='safe-fixture'),result)
                        self.assertEqual(replay,durable)
                        record(replay)
                    self.assertNotEqual(anonymous,durable)
            for trace, expected in ((anonymous,1),(durable,2)):
                names = [s['name'] for s in spans if s['traceId']==trace[0]]
                self.assertEqual(names.count('memory_query.vm_http_submission'),expected)
                self.assertIn('memory_query.submission',names)
                self.assertNotIn('memory_query.job_total',names)
                self.assertEqual(names.count('memory_query.ready_result_persist'),expected-1)
                self.assertNotIn('private-input-never-log',json.dumps(spans))
        asyncio.run(run())

    def test_readiness_timeout_and_worker_share_payload_free_otlp_trace(self):
        async def run():
            gate = threading.Event()
            spans = []
            class Exporter:
                def put(self, span):
                    spans.append(span)
            def query(**args):
                gate.wait(1)
                with timing.span('search'):
                    return {'answer_accepted': True, 'answer':args['query']}
            with tempfile.TemporaryDirectory() as tmp, patch.object(timing,'_write_line'), \
                 patch.object(genai_traces,'_exporter',Exporter()):
                jobs = QueryJobs(Path(tmp)/'jobs.sqlite3',query,lambda **_:None,wait_seconds=.025)
                async with jobs.lifespan():
                    result = await jobs(query='private-input-never-log', query_request_id='private-request-id')
                    self.assertIn(result['query_state'],('queued','running'))
                    gate.set()
                    for _ in range(100):
                        complete = await jobs(query_job_id=result['query_job_id'])
                        if complete['query_state']=='completed' and any(s['name']=='memory_query.job_total' for s in spans):
                            break
                        await asyncio.sleep(.01)
                    self.assertEqual(complete['query_state'],'completed')
            self.assertEqual({s['traceId'] for s in spans},{result['query_job_id']})
            indexed = {s['name'].split('.')[-1]:s for s in spans}
            self.assertEqual(len(indexed),len(spans), 'measured phases should not double-export')
            for stage in ('queue_wait','result_persist','search'):
                self.assertEqual(indexed[stage]['parentSpanId'],indexed['job_total']['spanId'])
            deadline = indexed['query_response_wait']
            attrs = {a['key']:a['value'] for a in deadline['attributes']}
            self.assertEqual(attrs['memory.outcome'], {'stringValue':'timeout'})
            self.assertEqual(deadline['status']['code'],2)
            wire = json.dumps(spans)
            self.assertNotIn('private-input-never-log',wire)
            self.assertNotIn('private-request-id',wire)
            self.assertNotIn('answer_accepted',wire)
            self.assertLessEqual(int(indexed['job_total']['startTimeUnixNano']), int(indexed['queue_wait']['startTimeUnixNano']))
        asyncio.run(run())

    def test_result_retry_measures_only_write_and_never_repeats_model(self):
        spans, calls = [], []
        class Exporter:
            def put(self, span):
                spans.append(span)
        def query(**args):
            calls.append(1)
            return {'answer_accepted': True}
        key='7'*32
        with tempfile.TemporaryDirectory() as tmp, patch.object(timing,'_write_line'), \
             patch.object(genai_traces,'_exporter',Exporter()), timing.bind(trace_id=key):
            jobs=QueryJobs(Path(tmp)/'jobs.sqlite3',query,lambda **_:None)
            jobs._init()
            real=jobs._connect
            with patch.object(jobs,'_connect',side_effect=[sqlite3.OperationalError('PRIVATE-DB-PATH'),real()]):
                jobs._execute(key,'{}',queue_wait_ms=15)
        self.assertEqual(calls,[1])
        match,= [row for row in spans if row['name']=='memory_query.result_persist']
        attrs={a['key']:a['value'] for a in match['attributes']}
        self.assertEqual(attrs['memory.retry_count'],{'intValue':'1'})
        self.assertNotIn('PRIVATE-DB-PATH',json.dumps(spans))

    def test_worker_submission_and_execution_use_same_job_identifier(self):
        async def run():
            logs = []
            def query(**args):
                with timing.span('search'):
                    return {'answer_accepted': True, 'answer':args['query']}
            with tempfile.TemporaryDirectory() as tmp, patch.object(timing,'_write_line',logs.append):
                jobs = QueryJobs(Path(tmp)/'jobs.sqlite3',query,lambda **_:None,wait_seconds=.3)
                async with jobs.lifespan():
                    result = await jobs(query='do not log this', query_request_id='caller-id-not-for-logs')
                    for _ in range(50):
                        if result['query_state']=='completed':
                            break
                        await asyncio.sleep(.01)
                        result = await jobs(query_job_id=result['query_job_id'])
            self.assertEqual(result['query_state'],'completed')
            wire = b''.join(logs)
            self.assertNotIn(b'do not log this',wire)
            self.assertNotIn(b'caller-id-not-for-logs',wire)
            rows = [json.loads(log) for log in logs]
            self.assertEqual({r['trace_id'] for r in rows},{result['query_job_id']})
            self.assertEqual({r['stage'] for r in rows},
                             {'submission','ready_probe','queue_wait','job_total','search','result_persist','query_response_wait'})
        asyncio.run(run())
