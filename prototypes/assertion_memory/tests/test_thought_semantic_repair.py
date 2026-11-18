"""Source coverage and rejected checkpoints use the same bounded repair path."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from prototypes.assertion_memory.model import ModelError,ModelContextExceeded
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_worker import ThoughtWorker

ACCEPT={'verdict':'accept','text':'All current useful meanings and necessary conditions are retained.'}

from .prepared_support_helpers import drafts,record_text


class ThoughtSemanticRepairTest(unittest.TestCase):
    def memory(self):
        memory=MemoryStore(':memory:');self.addCleanup(memory.close)
        return memory,ThoughtStore(memory),CaptureQueue(memory)

    def test_new_reply_does_not_need_independent_context_decisions_repeated(self):
        memory,store,queue=self.memory()
        context=queue.capture_event('s','old','The user chose to show the overall picture rather than portraits.',source='fixture')
        text='Three hours is too long on weekdays. Prefer weekends, and lunch rather than dinner.'
        raw=queue.capture_event('s','new',text,source='fixture')
        def model(request):
            if request.task=='Construct thought':return json.dumps(drafts(request,(text,[text])))
            scoped='independent context-only' in request.instructions
            return json.dumps(ACCEPT if scoped else {'verdict':'reject','text':'The separate picture/portrait choice was omitted.'})
        result=ThoughtEngine(store,model).ingest(raw,[context],consolidate=False)
        self.assertEqual([row.text for row in result],[text])
        self.assertEqual({basis['event_id'] for basis in result[0].bases},{raw.id})

    def test_semantic_feedback_repairs_whole_refresh_without_dropping_peer_meaning(self):
        memory,store,queue=self.memory()
        first=store.publish('Approval is required.')
        second=store.publish('Evacuation is the exception.')
        target=store.publish('Approval is required. Evacuation is the exception.',question='When may it open?',
            bases=[{'thought_id':first.id,'version':1},{'thought_id':second.id,'version':1}])
        records=[{'role':'whole_refresh_scope','prior_content':target.text,'questions':['When may it open?','What is the exception?']},
            {'role':'material','text':first.text},{'role':'material','text':second.text}]
        bases=[{'thought_id':target.id,'version':1},{'thought_id':first.id,'version':1},{'thought_id':second.id,'version':1}]
        calls=[]
        def model(request):
            self.assertFalse(memory._connection.in_transaction);calls.append(request)
            if request.task=='Construct thought':
                if sum(row.task=='Construct thought' for row in calls)==1:return json.dumps(drafts(request,(first.text,[first.text])))
                self.assertIn('Preserve the evacuation exception',request.instructions)
                return json.dumps(drafts(request,(target.text,[first.text,second.text])))
            self.assertIn('ONE WHOLE thought',request.instructions)
            return json.dumps(ACCEPT if second.text in request.records[-1]['text'] else {'verdict':'reject','text':'Preserve the evacuation exception.'})
        result=ThoughtEngine(store,model)._construct(records,bases,target=target)[0]
        self.assertEqual(result.text,target.text)
        self.assertEqual([row.task for row in calls],['Construct thought','Verify thought']*2)

    def test_restart_discards_rejected_construct_but_reuses_independent_checked_passage(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'memory.sqlite'
            head='First independent meaning.';tail='Second meaning only when approved.'
            text=head+' '+('a'*870)+'\n\n'+tail+' '+('b'*870)
            calls=[];b_constructs=0
            def model(request):
                nonlocal b_constructs
                calls.append(request)
                if request.task=='Construct thought':
                    body=record_text(request.records[0])
                    if body==text:raise ModelContextExceeded('fixture requires passages')
                    if tail in body:
                        b_constructs+=1
                        if b_constructs==2:
                            self.assertIn('Keep its approval condition',request.instructions)
                            raise ModelError('temporary repair provider failure')
                        claim=tail if b_constructs>=3 else 'Second meaning.'
                        return json.dumps(drafts(request,(claim,[tail])))
                    return json.dumps(drafts(request,(head,[head])))
                draft_rows=[row for row in request.records if row.get('role')=='draft']
                if request.task=='Verify thought' and draft_rows[-1]['text']=='Second meaning.':
                    return json.dumps({'verdict':'reject','text':'Keep its approval condition.'})
                return json.dumps(ACCEPT)
            with MemoryStore(path) as memory:
                queue=CaptureQueue(memory);store=ThoughtStore(memory)
                raw=queue.capture_event('s','long',text,source='fixture')
                worker=ThoughtWorker(queue,ThoughtEngine(store,model),lane='source')
                with patch('prototypes.assertion_memory.thought_engine.RAW_CHUNK_CHARACTERS',1000):
                    self.assertEqual(worker.run_once().status,'pending')
                self.assertEqual(store.list_thoughts(),[])
            with MemoryStore(path) as memory:
                queue=CaptureQueue(memory);queue.retry(raw.id)
                store=ThoughtStore(memory);worker=ThoughtWorker(queue,ThoughtEngine(store,model),lane='source')
                with patch('prototypes.assertion_memory.thought_engine.RAW_CHUNK_CHARACTERS',1000):
                    self.assertEqual(worker.run_once().status,'completed')
                self.assertEqual({row.text for row in store.list_thoughts()},{head,tail})
            a_calls=[row for row in calls if row.task=='Construct thought' and record_text(row.records[0])!=text and head in record_text(row.records[0])]
            self.assertEqual(len(a_calls),1)
            self.assertEqual(b_constructs,3)

    def test_cross_passage_rejection_rebuilds_each_passage_with_reason_and_exact_other_ground(self):
        memory,store,queue=self.memory()
        head='Visits are permitted.';tail='Exception: visits are forbidden during storms.'
        text=head+' '+('a'*870)+'\n\n'+tail+' '+('b'*870)
        raw=queue.capture_event('s','cross',text,source='fixture')
        calls=[];reviews=0
        def model(request):
            nonlocal reviews
            self.assertFalse(memory._connection.in_transaction);calls.append(request)
            if request.task=='Construct thought':
                body=record_text(request.records[0])
                if body==text:raise ModelContextExceeded('fixture requires passages')
                repairing='Preserve the storm exception' in request.instructions
                if repairing and head in body:
                    self.assertTrue(any(tail in record_text(row) for row in request.records))
                    return json.dumps(drafts(request,('Visits are permitted except during storms.',[head,tail])))
                return json.dumps(drafts(request,(head,[head]))) if head in body else json.dumps(drafts(request,(tail,[tail])))
            if request.task=='Verify source thought batch':
                reviews+=1
                if reviews==1:return json.dumps({'verdict':'reject','text':'Preserve the storm exception in the permission thought.'})
            return json.dumps(ACCEPT)
        with patch('prototypes.assertion_memory.thought_engine.RAW_CHUNK_CHARACTERS',1000):
            result=ThoughtEngine(store,model).ingest(raw,consolidate=False)
        self.assertEqual(reviews,2)
        self.assertEqual(sum(row.task=='Construct thought' and record_text(row.records[0])==text for row in calls),1)
        self.assertIn('Visits are permitted except during storms.',[row.text for row in result])
        constructions=[row for row in calls if row.task=='Construct thought' and record_text(row.records[0])!=text]
        self.assertEqual(len(constructions),4)
        self.assertTrue(all('Preserve the storm exception' in row.instructions for row in constructions[2:]))

    def test_malformed_verification_keeps_successful_construct_for_worker_retry(self):
        memory,store,queue=self.memory();raw=queue.capture_event('s','x','Only after approval.',source='fixture')
        calls=[]
        def model(request):
            calls.append(request.task)
            if request.task=='Construct thought':return json.dumps(drafts(request,(raw.prompt,[raw.prompt])))
            return json.dumps({'verdict':'invalid','text':'Malformed review.'} if calls.count('Verify thought')<=2 else ACCEPT)
        worker=ThoughtWorker(queue,ThoughtEngine(store,model),lane='source')
        self.assertEqual(worker.run_once().status,'pending')
        queue.retry(raw.id)
        self.assertEqual(ThoughtWorker(queue,ThoughtEngine(store,model),lane='source').run_once().status,'completed')
        self.assertEqual(calls.count('Construct thought'),1)
        self.assertEqual(calls.count('Verify thought'),3)

    def test_two_semantic_rejections_are_recoverable_processing_error_not_knowledge_limit(self):
        from prototypes.assertion_memory.model import SemanticUnavailable
        from prototypes.assertion_memory.thought_service import ThoughtService
        memory,_,queue=self.memory()
        raw=queue.capture_event('s','query','Only after approval.',source='fixture')
        calls=[]
        def model(request):
            calls.append(request.task)
            return json.dumps(drafts(request,('Approval is optional.',[raw.prompt]))) if request.task=='Construct thought' else json.dumps(
                {'verdict':'reject','text':'The mandatory approval condition was dropped.'})
        service=ThoughtService(memory,model)
        with self.assertRaises(ModelError) as caught:
            service.query('What approval is required?')
        self.assertNotIsInstance(caught.exception,SemanticUnavailable)
        self.assertTrue(caught.exception.recoverable)
        self.assertEqual(service.thoughts.list_thoughts(),[])
        self.assertEqual(calls,['Construct thought','Verify thought']*2)
        with memory.transaction() as c:
            self.assertEqual(c.execute('SELECT status FROM memory_thought_runs').fetchone()[0],'failed')

    def test_context_only_repeats_are_omitted_before_full_current_source_coverage_check(self):
        memory,store,queue=self.memory()
        context=queue.capture_event('s','old','Earlier choice one. Earlier choice two.',source='fixture')
        raw=queue.capture_event('s','new','New scenario one. New scenario two.',source='fixture',actor='assistant')
        def model(request):
            if request.task=='Construct thought':return json.dumps(drafts(request,('Earlier choice one.',['Earlier choice one.']),
                ('New scenario one.',['New scenario one.']),('Earlier choice two.',['Earlier choice two.']),('New scenario two.',['New scenario two.'])))
            self.assertEqual([record_text(row) for row in request.records if row.get('role')=='source'],[raw.prompt])
            self.assertEqual(len([row for row in request.records if row.get('role')=='draft']),2)
            return json.dumps(ACCEPT)
        result=ThoughtEngine(store,model).ingest(raw,[context],consolidate=False)
        self.assertEqual({row.text for row in result},{'New scenario one.','New scenario two.'})
        self.assertTrue(all(any(basis.get('event_id')==raw.id for basis in row.bases) for row in result))

    def test_empty_draft_cannot_hide_a_new_short_confirmation_or_adopt_unrelated_choice(self):
        memory,store,queue=self.memory()
        proposal=queue.capture_event('s','proposal','Choose lunch on weekends.',source='fixture',actor='assistant')
        unrelated=queue.capture_event('s','other','Use portraits rather than an overview.',source='fixture')
        raw=queue.capture_event('s','yes','Да',source='fixture')
        calls=[]
        def model(request):
            calls.append(request.task)
            if request.task=='Construct thought':
                if calls.count('Construct thought')==1:return json.dumps(drafts(request))
                self.assertIn('The current reply adopts the lunch proposal',request.instructions)
                return json.dumps(drafts(request,('The user agreed to lunch on weekends.',['Да',proposal.prompt])))
            return json.dumps(ACCEPT if any(row.get('role')=='draft' for row in request.records) else
                {'verdict':'reject','text':'The current reply adopts the lunch proposal; preserve that new decision only.'})
        result=ThoughtEngine(store,model).ingest(raw,[proposal,unrelated],consolidate=False)
        self.assertEqual({basis['event_id'] for basis in result[0].bases},{raw.id,proposal.id})
        self.assertNotIn('portraits',result[0].text)
        self.assertEqual(calls,['Construct thought','Verify thought']*2)


if __name__=='__main__':unittest.main()
