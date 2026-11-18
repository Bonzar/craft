"""Generic fictional source-proof acquisition; no providers or external sources."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import unittest

from prototypes.assertion_memory.capture import CaptureInput
from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import ModelError, format_prompt
from prototypes.assertion_memory.overview import _model_record
from prototypes.assertion_memory.overview_review import (RetrievalLedger, _requests, _request_identities,
    _make_writer_request, _make_review_request, _write, _review_text, review_overview, _review_contract)
from prototypes.assertion_memory.query_snapshot import _restore_frozen
from prototypes.assertion_memory.overview_projection import preflight_family
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore

STAMP='2026-02-01T12:00:00.000000+00:00'
BODY='Plan: a quiet arrival. Draft: The visitor entered. These plan and draft are the whole supplied scene. Unaccepted extra: a silver key exists.'


class OverviewSourceProofTest(unittest.TestCase):
    def setUp(self):
        self.store=MemoryStore(':memory:'); self.addCleanup(self.store.close)
        self.queue=CaptureQueue(self.store,clock=lambda:datetime.fromisoformat(STAMP))
        self.event=self.queue.capture_event('fiction','scene',BODY,scope='fiction',source='fixture://scene',actor='author')
        self.engine=MemoryEngine(self.store)
        self.rows={}
        for key,quote in [('plan','Plan: a quiet arrival.'),('draft','Draft: The visitor entered.'),
                          ('boundary','These plan and draft are the whole supplied scene.')]:
            start=BODY.index(quote)
            ref={'event_id':self.event.id,'start':start,'end':start+len(quote),'role':'user','relation':'statement'}
            metadata={'event_id':self.event.id,'excerpt':quote,'source_span':{'start':start,'end':start+len(quote)},
                'evidence_refs':[ref],'interpretation':{'status':'model_verified'}}
            row=self.queue.capture(CaptureInput(quote,'author','fixture://scene','fiction',assertion_id=key,
                scope='fiction',recorded_at=STAMP,metadata=metadata))
            self.rows[key]={**asdict(row),'id':key,'disposition':'assertion'}
        self.snapshot=self.engine.capture_snapshot(recorded_as_of=STAMP)
        self.spec=ViewSpec('scene-question','Scene','Question about supplied scene',scopes=('fiction',),root_ids=('plan','draft','boundary'))
        self.frame={'role':'query_context','question':self.spec.query,'scopes':['fiction'],'cutoffs':{}}

    def ledger(self,rows=None,snapshot=None):
        return RetrievalLedger(None,rows or self.rows,set(self.rows),
            source_resolver=(snapshot or self.snapshot).source_proof_resolver(self.engine))

    def acquire(self,ledger,ids=('draft',),gap='literal'):
        ledger.register([{'operation':'read','source_ids':list(ids),'gap_id':gap,'include_source_evidence':True}],'global')
        return ledger.execute()

    def bundle(self,ledger):
        return ledger.proof_for('global')

    def test_canonical_then_original_is_a_distinct_need_and_exact_body(self):
        ledger=self.ledger()
        ledger.register([{'operation':'read','source_ids':['draft'],'gap_id':'literal'}],'global'); ledger.execute()
        self.assertEqual(ledger.proof_for('global')['bodies'],[])
        receipts=self.acquire(ledger)
        self.assertEqual(len(ledger.obligations),2)
        self.assertTrue(receipts[0]['include_source_evidence'])
        bundle=self.bundle(ledger)
        self.assertEqual(bundle['bodies'][0]['text'],BODY)
        self.assertEqual(bundle['bodies'][0]['source_span'],{'start':0,'end':len(BODY)})
        self.assertEqual(bundle['bodies'][0]['source_length'],len(BODY))
        self.assertTrue(bundle['bodies'][0]['source_text_complete'])
        self.assertEqual(receipts[0]['proof_delivery_sha256'],bundle['delivery_sha256'])

    def test_same_event_body_reused_without_losing_per_anchor_meaning(self):
        ledger=self.ledger(); self.acquire(ledger,['draft']); before=self.bundle(ledger)
        self.acquire(ledger,['plan','boundary'],gap='boundary-check')
        bundle=self.bundle(ledger)
        self.assertEqual(len(bundle['bodies']),1)
        self.assertEqual({a['assertion_id'] for a in bundle['anchors']},set(self.rows))
        self.assertNotEqual(before['delivery_sha256'],bundle['delivery_sha256'])
        self.acquire(ledger,['draft'])
        self.assertEqual(bundle,self.bundle(ledger))
        self.assertEqual(len(ledger.receipts),2)

    def test_default_read_remains_canonical(self):
        ledger=self.ledger(); ledger.register([{'source_ids':['draft']}],'global'); ledger.execute()
        self.assertEqual(ledger.sources_for('global'),[self.rows['draft']])
        request=_make_writer_request(self.spec,[_model_record(self.rows['draft'])],[],self.frame,ledger,'global')
        self.assertFalse(any(row.get('role')=='source_evidence' for row in request.records))
        self.assertNotIn('evidence_refs',_model_record(self.rows['draft'])['metadata'])

    def test_missing_legacy_original_honestly_unavailable(self):
        rows={k:{**v,'metadata':{}} for k,v in self.rows.items()}
        ledger=self.ledger(rows); receipt=self.acquire(ledger)[0]
        self.assertEqual(receipt['status'],'unavailable')
        self.assertEqual(self.bundle(ledger)['bodies'],[])
        self.assertEqual(self.bundle(ledger)['unavailable'][0]['assertion_id'],'draft')
        self.assertIn('unavailable',self.bundle(ledger)['unavailable'][0]['reason'])

    def test_unknown_id_cannot_read_an_event(self):
        ledger=self.ledger(); self.acquire(ledger,[self.event.id])
        self.assertFalse(self.bundle(ledger)['bodies'])
        self.assertEqual(self.bundle(ledger)['unavailable'][0]['assertion_id'],self.event.id)

    def test_invalid_reference_identity_scope_role_bounds_or_quote_denied(self):
        for change in ({'event_id':'unknown'},{'start':-1},{'end':len(BODY)+1},{'start':True},
                       {'role':'assistant'},{'quote':'forged quote'}):
            with self.subTest(change=change):
                rows=json.loads(json.dumps(self.rows)); rows['draft']['metadata']['evidence_refs'][0].update(change)
                ledger=self.ledger(rows,replace(self.snapshot,assertions=rows)); self.acquire(ledger)
                self.assertFalse(self.bundle(ledger)['bodies'])
        rows=json.loads(json.dumps(self.rows)); rows['draft']['scope']='other'
        ledger=self.ledger(rows); self.acquire(ledger)
        self.assertFalse(self.bundle(ledger)['bodies'])

    def test_future_and_backdated_late_raw_event_never_leak(self):
        self.queue._clock=lambda:datetime.fromisoformat('2026-02-02T12:00:00+00:00')
        future=self.queue.capture_event('fiction','future','Future body',scope='fiction',source='fixture://scene')
        rows=json.loads(json.dumps(self.rows)); rows['draft']['metadata']['evidence_refs'][0]['event_id']=future.id
        ledger=self.ledger(rows); self.acquire(ledger)
        self.assertFalse(self.bundle(ledger)['bodies'])
        restored=_restore_frozen(self.engine,self.snapshot.descriptor())
        self.assertEqual(restored.raw_event_watermark,self.snapshot.raw_event_watermark)
        self.assertLess(self.snapshot.raw_event_watermark,2)

    def test_frozen_proof_hash_and_source_metadata_integrity(self):
        ledger=self.ledger(); self.acquire(ledger)
        proof=self.bundle(ledger)['bodies'][0]
        self.assertEqual(proof['body_sha256'],hashlib.sha256(BODY.encode()).hexdigest())
        self.assertEqual(proof['text_sha256'],hashlib.sha256(BODY.encode()).hexdigest())
        # Supplied hash bindings are checked, never trusted as delivery hashes.
        rows=json.loads(json.dumps(self.rows)); rows['draft']['metadata']['evidence_refs'][0]['source_sha256']='forged'
        ledger=self.ledger(rows); self.acquire(ledger)
        self.assertFalse(self.bundle(ledger)['bodies'])

    def test_default_and_resolver_construction_never_read_raw_corpus(self):
        statements=[]; self.store._connection.set_trace_callback(statements.append)
        self.addCleanup(lambda:self.store._connection.set_trace_callback(None))
        ledger=self.ledger(); ledger.register([{'source_ids':['draft']}],'global'); ledger.execute()
        self.assertFalse(any('memory_raw_events' in sql for sql in statements))
        self.acquire(ledger)
        reads=[sql for sql in statements if 'FROM memory_raw_events' in sql]
        self.assertEqual(len(reads),1)
        self.assertIn(self.event.id,reads[0])
        self.assertIn('rowid<=',reads[0].replace(' ',''))

    def test_large_source_is_exact_explicit_partial_never_complete_artifact(self):
        large='x'*7000+BODY
        event=self.queue.capture_event('fiction','large',large,scope='fiction',source='fixture://scene',actor='author')
        ref={'event_id':event.id,'start':7000,'end':7021,'role':'user','relation':'statement'}
        row=self.queue.capture(CaptureInput('Plan: a quiet arrival.','author','fixture://scene','fiction',assertion_id='large',
            scope='fiction',recorded_at=STAMP,metadata={'event_id':event.id,'evidence_refs':[ref]}))
        snapshot=self.engine.capture_snapshot(recorded_as_of=STAMP)
        ledger=self.ledger({'large':{**asdict(row),'id':'large'}},snapshot); self.acquire(ledger,['large'])
        proof=self.bundle(ledger)['bodies'][0]
        self.assertEqual(proof['text'],large[7000:7021])
        self.assertFalse(proof['source_text_complete'])
        self.assertEqual(proof['source_length'],len(large))
        self.assertEqual(proof['source_span'],{'start':7000,'end':7021})

    def test_schema_and_repair_identity_preserve_proof_representation(self):
        canonical={'operation':'read','source_ids':['draft'],'gap_id':'literal'}
        raw={**canonical,'include_source_evidence':True}
        self.assertNotEqual(_request_identities([canonical]),_request_identities([raw]))
        for request in ({**raw,'include_source_evidence':'yes'}, {'operation':'search','query':'scene','include_source_evidence':True}):
            with self.assertRaises(ModelError): _requests({'context_requests':[request]},'context_requests',2)

    def test_proof_is_visible_in_writer_and_reviewer_but_not_positive_facts(self):
        ledger=self.ledger(); self.acquire(ledger,['plan','draft','boundary'])
        rows=[_model_record(v) for v in self.rows.values()]
        for request in (_make_writer_request(self.spec,rows,[],self.frame,ledger,'global'),
                        _make_review_request(rows,[],'draft',self.frame,ledger,'global')):
            bundle=next(r for r in request.records if r.get('role')=='source_evidence')
            self.assertEqual(bundle['bodies'][0]['text'],BODY)
            self.assertIn('unaccepted positive',request.instructions)
            self.assertIn('SourceReprocess',request.instructions)
            self.assertIn('complete artifact',request.instructions)
            self.assertIn('independent',request.instructions)

    def test_preflight_reserves_proof_in_entire_family_and_never_truncates(self):
        ledger=self.ledger(); self.acquire(ledger)
        rows=[_model_record(v) for v in self.rows.values()]
        # A very large question plus proof must fail before ANY provider invocation.
        spec=replace(self.spec,query='q'*59000)
        calls=[]; engine=MemoryEngine(self.store,model=lambda r:calls.append(r) or 'answer')
        with self.assertRaises(ModelError): _write(engine,spec,rows,[],self.frame,ledger,'global')
        self.assertEqual(calls,[])
        self.assertEqual(self.bundle(ledger)['bodies'][0]['text'],BODY)

    def test_source_recording_clock_and_actor_stay_provenance(self):
        ledger=self.ledger(); self.acquire(ledger)
        anchor=self.bundle(ledger)['anchors'][0]
        self.assertEqual(anchor['source']['recorded_at'],STAMP)
        self.assertEqual(anchor['source']['actor'],'author')
        self.assertEqual(anchor['source']['source'],'fixture://scene')
        self.assertEqual(anchor['reference']['role'],'user')

    def test_proof_delivery_changes_cache_context_with_same_ids(self):
        ledger=self.ledger(); rows=[_model_record(self.rows['draft'])]
        before=_make_writer_request(self.spec,rows,[],self.frame,ledger,'global')
        self.acquire(ledger)
        after=_make_writer_request(self.spec,rows,[],self.frame,ledger,'global')
        self.assertNotEqual(format_prompt(before),format_prompt(after))
        self.assertNotEqual(ledger.proof_identity('global'),RetrievalLedger(None,self.rows,set(self.rows)).proof_identity('global'))

    def test_new_anchor_read_reuses_actual_delivered_whole_body_without_acquisition(self):
        ledger=self.ledger(); self.acquire(ledger,['draft'])
        ledger.register([{'operation':'read','source_ids':['plan','boundary'],'gap_id':'later-meaning',
            'include_source_evidence':True}],'global')
        self.assertEqual(ledger.pending(),[])
        self.assertEqual(ledger.execute(),[])
        receipt=ledger.receipts[-1]
        self.assertTrue(receipt['proof_reused'])
        self.assertEqual(receipt['requested_ids'],['plan','boundary'])
        self.assertEqual(len(self.bundle(ledger)['bodies']),1)
        self.assertEqual({r['assertion_id'] for r in self.bundle(ledger)['anchors']},set(self.rows))

    def test_origin_binding_cannot_be_replaced_by_an_unrelated_same_scope_source(self):
        other=self.queue.capture_event('fiction','unrelated',BODY,scope='fiction',source='fixture://scene',actor='author')
        rows=json.loads(json.dumps(self.rows)); rows['draft']['metadata']['evidence_refs'][0]['event_id']=other.id
        snapshot=replace(self.engine.capture_snapshot(recorded_as_of=STAMP),assertions=rows)
        ledger=self.ledger(rows,snapshot); self.acquire(ledger)
        self.assertFalse(self.bundle(ledger)['bodies'])

    def test_contract_repair_cannot_downgrade_original_read_to_canonical(self):
        ledger=self.ledger(); request=_make_review_request([_model_record(self.rows['draft'])],[],
            'The visitor enters. [source:draft]',self.frame,ledger,'global')
        intent={'operation':'read','source_ids':['draft'],'gap_id':'literal','include_source_evidence':True}
        original={'status':'revise','coverage':'invalid','reason':'Need literal text.','issues':[],
            'context_requests':[intent],'unknown_needs':[]}
        fixed={**original,'coverage':'partial','context_requests':[{**intent,'include_source_evidence':False}]}
        responses=iter([original,fixed])
        with self.assertRaisesRegex(ModelError,'retrieval intent'):
            _review_contract(lambda r:json.dumps(next(responses)),request,visible_ids=set(self.rows))

    def test_proof_overhead_derives_smaller_output_and_irreducible_proof_still_fails(self):
        canonical=self.ledger(); proof=self.ledger(); self.acquire(proof,['plan','draft','boundary'])
        rows=[_model_record(v) for v in self.rows.values()]
        writer=_make_writer_request(self.spec,rows,[],self.frame,canonical,'global')
        reviewer=_make_review_request(rows,[],'',self.frame,canonical,'global')
        sizes=preflight_family(writer,reviewer)
        # Fill a shared frame to within a small exact margin of the largest
        # family member. Proof overhead, rather than question size alone,
        # must push the proof version over the existing 60k bound.
        frame={**self.frame,'fixture_padding':'x'*(60000-max(sizes)-100)}
        preflight_family(_make_writer_request(self.spec,rows,[],frame,canonical,'global'),
            _make_review_request(rows,[],'',frame,canonical,'global'))
        from prototypes.assertion_memory.overview_review import _plan_writer_request, _review_text
        planned,bound=_plan_writer_request(self.spec,rows,[],frame,proof,'global')
        self.assertGreater(bound,0)
        self.assertLess(bound,8000)
        full_proof=self.bundle(proof)
        self.assertIn(full_proof,planned.records)
        calls=[]
        def provider(request):
            calls.append(request)
            self.assertLessEqual(len(format_prompt(request)),60000)
            self.assertIn(full_proof,request.records)
            if request.task=='Review grounded memory overview':
                return json.dumps({'status':'revise','coverage':'partial','reason':'Missing boundary.',
                    'issues':['Preserve original boundary.'],'context_requests':[],'unknown_needs':[]})
            return 'Reported visitor enters.'
        engine=MemoryEngine(self.store,model=provider)
        text,_=_write(engine,self.spec,rows,[],frame,proof,'global')
        verdict=_review_text(engine,self.spec,rows,[],text,frame,proof,'global')
        self.assertEqual(verdict['status'],'revise')
        self.assertEqual(len(calls),2)
        # Full proof cannot be discarded when even a one-character output fails.
        frame={**frame,'fixture_padding':frame['fixture_padding']+'x'*8000}
        calls.clear()
        with self.assertRaises(ModelError) as caught: _write(engine,self.spec,rows,[],frame,proof,'global')
        self.assertEqual(caught.exception.diagnostic['limit'],60000)
        self.assertEqual(caught.exception.diagnostic['max_draft'],1)
        self.assertIn('source_evidence',caught.exception.diagnostic['components']['review_contract_repair_reserved'])
        self.assertEqual(calls,[])
        self.assertEqual(self.bundle(proof)['bodies'][0]['text'],BODY)

    def test_raw_unaccepted_positive_claim_never_bypasses_whole_answer_review(self):
        reviews=0
        def provider(request):
            nonlocal reviews
            if request.task.startswith('Review'):
                reviews+=1
                self.assertIn('unaccepted positive',request.instructions)
                return json.dumps({'status':'revise','coverage':'partial','reason':'Unaccepted positive claim needs canonical processing.',
                    'issues':['Remove unsupported positive.'], 'context_requests':([{'operation':'read','source_ids':['draft'],
                    'gap_id':'original','include_source_evidence':True}] if reviews==1 else []),'unknown_needs':[]})
            return 'A silver key exists. [source:draft]'
        with self.assertRaises(ModelError) as caught: self.run_review(provider)
        self.assertEqual(reviews,3)
        self.assertEqual(caught.exception.acquisition['review']['status'],'unresolved')
        self.assertEqual(caught.exception.acquisition['review']['source_evidence']['bodies'][0]['text'],BODY)

    def test_proof_preserves_optional_source_message_clock_and_speaker_calendar(self):
        metadata={'source_message_at':'2026-01-31T10:00:00+03:00',
            'source_message_clock_basis':{'kind':'producer','field':'sent_at','value':'2026-01-31T10:00:00+03:00',
                'source':'fixture://dated'},'timezone':'Europe/Moscow'}
        event=self.queue.capture_event('fiction','dated','A dated message.',scope='fiction',source='fixture://dated',actor='author',metadata=metadata)
        ref={'event_id':event.id,'start':0,'end':len(event.prompt),'role':'user','relation':'statement'}
        row=self.queue.capture(CaptureInput('A dated message.','author','fixture://dated','fiction',assertion_id='dated',
            scope='fiction',recorded_at=STAMP,metadata={'event_id':event.id,'evidence_refs':[ref]}))
        snapshot=self.engine.capture_snapshot(recorded_as_of=STAMP)
        ledger=self.ledger({'dated':{**asdict(row),'id':'dated'}},snapshot); self.acquire(ledger,['dated'])
        source=self.bundle(ledger)['anchors'][0]['source']
        self.assertEqual(source['source_message_at'],'2026-01-31T07:00:00.000000+00:00')
        self.assertEqual(source['timezone'],'Europe/Moscow')
        self.assertEqual(source['base_available_at'],STAMP)

    def test_unread_span_in_same_large_event_never_inherits_delivered_partial_clearance(self):
        text='x'*7000+BODY
        event=self.queue.capture_event('fiction','partial-body',text,scope='fiction',source='fixture://partial',actor='author')
        rows={}
        for key,quote in [('large-plan','Plan: a quiet arrival.'),('large-draft','Draft: The visitor entered.')]:
            start=text.index(quote)
            ref={'event_id':event.id,'start':start,'end':start+len(quote),'role':'user','relation':'statement'}
            row=self.queue.capture(CaptureInput(quote,'author','fixture://partial','fiction',assertion_id=key,scope='fiction',
                recorded_at=STAMP,metadata={'event_id':event.id,'evidence_refs':[ref]}))
            rows[key]={**asdict(row),'id':key}
        ledger=self.ledger(rows,self.engine.capture_snapshot(recorded_as_of=STAMP))
        self.acquire(ledger,['large-plan'])
        ledger.register([{'operation':'read','source_ids':['large-draft'],'gap_id':'unread-span',
            'include_source_evidence':True}],'global')
        self.assertEqual([r['gap_id'] for r in ledger.pending()],['unread-span'])
        self.assertEqual(len(self.bundle(ledger)['bodies']),1)
        ledger.execute()
        self.assertEqual(len(self.bundle(ledger)['bodies']),2)
        self.assertTrue(all(not row['source_text_complete'] for row in self.bundle(ledger)['bodies']))

    def run_review(self,provider):
        engine=MemoryEngine(self.store,model=provider,verify_overviews=True)
        return review_overview(engine,self.spec,list(self.rows.values()),[],[],self.snapshot.assertions,[],snapshot=self.snapshot)

    def test_existing_visible_canonical_request_acquires_original_before_independent_acceptance(self):
        calls=[]
        def provider(request):
            calls.append(request)
            proof=next((r for r in request.records if r.get('role')=='source_evidence'),None)
            if request.task.startswith('Review'):
                if proof is None:
                    return json.dumps({'status':'revise','coverage':'partial','reason':'Literal text needed.',
                        'issues':[], 'context_requests':[{'operation':'read','source_ids':['draft','boundary'],
                        'gap_id':'literal','include_source_evidence':True}], 'unknown_needs':[]})
                self.assertEqual(proof['bodies'][0]['text'],BODY)
                self.assertEqual(len(proof['bodies']),1)
                self.assertNotIn('silver key',next(r['text'] for r in request.records if r.get('role')=='overview_draft'))
                return json.dumps({'status':'accepted','coverage':'all_claims','reason':'Scoped fictional review.',
                    'issues':[],'unknown_needs':[]})
            return 'In the supplied scene the visitor enters. [source:draft]'
        text,_,_,audit=self.run_review(provider)
        self.assertEqual(len(calls),4)
        self.assertEqual(audit['rounds'],2)
        self.assertEqual(audit['obligations'][0]['include_source_evidence'],True)
        self.assertEqual(audit['source_evidence']['bodies'][0]['text'],BODY)
        self.assertNotIn('silver key',text)

    def test_new_proof_request_after_final_round_is_unresolved_and_preserved(self):
        reviews=0; calls=[]
        def provider(request):
            nonlocal reviews
            calls.append(request)
            if request.task.startswith('Review'):
                reviews+=1
                return json.dumps({'status':'revise','coverage':'partial','reason':'Independent revision still needed.',
                    'issues':[], 'context_requests':([] if reviews<3 else [{'operation':'read','source_ids':['draft'],
                    'gap_id':'late-literal','include_source_evidence':True}]),'unknown_needs':[]})
            return 'The visitor enters. [source:draft]'
        with self.assertRaises(ModelError) as caught: self.run_review(provider)
        audit=caught.exception.acquisition['review']
        self.assertEqual(len(calls),6)
        self.assertEqual(audit['acquisition_budget']['rounds_started'],3)
        self.assertEqual(audit['remaining_obligations'][0]['include_source_evidence'],True)
        self.assertEqual(audit['remaining_obligations'][0]['attempts'],0)
        self.assertTrue(audit['resume_required'])

    def test_known_second_round_proof_request_is_consumed_before_third_review(self):
        reviews=0
        def provider(request):
            nonlocal reviews
            proof=next((r for r in request.records if r.get('role')=='source_evidence'),None)
            if request.task.startswith('Review'):
                reviews+=1
                accepted=reviews==3
                if accepted: self.assertEqual(proof['bodies'][0]['text'],BODY)
                return json.dumps({'status':'accepted' if accepted else 'revise',
                    'coverage':'all_claims' if accepted else 'partial','reason':'Independent review.',
                    'issues':[], 'context_requests':([{'operation':'read','source_ids':['draft'],
                    'gap_id':'known-literal','include_source_evidence':True}] if reviews==2 else []),'unknown_needs':[]})
            return 'The visitor enters. [source:draft]'
        _,_,_,audit=self.run_review(provider)
        self.assertEqual(reviews,3)
        self.assertEqual(audit['obligations'][0]['attempts'],1)
        self.assertEqual(audit['consumed_gaps'],['known-literal'])

    def test_recorded_cutoff_denies_preexisting_future_raw_and_late_backdated_raw(self):
        self.queue._clock=lambda:datetime.fromisoformat('2026-02-02T12:00:00+00:00')
        future=self.queue.capture_event('fiction','preexisting-future','Future evidence.',scope='fiction',source='fixture://future')
        # A canonical reference with an older recorded timestamp cannot override
        # the raw source's recorded cutoff, even when its row is under watermark.
        ref={'event_id':future.id,'start':0,'end':len(future.prompt),'role':'user','relation':'statement'}
        row=self.queue.capture(CaptureInput('Future evidence.','user','fixture://future','fiction',assertion_id='future-anchor',
            scope='fiction',recorded_at=STAMP,metadata={'event_id':future.id,'evidence_refs':[ref]}))
        snapshot=self.engine.capture_snapshot(recorded_as_of=STAMP)
        ledger=self.ledger({'future-anchor':{**asdict(row),'id':'future-anchor'}},snapshot)
        self.acquire(ledger,['future-anchor'])
        self.assertFalse(self.bundle(ledger)['bodies'])
        self.queue._clock=lambda:datetime.fromisoformat(STAMP)
        late=self.queue.capture_event('fiction','late','Late evidence.',scope='fiction',source='fixture://late')
        self.assertGreater(self.store._connection.execute('SELECT rowid FROM memory_raw_events WHERE id=?',(late.id,)).fetchone()[0],snapshot.raw_event_watermark)
        # Exercise the resolver boundary directly, retaining exact frozen anchor
        # metadata but pointing at a late backdated source in a synthetic snapshot.
        updated={**asdict(row),'id':'future-anchor','metadata':{'event_id':late.id,'evidence_refs':[
            {'event_id':late.id,'start':0,'end':len(late.prompt),'role':'user','relation':'statement'}]}}
        snapshot=replace(snapshot,assertions={updated['id']:updated})
        ledger=self.ledger({updated['id']:updated},snapshot); self.acquire(ledger,[updated['id']])
        self.assertFalse(self.bundle(ledger)['bodies'])

    def test_bad_original_body_hash_role_and_cross_scope_denied_even_if_stored_in_canon(self):
        for change in ({'source_sha256':'forged'},{'role':'assistant'}):
            with self.subTest(change=change):
                rows=json.loads(json.dumps(self.rows)); rows['draft']['metadata']['evidence_refs'][0].update(change)
                snapshot=replace(self.snapshot,assertions=rows)
                ledger=self.ledger(rows,snapshot); self.acquire(ledger)
                self.assertFalse(self.bundle(ledger)['bodies'])
        event=self.queue.capture_event('other','foreign',BODY,scope='foreign',source='fixture://scene',actor='author')
        rows=json.loads(json.dumps(self.rows)); rows['draft']['metadata']['evidence_refs'][0]['event_id']=event.id
        rows['draft']['metadata']['event_id']=event.id
        snapshot=replace(self.engine.capture_snapshot(recorded_as_of=STAMP),assertions=rows)
        ledger=self.ledger(rows,snapshot); self.acquire(ledger)
        self.assertFalse(self.bundle(ledger)['bodies'])



if __name__=='__main__': unittest.main()
