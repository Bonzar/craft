"""Retain specific answer limits without turning unrelated health into factual claims."""
from unittest import TestCase
from unittest.mock import patch

from prototypes.assertion_memory.query_coverage import project
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_service import ThoughtService
from prototypes.assertion_memory.thought_status import query_diagnostics


class QueryCoverageTest(TestCase):
    def setUp(self):
        self.service=ThoughtService(self.enterContext(MemoryStore(':memory:')))

    def accepted(self, *, answer='The approved serial is PX-41.'):
        return {'answer':answer,'answer_accepted':True,'answer_payload_complete':True,'answer_continuations':[]}

    def test_accepted_original_quote_not_downgraded_by_unrelated_queue(self):
        source=self.service.capture_message('serial','first','owner','user','The serial is PX-41.',0,'fixture://serial')
        self.service.capture_message('unrelated','second','owner','user','The garden is wet.',0,'fixture://unrelated')
        thought=self.service.thoughts.publish('The serial is PX-41.',question='What serial was reported?')
        with patch('prototypes.assertion_memory.thought_query.reuse_without_generation',return_value=thought):
            result=self.service.ready_query('What serial was reported?')
        self.assertEqual(self.service.queue.get_event(source['event']['id']).prompt,result['answer'])
        self.assertGreaterEqual(result['pending_count'],1)
        self.assertEqual(result['processing_state'],'pending')
        self.assertEqual(result['completeness'],'available_evidence')
        self.assertFalse(result['refresh_required'])
        self.assertEqual(result['query_coverage']['evidence'],'supported')
        self.assertEqual(result['query_coverage']['processing_gap'],'unassessed')
        self.assertEqual(result['query_coverage']['answer_delivery'],'inline')

    def test_global_undelivered_report_preserved_without_asserting_relevance_or_downrating_quote(self):
        # A registered but unobserved stream is not proof of an omitted source.
        stream=self.service.ingest_register('fixture-device','new-session')
        unknown=query_diagnostics(self.service,self.accepted(),None)
        self.assertTrue(unknown['transport_limit'])
        self.assertEqual(unknown['query_coverage']['transport']['status'],'unobserved')
        self.service.ingest_report(stream['stream_id'],stream['epoch'],1,2,'catching_up',0)
        result=query_diagnostics(self.service,self.accepted(),'unrelated-scope')
        self.assertTrue(result['transport_limit'])
        self.assertTrue(result['refresh_required'])
        self.assertEqual(result['transport_status']['known_pending_count'],2)
        self.assertEqual(result['completeness'],'available_evidence')
        self.assertEqual(result['query_coverage']['transport'],
                         {'status':'known_delivery_gap','scope':'global','query_relevance':'unassessed'})

    def test_no_data_is_not_a_supported_answer_or_world_absence(self):
        result=self.service.query('Does the reported permit cover delivery?')
        self.assertFalse(result['answer_accepted'])
        self.assertEqual(result['query_coverage']['evidence'],'not_established')
        self.assertEqual(result['query_coverage']['evidence_scope'],'retrieved_records')
        self.assertEqual(result['query_coverage']['processing_gap'],'unassessed')

    def test_specific_unresolved_question_and_actual_relevant_gap_are_visible(self):
        partial={'answer':'The club picked the 19:40 screening.','answer_accepted':False,
                 'partial_answer':True,'missing_question':'Did the owner confirm?',
                 'knowledge_limit':'There is no confirmation in the retrieved records.',
                 'answer_payload_complete':True,'answer_continuations':[]}
        result=query_diagnostics(self.service,partial,None)
        self.assertEqual(result['completeness'],'incomplete')
        self.assertEqual(result['query_coverage']['evidence'],'partial')
        self.assertEqual(result['missing_question'],'Did the owner confirm?')
        accepted=self.accepted()
        self.assertEqual(project(accepted,processing_gap=True)['processing_gap'],'relevant')
        self.assertEqual(project(accepted,processing_gap=True)['evidence'],'qualified')
        self.assertEqual(project(accepted,processing_gap=False)['processing_gap'],'none_identified')

    def test_answer_pages_independent_of_proof_pages_and_relevance(self):
        pages=[{'tool':'memory_source','arguments':{'offset':6000}}]
        response=self.accepted()|{'answer_payload_complete':False,'answer_continuations':pages,
                                  'response_complete':False}
        result=query_diagnostics(self.service,response,None)
        self.assertEqual(result['query_coverage']['answer_delivery'],'paged')
        self.assertEqual(result['query_coverage']['evidence'],'supported')
        self.assertEqual(result['answer_continuations'],pages)
        self.assertEqual(project(self.accepted()|{'response_complete':False})['answer_delivery'],'inline')

    def test_published_mcp_tool_description_never_turns_backlog_into_answer_warning(self):
        import asyncio
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from prototypes.assertion_memory.mcp_server import ServiceConfig, create_server
        with TemporaryDirectory() as folder:
            server=create_server(ServiceConfig(db=str(Path(folder)/'fixture.sqlite3'),provider='none'),
                                 background=False)
            tools=asyncio.run(server.list_tools())
        description=next(tool.description for tool in tools if tool.name=='memory_query')
        self.assertIn('query_coverage.evidence',description)
        self.assertNotIn('Pending work means this answer is incomplete',description)
        self.assertIn('Unassessed means unknown',description)
