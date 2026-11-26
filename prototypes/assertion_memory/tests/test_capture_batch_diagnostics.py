import json
from unittest import TestCase
from unittest.mock import patch

from prototypes.assertion_memory.capture_batch import run_next
from prototypes.assertion_memory.mcp_server import ServiceConfig
from prototypes.assertion_memory.model import ModelError
from prototypes.assertion_memory.tests import test_capture_batch as fixtures
from prototypes.assertion_memory.thought_trace import ThoughtTrace


class CaptureBatchDiagnosticsTest(TestCase):
    def setUp(self):
        self.fixture = fixtures.CaptureBatchTest()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.ids = [item['event']['id'] for item in self.fixture.capture()['receipts']]

    def diagnostic(self, event_id):
        page = self.fixture.service.status(event_id, section='job', limit=3000)
        return json.loads(page['content'])

    def test_after_generation_failure_with_zero_attempts(self):
        calls = []
        delegate = self.fixture.model(calls)

        def model(request):
            if request.task == 'Verify source thought batch':
                raise ModelError('very private provider message')
            return delegate(request)

        with patch.object(ServiceConfig, 'components', lambda *_: (model, None, 'test')):
            self.assertTrue(run_next(self.fixture.config, self.fixture.store, lambda:False))

        for event_id in self.ids:
            result = self.diagnostic(event_id)
            self.assertEqual((result['status'], result['phase'], result['attempts']), ('failed', 'extract', 0))
            self.assertEqual(result['capture_batch'], {
                'state':'blocked', 'failure_code':'batch_ModelError',
                'trace_coverage':'recorded_stages_only',
                'model_stages': {'draft_generation':'succeeded', 'batch_coverage_verification':'failed'},
            })
            self.assertNotIn('very private provider message', json.dumps(result))
            self.assertNotIn(self.fixture.batch_id, json.dumps(result))

    def test_unknown_trace_code_and_unauthorized_scope(self):
        t=self.fixture
        other=ThoughtTrace(t.store, 'source_model_batch', {'capture_batch_id':'c'*64})
        with t.store.transaction(write=True) as db:
            db.execute('UPDATE memory_capture_batches SET state=?,error_code=?',
                       ('blocked','unreviewed arbitrary exception text'))
            db.execute("""INSERT INTO memory_thought_model_calls(id,run_id,stage,request_hash,bindings,status,started_at)
                VALUES('other',?,'Verify source thought batch','private-hash','[]','failed','2026-11-26')""",(other.id,))
        result=self.diagnostic(self.ids[0])
        self.assertEqual(result['capture_batch'], {
            'state':'blocked', 'failure_code':'unknown', 'trace_coverage':'not_recorded', 'model_stages':{},
        })
        with self.assertRaises(ValueError):
            t.service.status(self.ids[0],scope='unrelated',section='job',limit=3000)

    def test_nonbatch_does_not_gain_batch_metadata(self):
        event=self.fixture.service.capture_message('other','one','user','user',
            'fixture source',1,source='fixture://other')['event']['id']
        self.assertNotIn('capture_batch',self.diagnostic(event))
