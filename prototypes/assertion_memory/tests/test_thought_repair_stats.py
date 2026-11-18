"""Repair accounting counts validated draft batches, not JSON transport retries."""
import json
import unittest
from types import SimpleNamespace
from prototypes.assertion_memory.model import CandidateSearch,ModelError,ModelRequest
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.thought_trace import ThoughtTrace
from prototypes.assertion_memory.thought_service import traced_engine,ThoughtService
from prototypes.assertion_memory.thought_repair_stats import repair_statistics


class Provider:
    def __init__(self,name,call):self.requested_model=name;self.reasoning_effort='low';self.call=call
    def __call__(self,request):return self.call(request)


class Router:
    def __init__(self,call,profile='luna-sol-low'):
        self.primary=Provider('luna',call);self.review=Provider('sol',call);self.route_profile_key=profile
    def model_for(self,task):return self.review if task.startswith('Verify') or task=='Check ready thought' else self.primary
    def __call__(self,request):return self.model_for(request.usage_task or request.task)(request)


class ThoughtRepairStatsTest(unittest.TestCase):
    def setup_memory(self):
        memory=MemoryStore(':memory:');self.addCleanup(memory.close)
        return memory,ThoughtStore(memory),CaptureQueue(memory)

    def execute(self,memory,store,raw,router,operation='source'):
        trace=ThoughtTrace(memory,operation)
        engine=traced_engine(store,router,CandidateSearch(),trace)
        result=engine.ingest(raw,consolidate=False)
        trace.finish('completed')
        return result,trace

    @staticmethod
    def constructed(request):
        option=next(fragment['option'] for row in request.records for fragment in row.get('fragments',[]))
        return json.dumps({'thoughts':[{'text':'Approval is required.','question':None,'support_fragments':[option]}],'needs_detail':None})

    def test_first_accept_records_requested_route_and_leaves_actual_and_tokens_unknown(self):
        memory,store,queue=self.setup_memory();raw=queue.capture_event('s','a','Approval is required.',source='fixture')
        router=Router(lambda request:self.constructed(request) if request.task=='Construct thought' else json.dumps({'verdict':'accept','text':'Grounded.'}))
        _,trace=self.execute(memory,store,raw,router)
        report=repair_statistics(memory,router)
        self.assertEqual(report['summary']['construct_cycles'],1)
        self.assertEqual(report['summary']['first_review_accept'],1)
        self.assertEqual(report['summary']['accepted_after_repair'],0)
        self.assertEqual([row['requested_model'] for row in trace.calls()],['luna','sol'])
        self.assertTrue(all(row['actual_model'] is None for row in trace.calls()))
        self.assertTrue(all(row['requested_reasoning_effort']=='low' for row in trace.calls()))
        self.assertEqual(report['tokens']['input_tokens']['unknown_calls'],2)
        self.assertEqual(report['publication_refs'][0]['construction_call_id'],trace.calls()[0]['id'])

    def test_semantic_reject_reconstruction_has_explicit_denominator_and_referenced_example(self):
        memory,store,queue=self.setup_memory();raw=queue.capture_event('s','b','Approval is required.',source='fixture')
        reviewed=0
        def provider(request):
            nonlocal reviewed
            if request.task=='Construct thought':return self.constructed(request)
            reviewed+=1
            return json.dumps({'verdict':'reject' if reviewed==1 else 'accept','text':'Keep the approval condition.'})
        router=Router(provider)
        self.execute(memory,store,raw,router,'refresh')
        report=repair_statistics(memory,router)
        self.assertEqual(report['summary']['construct_cycles'],2)
        self.assertEqual(report['summary']['first_review_reject'],1)
        self.assertEqual(report['summary']['accepted_after_repair'],1)
        self.assertEqual(report['summary']['semantic_reconstructions'],1)
        self.assertEqual(report['summary']['repair_calls'],2)
        self.assertTrue(report['examples'][0]['before']['response_hash'])
        self.assertIn('approval',report['examples'][0]['reason'])
        self.assertEqual({row['operation'] for row in report['by_stage']},{'refresh'})

    def test_malformed_verify_is_format_overhead_not_another_construct_or_semantic_reject(self):
        memory,store,queue=self.setup_memory();raw=queue.capture_event('s','c','Approval is required.',source='fixture')
        reviews=0
        def provider(request):
            nonlocal reviews
            if request.task=='Construct thought':return self.constructed(request)
            reviews+=1
            return 'not JSON' if reviews==1 else json.dumps({'verdict':'accept','text':'Grounded.'})
        router=Router(provider)
        self.execute(memory,store,raw,router,'query')
        report=repair_statistics(memory,router)
        self.assertEqual(report['summary']['construct_cycles'],1)
        self.assertEqual(report['summary']['first_review_accept'],1)
        self.assertEqual(report['summary']['first_review_reject'],0)
        self.assertEqual(report['summary']['format_retries'],1)
        self.assertEqual(report['summary']['semantic_reconstructions'],0)

    def test_cached_construct_resume_keeps_denominator_and_reported_usage_is_bounded(self):
        from prototypes.assertion_memory.usage_context import notify_attempt_usage
        memory,store,queue=self.setup_memory()
        queue.capture_event('s','retry','Approval is required.',source='fixture')
        reviews=0
        def provider(request):
            nonlocal reviews
            if request.task=='Construct thought':return self.constructed(request)
            reviews+=1
            if reviews==1:raise ModelError('temporary reviewer outage')
            notify_attempt_usage('succeeded',SimpleNamespace(actual_model='reported-sol-snapshot',
                values={'input_tokens':20,'output_tokens':5,'cached_input_tokens':None,'reasoning_output_tokens':None}),2)
            return json.dumps({'verdict':'accept','text':'Grounded.'})
        old_router=Router(provider,profile='sol-sol-low');old_router.primary.requested_model='sol'
        service=ThoughtService(memory,old_router)
        with self.assertRaises(ModelError):service.query('What approval is required?')
        router=Router(provider)
        service.engine.model=router
        service.query('What approval is required?')
        report=repair_statistics(memory,router)
        self.assertEqual(report['summary']['construct_cycles'],0)
        self.assertEqual(report['summary']['cached_constructs_resumed'],1)
        creator=next(row for row in report['cached_origins'] if row['task']=='Construct thought')
        self.assertEqual(creator['requested_origin_model'],'sol')
        self.assertEqual(creator['origin_route_profile'],'sol-sol-low')
        self.assertEqual(repair_statistics(memory,old_router)['summary']['construct_cycles'],1)
        self.assertEqual(report['coverage']['actual_model_reported_calls'],1)
        self.assertEqual(report['tokens']['input_tokens'],{'reported_total':20,'reported_calls':1,'unknown_calls':0})
        self.assertTrue(report['publication_refs'])
        with memory.transaction() as db:
            creator=db.execute('SELECT requested_model FROM memory_thought_model_calls WHERE id=?',
                (report['publication_refs'][0]['construction_call_id'],)).fetchone()
        self.assertIsNotNone(creator)
        self.assertEqual(creator[0],'sol')
        extra=ThoughtTrace(memory,'query');extra.bind_model(router);extra.finish('knowledge_limit')
        before=memory._connection.total_changes
        bounded=repair_statistics(memory,router,limit=1)
        self.assertTrue(bounded['coverage']['truncated'])
        self.assertEqual(bounded['coverage']['calls_scanned'],1)
        self.assertEqual(memory._connection.total_changes,before)

    def test_queue_retry_metadata_counts_actual_extra_calls_but_not_similar_queries(self):
        memory,store,queue=self.setup_memory();raw=queue.capture_event('s','queued','Approval is required.',source='fixture')
        router=Router(lambda request:self.constructed(request) if request.task=='Construct thought' else json.dumps({'verdict':'accept','text':'Grounded.'}))
        trace=ThoughtTrace(memory,'source',{'event_id':raw.id,'job_id':raw.id,'attempt':2,
            'previous_error':'SemanticReviewRejected: required condition omitted'})
        traced_engine(store,router,CandidateSearch(),trace).ingest(raw,consolidate=False)
        trace.finish('completed')
        query=ThoughtTrace(memory,'query',{'attempt':2,'previous_error':'not a worker attempt'})
        query.bind_model(router);query.finish('knowledge_limit')
        report=repair_statistics(memory,router)
        self.assertEqual(report['summary']['queue_retry_attempts'],1)
        self.assertEqual(report['summary']['queue_retry_calls'],2)
        self.assertEqual(report['summary']['repair_calls'],2)
        self.assertEqual({row['repair_kind'] for row in trace.calls()},{'queue_retry'})
        self.assertIn('SemanticReviewRejected',report['queue_retry_causes'][0]['previous_error'])
        self.assertEqual(report['summary']['construct_cycles'],1)

    def test_rejecting_one_unknown_cached_draft_cannot_relabel_another_as_luna(self):
        import hashlib
        from prototypes.assertion_memory.model import format_prompt
        from prototypes.assertion_memory.thought_schema import response_schema
        memory,store,_=self.setup_memory()
        shared='Approval is required.'
        def provider(request):
            if request.task=='Construct thought':return self.constructed(request)
            text=request.records[-1]['text']
            return json.dumps({'verdict':'accept' if text==shared else 'reject','text':'Fixture semantic verdict.'})
        router=Router(provider);trace=ThoughtTrace(memory,'source')
        engine=traced_engine(store,router,CandidateSearch(),trace)
        def cached(text,label):
            request=ModelRequest('Construct thought',[{'text':text}],instructions=label,
                response_schema=response_schema('Construct thought',support_count=1))
            request_hash=hashlib.sha256(format_prompt(request).encode()).hexdigest()
            response=json.dumps({'thoughts':[{'text':text,'question':None,'support_fragments':[0]}],'needs_detail':None})
            response_hash=hashlib.sha256(response.encode()).hexdigest()
            trace.record('model_input',details={'task':'Construct thought','request_hash':request_hash,'purpose':'composite'})
            trace.record('model_step_reused',details={'source_call_id':None,'request_hash':request_hash,'response_hash':response_hash})
            trace.record('model_output',details={'task':'Construct thought','request_hash':request_hash,'response_hash':response_hash,
                'has_draft_batch':True,'draft_text_hashes':[hashlib.sha256(text.encode()).hexdigest()]})
        cached(shared,'unknown cached A')
        engine._call('Verify thought',[{'role':'draft','text':shared}],[],'Fixture review A')
        engine._call('Construct thought',[{'role':'material','fragments':[{'option':0,'text':shared}]}],[],
            'Fresh Luna B',support_count=1)
        engine._call('Verify thought',[{'role':'draft','text':shared}],[],'Fixture review B')
        cached('Different unsupported claim.','unknown cached C')
        engine._call('Verify thought',[{'role':'draft','text':'Different unsupported claim.'}],[],'Fixture reject C')
        published=store.publish(shared)
        trace.record('publication',details={'thought':{'thought_id':published.id,'version':published.version}})
        trace.finish()
        self.assertIsNone(repair_statistics(memory,router)['publication_refs'][0]['construction_call_id'])

    def test_technical_interrupted_and_bounded_read_only_cutover_do_not_relabel_old_unknown_calls(self):
        memory,store,queue=self.setup_memory()
        def fail(request):raise ModelError('provider temporarily unavailable')
        old=ThoughtTrace(memory,'source')
        with self.assertRaises(ModelError):old.call(fail,ModelRequest('Construct thought',[]))
        old.finish('failed')
        router=Router(lambda request:(_ for _ in ()).throw(KeyboardInterrupt()),profile='new-route')
        technical=ThoughtTrace(memory,'source')
        with self.assertRaises(ModelError):technical.call(Router(fail,profile='new-route'),ModelRequest('Construct thought',[]))
        technical.finish('failed')
        trace=ThoughtTrace(memory,'query')
        with self.assertRaises(KeyboardInterrupt):trace.call(router,ModelRequest('Verify thought',[]))
        trace.finish('failed')
        empty_trace=ThoughtTrace(memory,'query');empty_trace.bind_model(router);empty_trace.finish('knowledge_limit')
        before=memory._connection.total_changes
        report=repair_statistics(memory,router,limit=3)
        self.assertEqual(memory._connection.total_changes,before)
        self.assertEqual(report['summary']['interrupted_calls'],1)
        self.assertEqual(report['coverage']['calls_scanned'],2)
        self.assertEqual(report['summary']['technical_failed_calls'],1)
        self.assertEqual(report['run_outcomes']['knowledge_limit'],1)
        self.assertEqual(report['by_operation_outcome']['query']['no_accepted_output'],2)
        self.assertEqual(report['scope']['route_profile'],'new-route')
        self.assertIsNone(old.calls()[0]['requested_model'])
        service=ThoughtService(memory,router)
        before=memory._connection.total_changes
        self.assertIn('construct_cycles',service.status()['thought_repairs'])
        self.assertEqual(memory._connection.total_changes,before)


if __name__=='__main__':unittest.main()
