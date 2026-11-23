"""Synthetic full-path regression: page sizes are not total-volume ceilings."""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from . import test_source_budget_references as helpers
from ..turn_source.budget import TokenBudget
from ..turn_source.extractor import SourceTurnConfig, SourceTurnFactory
from ..turn_source.navigation import ClaimNavigation, OwnerScopeGrant
from ..turn_source.session_dialogue import SessionDialogue
from ..turn_source.transport import StdioTransport, encode, MAX_FRAME, MAX_PENDING_BYTES
from ..turn_source.reader import SourceReadDenied
from ..thought_worker import ThoughtWorker


class UnboundedSourceTest(unittest.TestCase):
    setUp=helpers.BudgetReferencesTest.setUp
    event=helpers.BudgetReferencesTest.event
    run_source=helpers.BudgetReferencesTest.run_source
    proposal=staticmethod(helpers.BudgetReferencesTest.proposal)
    make_thought=helpers.BudgetReferencesTest.make_thought

    def test_history_over_old_pages_bytes_refs_in_both_dialogues(self):
        for session in ('current','foreign'):
            for index in range(1,141):
                self.event(str(index)+':'+'x'*2500,session=session,position=index)
        event=self.event('Primary.',position=141)
        grant=OwnerScopeGrant(Path(self.memory.path),'scope')
        def turn(_,reader):
            for session in ('current','foreign'):
                dialogue=SessionDialogue(reader,grant)
                args={'session_id':session};items=[];pages=0
                while True:
                    result=dialogue.read(args);pages+=1;items.extend(result['messages'])
                    if result['next_cursor'] is None:break
                    args={'cursor':result['next_cursor']}
                self.assertEqual(result['history_status'],'history_end')
                self.assertGreater(pages,16)
                self.assertGreater(len(items),128)
                self.assertEqual(''.join(r['text'] for r in items),''.join(str(i)+':'+'x'*2500 for i in range(1,141)))
                self.assertTrue(all(r['evidence_eligible']==(session=='current') for r in items))
                for item in items:reader.evidence([item['ref']])
            self.assertGreater(reader.bytes_read,512000)
            self.assertFalse(reader.budget_exhausted)
            return self.proposal(reader)
        job=self.run_source(event,turn,grant=grant)
        self.assertEqual(job.status,'completed',job.error)

    def test_older_cursor_over_old_total_limits(self):
        source='🧩'*400000+'EXACT TAIL'
        self.event(source)
        event=self.event('Primary.',position=2)
        def turn(_,reader):
            items=[];cursor=None
            while True:
                result=reader.older(cursor);items[:0]=result['messages']
                cursor=result['next_cursor']
                if cursor is None:break
            self.assertGreater(reader.pages,16)
            self.assertGreater(reader.bytes_read,256000)
            self.assertEqual(''.join(r['text'] for r in items),source)
            return self.proposal(reader)
        job=self.run_source(event,turn)
        self.assertEqual(job.status,'completed',job.error)

    def test_search_all_candidates_and_sources_paginate_without_total_quotas(self):
        raw=self.event('x'*300000+'TAIL')
        thoughts=[self.make_thought(raw,'Thought '+str(i)+'. '+'x'*1500) for i in range(75)]
        event=self.event('Primary.',position=2)
        class Ranker:
            def rank_entries(_,query,entries,limit):
                self.assertEqual(len(entries),75)  # includes candidates beyond old 64/64k
                return [SimpleNamespace(id=k) for k in sorted(entries)][:limit],{}
        def turn(_,reader):
            nav=ClaimNavigation(reader,Ranker())
            page=nav.call('semantic_search',{'query':'thought '*200,'limit':5});items=[]
            while True:
                items.extend(page['thoughts'])
                token=page['next_cursor']
                if token is None:break
                with self.assertRaises(SourceReadDenied):nav.call('thought_sources',{'cursor':token})
                page=nav.call('semantic_search',{'cursor':token})
                with self.assertRaises(SourceReadDenied):nav.call('semantic_search',{'cursor':token})
            self.assertEqual({r['thought_id'] for r in items},{t.id for t in thoughts})
            self.assertGreater(nav.calls,8);self.assertGreater(nav.sent,64000)
            sources=[];page=nav.call('thought_sources',{'ref':items[0]['ref']})
            while True:
                sources.extend(page['sources'])
                if page['next_cursor'] is None:break
                page=nav.call('thought_sources',{'cursor':page['next_cursor']})
            self.assertEqual(''.join(s['text'] for s in sources),raw.prompt)
            for item in sources:
                span=item['span'];self.assertEqual(item['text'],raw.prompt[span['start']:span['end']])
                reader.evidence([item['ref']])
            self.assertFalse(reader.budget_exhausted)
            return self.proposal(reader)
        job=self.run_source(event,turn)
        self.assertEqual(job.status,'completed',job.error)

    def test_over_64_real_frame_windows_exact_tail_atomic_dedup(self):
        event=self.event('x'*8_600_000+'END🧩')
        budget=TokenBudget(2_000_000,len,'synthetic-codepoint-counter')
        spans=[]
        def turn(_,reader):
            span=reader.turn_span;spans.append((span['start'],span['end']))
            self.assertEqual(reader.turn_text,event.prompt[span['start']:span['end']])
            self.assertFalse(self.thoughts.list_thoughts())
            return self.proposal(reader)
        job=self.run_source(event,turn,budget=budget)
        self.assertEqual(job.status,'completed',job.error)
        count=len(spans)//2
        self.assertGreater(count,64)
        self.assertEqual(spans[:count],spans[count:])
        self.assertEqual(spans[0][0],0);self.assertEqual(spans[-1][1],len(event.prompt))
        self.assertTrue(all(a[1]==b[0] for a,b in zip(spans[:count],spans[1:count])))
        self.assertEqual(len(job.extracted_ids),1)
        self.assertEqual(len(self.thoughts.get(job.extracted_ids[0]).bases),count)

    def test_more_than_three_failures_keep_backoff_then_complete(self):
        event=self.event('Primary.')
        for attempt in range(1,6):
            job=self.run_source(event,lambda _,r:{'status':'unresolved','text':None,'supports':[]})
            self.assertEqual((job.status,job.attempts),('pending',attempt))
            with self.memory.transaction(write=True) as db:
                row=db.execute('SELECT available_at,updated_at FROM memory_capture_jobs WHERE id=?',(job.id,)).fetchone()
                self.assertGreater(row[0],row[1])
                db.execute("UPDATE memory_capture_jobs SET available_at='2000-01-01' WHERE id=?",(job.id,))
        job=self.run_source(event,lambda _,r:self.proposal(r))
        self.assertEqual((job.status,job.attempts),('completed',6),job.error)

    def test_expired_claim_after_old_retry_limit_can_be_reclaimed(self):
        event=self.event('Primary.')
        factory=SourceTurnFactory._for_protocol_fixture(SourceTurnConfig((sys.executable,),enabled=True))
        worker=ThoughtWorker(self.queue,self.engine,lane='source',claim_bound_source_extractor_factory=factory)
        with self.memory.transaction(write=True) as db:
            self.queue._scheduler_schema(db)
            db.execute("UPDATE memory_capture_jobs SET status='running',attempts=9,lease_until='2000-01-01' WHERE event_id=?",(event.id,))
        claim=worker._claim_source(event_id=event.id)
        self.assertIsNotNone(claim);self.assertEqual(claim.job.attempts,10)

    def test_stdio_has_no_implicit_deadline_or_lifetime_traffic_ceiling(self):
        self.assertIsNone(SourceTurnConfig((sys.executable,),enabled=True).timeout)
        for timeout in (None,301):
            StdioTransport((sys.executable,),allow_spawn=True,timeout=timeout)
        wire=StdioTransport((sys.executable,),allow_spawn=True)
        wire.counter=2048;wire.received_bytes=8_388_608
        wire.incoming.extend(encode({'method':'fixture'}))
        self.assertEqual(wire._receive(),{'method':'fixture'})
        # Real pipe reads cross both old cumulative boundaries, with every frame consumed.
        import subprocess
        body=json.dumps({'method':'fixture','params':{'text':'x'*4096}})
        wire.process=subprocess.Popen([sys.executable,'-c',
            'import sys\nfor _ in range(2100): print('+repr(body)+', flush=True)'],stdout=subprocess.PIPE)
        wire.deadline=None
        try:
            for _ in range(2100):self.assertEqual(wire._receive()['method'],'fixture')
        finally:
            wire.process.stdout.close();wire.process.wait()
        self.assertGreater(wire.received_bytes,2*8_388_608)
        self.assertGreater(wire.counter,4096)

    def test_pending_protocol_buffer_and_frame_security_remain(self):
        wire=StdioTransport((sys.executable,),allow_spawn=True)
        wire.incoming.extend(b'x'*MAX_FRAME+b'\n')
        with self.assertRaisesRegex(ValueError,'frame exceeds'):wire._receive()
        message={'method':'fixture','params':'x'*10000}
        wire.pending_bytes=MAX_PENDING_BYTES
        with patch.object(wire,'_send'),patch.object(wire,'_receive',return_value=message):
            with self.assertRaisesRegex(RuntimeError,'Unconsumed'):wire.call('fixture',{})

    def test_broker_failure_propagates_without_a_default_timeout(self):
        import os
        def broken(_):raise ValueError('secret dispatch input must not escape')
        for message in ({'id':1,'method':'fixture'},['invalid-object']):
            with self.subTest(message=message):
                wire=StdioTransport((sys.executable,'-c','import sys;sys.stdin.read()'),allow_spawn=True)
                try:
                    wire.configure_mcp(broken)
                    os.write(wire.mcp_in,encode(message))
                    with self.assertRaisesRegex(RuntimeError,'^Source MCP broker failed$'):
                        wire._receive()
                finally:wire.close()

    def test_group_uses_claim_bound_windows_without_legacy_batch_size_limit(self):
        from ..thought_processing import TracedThoughtWorker
        from ..processing_pool import ActiveClaims
        from ..turn_source.extractor import ContinuingSourceExtractor
        from threading import Event
        event=self.event('x'*300000+'TAIL')  # larger than legacy explicit batch 256000
        factory=SourceTurnFactory._for_protocol_fixture(SourceTurnConfig((sys.executable,),enabled=True))
        worker=TracedThoughtWorker(self.queue,self.engine,lane='source',lock_owned=True,
            processing_session_ids=('current',),claim_bound_source_extractor_factory=factory)
        renewed=Event();original=ActiveClaims.renew
        def renew(active,db,**kwargs):
            count=original(active,db,**kwargs)
            if count:renewed.set()
            return count
        def turn(_,reader):
            self.assertTrue(renewed.wait(2),'host heartbeat did not renew the long source claim')
            return self.proposal(reader)
        with self.memory.transaction(write=True) as db:self.queue._scheduler_schema(db)
        with (patch.object(ContinuingSourceExtractor,'_turn',turn),patch.object(ActiveClaims,'renew',renew),
              patch('prototypes.assertion_memory.thought_processing.GROUP_HEARTBEAT_SECONDS',.01)):
            worker.process_source_group([event.id],limit=1)
        job=self.queue.get(event.id)
        self.assertEqual(job.status,'completed',job.error)
        self.assertEqual(len(job.extracted_ids),1)

    def test_other_workers_keep_the_existing_retry_policy(self):
        event=self.event('Legacy source.')
        worker=ThoughtWorker(self.queue,self.engine,lane='source')
        with self.memory.transaction(write=True) as db:
            self.queue._scheduler_schema(db)
            db.execute('UPDATE memory_capture_jobs SET attempts=2 WHERE event_id=?',(event.id,))
        claim=worker._claim_source(event_id=event.id)
        job=self.queue._fail(claim,RuntimeError('synthetic failure'))
        self.assertEqual((job.status,job.attempts),('failed',3))
