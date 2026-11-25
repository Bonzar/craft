"""Payload-free timing records distinguish model latency from local pipeline work."""
import asyncio
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from prototypes.assertion_memory import query_timing as timing
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
                    'draft_generation', 'answer_verification', 'diagnostics', 'usage_accounting'}
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
                             {'submission','ready_probe','queue_wait','job_total','search','result_persist'})
        asyncio.run(run())
