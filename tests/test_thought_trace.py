import unittest
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.model import ModelRequest,ModelError,format_prompt
from prototypes.assertion_memory.thought_trace import ThoughtTrace


class ThoughtTraceTest(unittest.TestCase):
    def setUp(self):
        self.store=MemoryStore(':memory:');self.addCleanup(self.store.close)
        self.trace=ThoughtTrace(self.store,'fixture')

    def test_exact_payload_binding_and_unknown_usage_without_remote_write_lock(self):
        request=ModelRequest('fixture',[{'text':'Only during pilot.'}])
        def provider(value):
            self.assertFalse(self.store._connection.in_transaction)
            return '{"text":"Only during pilot."}'
        self.trace.call(provider,request,[{'thought_id':'source','version':1}])
        call=self.trace.calls()[0]
        self.assertEqual(self.trace.payload(call['request_hash']),format_prompt(request))
        self.assertEqual(call['bindings'],[{'thought_id':'source','version':1}])
        self.assertIsNone(call['usage'])

    def test_candidates_and_actual_selection_are_distinct(self):
        self.trace.record('search',candidates=['a','b'],selected=['a'])
        row=self.trace.events()[0]['data']
        self.assertEqual(row['candidates'],['a','b']);self.assertEqual(row['selected'],['a'])

    def test_mutation_is_failed_and_original_input_is_retained(self):
        records=[{'text':'Original'}];request=ModelRequest('fixture',records)
        def provider(value):records[0]['text']='Changed';return 'not accepted'
        with self.assertRaises(ModelError):self.trace.call(provider,request)
        call=self.trace.calls()[0]
        self.assertEqual(call['status'],'failed')
        self.assertIn('Original',self.trace.payload(call['request_hash']))
