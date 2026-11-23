"""Inference receives context; diagnostics retain only content-free audit metadata."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from . import test_source_budget_references as fixtures
from ..thought_trace import ThoughtTrace
from ..turn_source.navigation import ClaimNavigation, OwnerScopeGrant
from ..turn_source.session_dialogue import SessionDialogue
from ..turn_source.budget import TokenBudget


class SourceContextPrivacyTest(unittest.TestCase):
    setUp=fixtures.BudgetReferencesTest.setUp
    event=fixtures.BudgetReferencesTest.event
    run_source=fixtures.BudgetReferencesTest.run_source
    make_thought=fixtures.BudgetReferencesTest.make_thought
    proposal=staticmethod(fixtures.BudgetReferencesTest.proposal)

    def diagnostics(self,trace,events):
        content=[json.dumps(events),json.dumps(trace.events()),json.dumps(trace.calls())]
        for call in trace.calls():
            for key in ('request_hash','response_hash'):
                if call.get(key):content.append(trace.payload(call[key]))
        with self.memory.transaction() as db:
            for table in ('memory_capture_jobs','memory_source_draft_checkpoints',
                          'memory_refresh_generations','memory_thought_generations',
                          'memory_thought_draft_cycles'):
                if db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():
                    content.append(json.dumps([dict(row) for row in db.execute('SELECT * FROM '+table)]))
        return '\n'.join(content)

    def test_rejection_and_provider_errors_do_not_escape_to_trace_or_queue(self):
        # Both exact quotations and novel paraphrases are removed, not substring-masked.
        for mode in ('reject','invalid','exception','provider_event','checkpoint'):
            with self.subTest(mode=mode):
                secret='SECRET-FOREIGN-'+mode+'-the-vault-is-blue'
                paraphrase='PARAPHRASED-'+mode+'-azure-storage-location'
                foreign=self.event(secret,session='foreign-'+mode)
                thought=self.make_thought(foreign,secret+' whole thought')
                primary=self.event('Current explicit reference.',session='primary-'+mode)
                grant=OwnerScopeGrant(Path(self.memory.path),'scope')
                trace=ThoughtTrace(self.memory,'source',{'event_id':primary.id})
                events=[];calls=[]
                def provider(request):
                    calls.append(request)
                    wire=json.dumps(request.records)
                    self.assertIn(secret,wire) # Full authorized inference context survives.
                    if mode=='exception':raise ValueError(paraphrase+' '+secret)
                    if mode=='checkpoint':
                        from ..thought_engine import SourceDraftRepairPending
                        raise SourceDraftRepairPending(secret,[],[{'reason':paraphrase}])
                    if mode=='invalid':return json.dumps({'unknown':secret+' '+paraphrase})
                    if mode=='provider_event' and len(calls)==1:
                        # Direct trace entry points must apply the same restriction.
                        trace.record('synthetic_provider_event',details={'reason':paraphrase})
                    return json.dumps({'verdict':'reject','text':secret+' '+paraphrase})
                self.engine.gateway=lambda task,request,bindings:trace.call(provider,request,bindings,task)
                def emit(event):
                    events.append(event)
                    trace.record(event['stage'],details=event)
                self.engine.trace=emit
                readers=[]
                def turn(_,reader):
                    readers.append(reader)
                    nav=ClaimNavigation(reader,self.engine.retrieval,grant)
                    raw=SessionDialogue(reader,grant).read({'session_id':'foreign-'+mode})['messages'][0]['ref']
                    ref=reader.offer_thought(thought,local=False)
                    return self.proposal(reader,[reader.primary_ref,raw,ref])
                job=self.run_source(primary,turn,grant=grant)
                self.assertNotEqual(job.status,'completed')
                self.assertFalse(job.extracted_ids)
                self.assertTrue(all(r._anchor is None for r in readers))
                self.assertTrue(calls)
                diagnostics=self.diagnostics(trace,events)
                for marker in (secret,paraphrase):self.assertNotIn(marker,diagnostics)
                self.assertIn('redacted',job.error)
                self.assertTrue(any(e.get('redacted') for e in events if e['stage']=='model_input'))
                self.assertTrue(all(json.loads(trace.payload(c['request_hash']))['redacted'] for c in trace.calls()))
                self.assertTrue(all(c['status'] in ('failed','succeeded') for c in trace.calls()))
                if mode in ('reject','provider_event'):
                    self.assertEqual(trace.calls()[-1]['semantic_verdict'],'reject')
                self.engine.gateway=None;self.engine.trace=None

    def test_local_whole_thought_keeps_semantics_and_existing_trace(self):
        prior=self.event('Local original.',position=1)
        thought=self.make_thought(prior,'Local whole thought.')
        primary=self.event('Current source.',position=2)
        events=[];self.engine.trace=events.append
        def turn(_,reader):
            ClaimNavigation(reader,self.engine.retrieval)
            ref=reader.offer_thought(thought,local=True)
            return self.proposal(reader,[reader.primary_ref,ref])
        job=self.run_source(primary,turn)
        self.assertEqual(job.status,'completed',job.error)
        self.assertIn('Local whole thought.',json.dumps(events))
        saved=self.thoughts.get(job.extracted_ids[0])
        self.assertEqual(next(b['version'] for b in saved.bases if 'thought_id' in b),1)

    def test_duplicate_windows_merge_all_bases_and_snapshot_closes(self):
        primary=self.event('a'*200000)
        budget=TokenBudget(100000,len,'synthetic:codepoints')
        refs=[];spans=[]
        def turn(_,reader):
            self.assertIsNone(reader._anchor)
            self.assertIsNone(reader._read_db.get())
            refs.append(reader);spans.append(dict(reader.turn_span))
            return self.proposal(reader,text='Exactly the same thought.')
        with patch.object(self.queue,'renew_claim',wraps=self.queue.renew_claim) as renew:
            job=self.run_source(primary,turn,budget=budget)
        self.assertEqual(job.status,'completed',job.error)
        self.assertGreater(len(spans),2)
        self.assertEqual(len(job.extracted_ids),1)
        saved=self.thoughts.get(job.extracted_ids[0])
        spans=sorted((b['span']['start'],b['span']['end']) for b in saved.bases)
        self.assertEqual(spans[0][0],0);self.assertEqual(spans[-1][1],len(primary.prompt))
        self.assertTrue(all(a[1]==b[0] for a,b in zip(spans,spans[1:])))
        self.assertGreater(renew.call_count,1)
        self.assertTrue(all(reader._anchor is None for reader in refs))

    def test_redacted_payload_cannot_alias_preexisting_plaintext(self):
        from ..diagnostic_privacy import diagnostic_scope
        trace=ThoughtTrace(self.memory,'fixture')
        secret='PREEXISTING-SECRET-PAYLOAD'
        with self.memory.transaction(write=True) as db:
            original=trace._payload(db,secret)
        with diagnostic_scope() as scope:
            scope.restricted=True
            with self.memory.transaction(write=True) as db:
                protected=trace._payload(db,secret)
        self.assertNotEqual(original,protected)
        self.assertEqual(trace.payload(original),secret) # No unsolicited historical rewrite.
        self.assertNotIn(secret,trace.payload(protected))
        self.assertEqual(json.loads(trace.payload(protected))['original_sha256'],original)

    def test_traced_worker_does_not_reemit_prior_error_or_rejection(self):
        import sys
        from ..thought_processing import TracedThoughtWorker
        from ..turn_source.extractor import SourceTurnConfig, SourceTurnFactory, ContinuingSourceExtractor
        secret='SECRET-TRACED-WORKER'
        foreign=self.event(secret,session='foreign')
        primary=self.event('Current explicit reference.')
        grant=OwnerScopeGrant(Path(self.memory.path),'scope')
        with self.memory.transaction(write=True) as db:
            self.queue._scheduler_schema(db)
            db.execute('UPDATE memory_capture_jobs SET error=? WHERE event_id=?',(secret,primary.id))
        def provider(request):
            self.assertIn(secret,json.dumps(request.records))
            return json.dumps({'verdict':'reject','text':secret+' paraphrased private reason'})
        self.engine.model=provider
        factory=SourceTurnFactory._for_protocol_fixture(SourceTurnConfig((sys.executable,),enabled=True,owner_scope=grant))
        worker=TracedThoughtWorker(self.queue,self.engine,lane='source',claim_bound_source_extractor_factory=factory)
        def turn(_,reader):
            ref=SessionDialogue(reader,grant).read({'session_id':'foreign'})['messages'][0]['ref']
            return self.proposal(reader,[reader.primary_ref,ref])
        with patch.object(ContinuingSourceExtractor,'_turn',turn):
            job=worker._source(worker._claim_source(event_id=primary.id))
        self.assertNotEqual(job.status,'completed')
        with self.memory.transaction() as db:
            runs=[dict(row) for row in db.execute('SELECT * FROM memory_thought_runs')]
        self.assertNotIn(secret,json.dumps(runs))
        trace=object.__new__(ThoughtTrace);trace.store=self.memory;trace.id=runs[-1]['id']
        self.assertNotIn(secret,self.diagnostics(trace,[]))
        self.assertNotIn('paraphrased private reason',self.diagnostics(trace,[]))
