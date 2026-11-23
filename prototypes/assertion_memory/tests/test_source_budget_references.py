"""Synthetic tokenizer, extractor and verifier; no provider or deployment."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ..queue import CaptureQueue
from ..storage import MemoryStore
from ..thought_store import ThoughtStore
from ..thought_engine import ThoughtEngine
from ..thought_worker import ThoughtWorker
from ..turn_source.budget import TokenBudget, SourceBudgetExceeded
from ..turn_source.extractor import ContinuingSourceExtractor, SourceTurnConfig, SourceTurnFactory, SourceMCP
from ..turn_source.reader import SourceReadDenied
from ..turn_source.navigation import ClaimNavigation, OwnerScopeGrant
from ..turn_source.session_dialogue import SessionDialogue


class BudgetReferencesTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.memory=MemoryStore(Path(self.tmp.name)/'synthetic.sqlite');self.addCleanup(self.memory.close)
        self.queue=CaptureQueue(self.memory);self.thoughts=ThoughtStore(self.memory)
        self.requests=[]
        def verify(request):
            self.requests.append(request)
            return json.dumps({'verdict':'accept','text':'Synthetic approval; no semantic quality claim.'})
        self.engine=ThoughtEngine(self.thoughts,verify)

    def event(self,text,session='current',position=1):
        return self.queue.capture_event(session,str(position),text,source='fixture',scope='scope',position=position)

    def run_source(self,event,turn,*,budget=None,grant=None):
        with self.memory.transaction(write=True) as db:self.queue._scheduler_schema(db)
        factory=SourceTurnFactory._for_protocol_fixture(SourceTurnConfig((sys.executable,),enabled=True,
                                                                        budget=budget,owner_scope=grant))
        worker=ThoughtWorker(self.queue,self.engine,lane='source',claim_bound_source_extractor_factory=factory)
        with patch.object(ContinuingSourceExtractor,'_turn',turn):
            job=worker._source(worker._claim_source(event_id=event.id))
        return job

    @staticmethod
    def proposal(reader,refs=None,text='Synthetic extracted thought.'):
        refs=refs or [reader.primary_ref]
        reader.select(refs)
        return {'status':'proposal','text':text,'supports':refs}

    def test_old_threshold_full_message_and_tail(self):
        for size in (23999,24000,24001,70000):
            with self.subTest(size=size):
                event=self.event('a'*size+'🧩 END',session=str(size))
                shown=[]
                def turn(extractor,reader):
                    shown.append(reader.turn_text)
                    return self.proposal(reader)
                job=self.run_source(event,turn)
                self.assertEqual(job.status,'completed',job.error)
                self.assertEqual(shown,[event.prompt])
                saved=self.thoughts.get(job.extracted_ids[0])
                spans=[b['span'] for b in saved.bases]
                self.assertEqual(''.join(event.prompt[s['start']:s['end']] for s in spans),event.prompt)

    def test_chunks_cover_original_offsets_tail_and_second_pass(self):
        event=self.event(('ёж🧩\\"\n'*9000)+'FINAL QUALIFICATION')
        budget=TokenBudget(100000,len,'test-only:one-codepoint-per-token',4096,4096,4096)
        shown=[]
        def turn(extractor,reader):
            span=reader.turn_span
            self.assertEqual(reader.turn_text,event.prompt[span['start']:span['end']])
            shown.append((dict(span),reader.turn_text,list(reader.continuation)))
            return self.proposal(reader,text='Thought at '+str(span['start']))
        job=self.run_source(event,turn,budget=budget)
        self.assertEqual(job.status,'completed',job.error)
        count=len(shown)//2
        self.assertGreater(count,1)
        self.assertEqual(''.join(text for _,text,_ in shown[:count]),event.prompt)
        self.assertEqual(shown[count][2][-1]['global_span']['end'],len(event.prompt))
        self.assertEqual(len(self.requests),3*count)
        spans=sorted(b['span'].items() for identity in job.extracted_ids for b in self.thoughts.get(identity).bases)
        self.assertTrue(spans)

    def test_insufficient_budget_never_completes_even_with_canned_proposal(self):
        event=self.event('a'*30000)
        budget=TokenBudget(1000,len,'test-only:codepoints',100,100,100)
        job=self.run_source(event,lambda _,r:self.proposal(r),budget=budget)
        self.assertNotEqual(job.status,'completed')
        self.assertFalse(job.extracted_ids)
        self.assertTrue('budget' in job.error.lower() or 'windows' in job.error.lower())

    def test_history_budget_cannot_be_hidden_by_canned_answer(self):
        event=self.event('Current raw.')
        def turn(_,reader):
            reader.budget_exhausted=True
            return self.proposal(reader)
        job=self.run_source(event,turn)
        self.assertNotEqual(job.status,'completed')
        self.assertFalse(self.requests)

    def make_thought(self,event,text):
        return self.thoughts.publish(text,origin=self.engine._source_packet(event)[2],
            bases=[{'event_id':event.id}],metadata={'verification':'accepted'})

    def test_whole_thought_pins_shown_version_after_head_changes(self):
        earlier=self.event('Earlier exact raw.')
        thought=self.make_thought(earlier,'Shown thought. Second sentence.')
        current=self.event('Current source.',position=2)
        def turn(_,reader):
            nav=ClaimNavigation(reader,self.engine.retrieval)
            result=nav.call('semantic_search',{'query':'Shown thought'})
            ref=result['thoughts'][0]['ref']
            self.thoughts.publish('Changed thought must not replace shown content.',thought_id=thought.id,
                expected_version=1,origin=thought.origin,bases=thought.bases,metadata={'verification':'accepted'})
            return self.proposal(reader,[reader.primary_ref,ref])
        job=self.run_source(current,turn)
        self.assertEqual(job.status,'completed',job.error)
        saved=self.thoughts.get(job.extracted_ids[0])
        basis=next(b for b in saved.bases if 'thought_id' in b)
        self.assertEqual((basis['thought_id'],basis['version']),(thought.id,1))
        self.assertNotIn('span',basis)
        records=self.requests[-1].records
        shown=next(r for r in records if r.get('thought_id')==thought.id)
        self.assertEqual([f['text'] for f in shown['fragments']],[thought.text])
        self.assertNotIn('Changed thought',json.dumps(records))

    def test_foreign_exact_quotes_and_whole_thoughts_are_context_only(self):
        foreign=self.event('Foreign exact context 🧩.',session='foreign')
        thought=self.make_thought(foreign,'Foreign whole thought.')
        current=self.event('Explicitly referencing the other dialogue.')
        grant=OwnerScopeGrant(Path(self.memory.path),'scope')
        def turn(_,reader):
            nav=ClaimNavigation(reader,self.engine.retrieval,grant)
            dialogue=SessionDialogue(reader,grant)
            raw=dialogue.read({'session_id':'foreign'})['messages'][0]['ref']
            found=nav.call('semantic_search',{'query':'Foreign whole'})['thoughts'][0]['ref']
            sources=nav.call('thought_sources',{'ref':found})
            self.assertFalse(sources['sources'][0]['evidence_eligible'])
            return self.proposal(reader,[reader.primary_ref,raw,found])
        job=self.run_source(current,turn,grant=grant)
        self.assertEqual(job.status,'completed',job.error)
        saved=self.thoughts.get(job.extracted_ids[0])
        self.assertEqual({b.get('event_id') for b in saved.bases},{current.id})
        context=[r for r in self.requests[-1].records if r.get('role')=='external_dialogue_context']
        self.assertEqual({r['text'] for r in context},{foreign.prompt,thought.text})
        self.assertTrue(all('fragments' not in r for r in context))
        self.assertIn('not current RAW',self.requests[-1].instructions)

    def test_select_rejects_forged_refs_text_type_and_scope_substitution(self):
        current=self.event('Current source.')
        def turn(_,reader):
            service=SourceMCP(reader)
            for args in ({'refs':['forged']},{'refs':[reader.primary_ref],'text':'forged'},
                         {'refs':[reader.primary_ref],'kind':'thought'},
                         {'refs':[reader.primary_ref],'scope':'foreign'}):
                response=service.dispatch({'jsonrpc':'2.0','id':1,'method':'tools/call',
                    'params':{'name':'select_references','arguments':args}})
                self.assertIn('error',response)
            return self.proposal(reader)
        self.assertEqual(self.run_source(current,turn).status,'completed')

    def test_tokenizer_contract_and_lossless_unicode(self):
        budget=TokenBudget(100,len,'test-only:codepoints',1,1,1)
        text='🧩ёж\\"\n'*100
        chunks=list(budget.chunks(text,overhead=40))
        self.assertEqual(''.join(t for _,_,t in chunks),text)
        self.assertTrue(all(text[a:b]==t for a,b,t in chunks))
        from ..turn_source.budget import frame_fits
        self.assertFalse(frame_fits('\x00'*30000))
        with self.assertRaises(ValueError):TokenBudget(10,len,'',1,1,1)
        with self.assertRaises(SourceBudgetExceeded):budget.require('x'*100)

    def test_final_review_failure_publishes_no_chunk(self):
        event=self.event('a'*200000)
        budget=TokenBudget(100000,len,'test-only:codepoints')
        calls=[]
        def reject(request):
            calls.append(request)
            return json.dumps({'verdict':'accept' if len(calls)==1 else 'reject','text':'Synthetic rejection'})
        self.engine.model=reject
        job=self.run_source(event,lambda _,r:self.proposal(r),budget=budget)
        self.assertNotEqual(job.status,'completed')
        self.assertEqual(job.extracted_ids,[])
        self.assertEqual(self.thoughts.list_thoughts(),[])
        self.assertGreaterEqual(len(calls),2)

    def test_source_mutation_during_verification_is_not_published(self):
        event=self.event('Immutable source.')
        def corrupt(request):
            with self.memory.transaction(write=True) as db:
                db.execute('UPDATE memory_raw_events SET prompt=? WHERE id=?',('Changed bytes.',event.id))
            return json.dumps({'verdict':'accept','text':'Synthetic approval'})
        self.engine.model=corrupt
        job=self.run_source(event,lambda _,r:self.proposal(r))
        self.assertNotEqual(job.status,'completed')
        self.assertFalse(job.extracted_ids)

    def test_foreign_context_cannot_replace_primary(self):
        foreign=self.event('Foreign content.',session='foreign')
        event=self.event('Current content.')
        grant=OwnerScopeGrant(Path(self.memory.path),'scope')
        def turn(_,reader):
            ref=SessionDialogue(reader,grant).read({'session_id':'foreign'})['messages'][0]['ref']
            return self.proposal(reader,[ref])
        job=self.run_source(event,turn,grant=grant)
        self.assertNotEqual(job.status,'completed')
        self.assertFalse(self.requests)

    def test_visual_summary_projection_marks_truncation(self):
        from ..presentation_titles import model_projection
        projected=model_projection({'question':'Question','summary':'x'*7000,'summary_complete':True,'references':[]})
        self.assertFalse(projected['summary_complete'])
        self.assertEqual(len(projected['summary']),6000)

    def test_tool_budget_counts_complete_wire_response_and_blocks_completion(self):
        event=self.event('Current source.')
        def turn(_,reader):
            service=SourceMCP(reader)
            service.budget=TokenBudget(100,len,'test-only:codepoints',1,1,1)
            response=service.dispatch({'jsonrpc':'2.0','id':1,'method':'tools/call',
                'params':{'name':'select_references','arguments':{'refs':[reader.primary_ref]}}})
            self.assertEqual(response['result']['structuredContent']['status'],'source_token_budget_exhausted')
            self.assertTrue(reader.budget_exhausted)
            return self.proposal(reader)
        job=self.run_source(event,turn)
        self.assertNotEqual(job.status,'completed')
        self.assertFalse(self.requests)
