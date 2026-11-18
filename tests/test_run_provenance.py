"""Synthetic causal execution history, not evidence membership or timestamp inference."""
import copy
import json
import unittest
from unittest.mock import patch

from prototypes.assertion_memory import run_provenance as provenance
from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.worker import LinkWorker
from prototypes.assertion_memory.derived_work import DerivedWorkQueue


class RunProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.engine = MemoryEngine(self.store)
        self.queue = CaptureQueue(self.store)
        self.worker = LinkWorker(self.queue,self.engine,self.provider)
        self.engine.define_view(ViewSpec('leaf','Leaf','Reported synthetic conditions',scopes=('s',)))
        self.engine.define_view(ViewSpec('parent','Parent','Combined conditions',scopes=('s',),depends_on=('leaf',)))

    def provider(self, request):
        if request.task.startswith('Extract'):
            target = request.records[0]
            return json.dumps({'assertions':[{'text':target['text'],'quote':target['text']}]})
        if request.task.startswith('Verify contextual'):
            return json.dumps({'verdicts':[{'index':r['index'],'status':'accepted','reason':'Synthetic exact attribution.'}
                for r in request.records if r.get('role')=='interpretation']})
        if request.task == 'Review source material coverage':
            return json.dumps({'source_coverage':{'status':'accepted','reason':'Synthetic complete source.','omissions':[]}})
        if request.task.startswith('Propose'):return json.dumps({'links':[],'branches':[]})
        raise AssertionError(request.task)

    def source(self, session, text):
        event = self.queue.capture_event(session,'message',text,source='fixture://'+session,scope='s',position=0)
        job = self.worker.run_once()
        self.assertEqual(job.id,event.id)
        self.assertIsNone(job.error)
        return event,job.extracted_ids[0]

    def outputs(self,session,**kwargs):
        with self.store.transaction() as db:
            return provenance.session_outputs(db,session,**kwargs)

    def test_automatic_source_refresh_keeps_exact_old_and_recursive_versions(self):
        event,assertion = self.source('session-a','Synthetic condition A.')
        job = self.worker.run_once()
        self.assertIsNone(job.error)
        first = self.outputs('session-a')['entries']
        self.assertEqual({r['view_id'] for r in first},{'leaf','parent'})
        produced = [r for r in first if r['outcome']=='produced']
        self.assertTrue(produced)
        parent = next(r for r in first if r['view_id']=='parent')
        old = copy.deepcopy(self.engine.get_version(parent['version_id']).__dict__)
        with self.store.transaction() as db:
            detail = provenance.event_page(db,parent['event_id'])
        self.assertEqual(json.loads(detail['content'])['upstream'],old['upstream'])
        self.source('session-b','Synthetic condition B.')
        self.worker.run_once()
        self.assertNotEqual(self.engine.inspect_view('parent')['current_id'],parent['version_id'])
        self.assertEqual(self.engine.get_version(parent['version_id']).__dict__,old)
        self.assertEqual(self.outputs('session-a')['entries'],first)
        self.assertTrue(self.outputs('session-b')['entries'])

    def test_reused_versions_are_recorded_as_reused_not_new_generation(self):
        event,assertion = self.source('session-a','Synthetic condition A.')
        self.worker.run_once()
        initial = self.outputs('session-a')['entries']
        self.engine.refresh_from(assertion)
        later = self.outputs('session-a')['entries'][len(initial):]
        self.assertTrue(later)
        self.assertEqual({r['outcome'] for r in later},{'reused'})
        self.assertTrue({r['version_id'] for r in later}<={r['version_id'] for r in initial})

    def test_derived_generation_retains_run_across_yield_and_mutable_result_replacement(self):
        event,assertion = self.source('session-a','Synthetic condition A.')
        derived = self.worker.derived
        claim = derived.claim(self.engine,with_model=True)
        run_id = claim.checkpoint['_scheduler']['provenance_run_id']
        with provenance.bind(self.engine,run_id):
            first = self.engine.build_view('parent',_snapshot=claim.snapshot)
        derived.release(claim)
        resumed = derived.claim(self.engine,with_model=True)
        self.assertEqual(resumed.checkpoint['_scheduler']['provenance_run_id'],run_id)
        with provenance.bind(self.engine,run_id):
            repeated = self.engine.build_view('parent',_snapshot=resumed.snapshot)
        derived.finish(resumed,{'root_view_id':'parent','version_id':repeated.id})
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_derived_work SET result='{}',checkpoint='{}'")
        rows = self.outputs('session-a')['entries']
        self.assertTrue(rows)
        self.assertEqual({r['run_id'] for r in rows},{run_id})
        self.assertEqual({r['cause_status'] for r in rows},{'complete'})
        with self.store.transaction() as db:
            history = json.loads(provenance.run_page(db,run_id,section='events')['content'])
        self.assertTrue(any(r['outcome']=='yielded' for r in history))
        self.assertTrue(any(r['outcome']=='completed' for r in history))

    def test_coalesced_generation_has_multiple_exact_trigger_sessions(self):
        a,aid = self.source('session-a','Synthetic condition A.')
        # Avoid processing A's separate link job before extracting B.
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_capture_jobs SET status='completed',phase='done' WHERE assertion_id=?",(aid,))
        b,bid = self.source('session-b','Synthetic condition B.')
        claim = self.worker.derived.claim(self.engine,with_model=True)
        run_id = claim.checkpoint['_scheduler']['provenance_run_id']
        with provenance.bind(self.engine,run_id): self.engine.build_view('parent',_snapshot=claim.snapshot)
        self.assertEqual(self.outputs('session-a')['entries'],self.outputs('session-b')['entries'])
        self.assertEqual({r['run_id'] for r in self.outputs('session-a')['entries']},{run_id})

    def test_no_causal_backfill_from_membership_episode_or_time(self):
        event = self.queue.capture_event('legacy-session','legacy','Legacy message.',source='fixture://legacy',scope='s')
        self.store.append_assertion(assertion_id='legacy',text='Legacy meaning.',actor='user',source='fixture://legacy',
            episode='legacy-session',scope='s',recorded_at='2026-11-16',metadata={'event_id':event.id})
        legacy = self.engine.build_view('parent')
        claim = self.worker.derived.claim(self.engine,with_model=True)
        run_id = claim.checkpoint['_scheduler']['provenance_run_id']
        with provenance.bind(self.engine,run_id):self.engine.build_view('parent',_snapshot=claim.snapshot)
        self.assertEqual(self.outputs('legacy-session')['entries'],[])
        with self.store.transaction() as db:
            run = json.loads(provenance.run_page(db,run_id)['content'])
        self.assertEqual(run['cause_status'],'unknown')
        self.assertEqual(self.engine.get_version(legacy.id).id,legacy.id)

    def test_pagination_pins_outputs_and_reads_do_not_change_graph_or_pipeline(self):
        event,assertion = self.source('session-a','Synthetic condition A.')
        self.worker.run_once()
        first = self.outputs('session-a',limit=1)
        self.engine.refresh_from(assertion)
        rest = self.outputs('session-a',offset=1,limit=10,snapshot_id=first['snapshot_id'])
        self.assertEqual(first['total'],1+len(rest['entries']))
        before = self.store._connection.total_changes
        key = self.engine.pipeline_key
        self.store._connection.execute('PRAGMA query_only=ON')
        self.outputs('session-a')
        with self.store.transaction() as db:provenance.event_page(db,first['entries'][0]['event_id'],limit=40)
        self.assertEqual(before,self.store._connection.total_changes)
        self.assertEqual(key,self.engine.pipeline_key)

    def test_failed_automatic_build_retains_its_real_published_failure_version(self):
        from prototypes.assertion_memory.model import ModelError
        event,assertion = self.source('session-a','Synthetic condition A.')
        with self.assertRaises(ModelError):
            with provenance.execution(self.engine,owner_id='synthetic-failure',event_id=event.id):
                with patch.object(self.engine,'_build',side_effect=ModelError('Synthetic build failure')):
                    self.engine.build_view('leaf')
        rows = self.outputs('session-a')['entries']
        self.assertEqual(len(rows),1)
        self.assertTrue(rows[0]['failure_proof'])
        self.assertEqual(rows[0]['review_status'],'unresolved')
        self.assertEqual(self.engine.get_version(rows[0]['version_id']).view_id,'leaf')

    def test_actual_derived_worker_activates_publication_context(self):
        self.source('session-a','Synthetic condition A.')
        self.engine.model = lambda request: self.fail('No provider work in this lifecycle fixture')
        self.engine.verify_overviews = True
        def builder(engine,scope,snapshot,checkpoint,save):
            self.assertEqual(provenance.active(engine),checkpoint['_scheduler']['provenance_run_id'])
            with patch.object(engine,'model',None),patch.object(engine,'verify_overviews',False):
                view = engine.build_view('parent',_snapshot=snapshot)
            return {'state':'idle','root_view_id':'parent','version_id':view.id}
        with patch('prototypes.assertion_memory.background_overviews.run_background_scope',side_effect=builder):
            result = self.worker._run_derived()
        self.assertEqual(result.status,'idle')
        self.assertEqual({r['run_kind'] for r in self.outputs('session-a')['entries']},{'derived_scope'})
        self.assertIsNone(provenance.active(self.engine))

    def test_causal_history_tables_reject_rewriting_published_records(self):
        import sqlite3
        event,assertion = self.source('session-a','Synthetic condition A.')
        self.worker.run_once()
        first = self.outputs('session-a')['entries']
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.transaction(write=True) as db:
                db.execute("UPDATE memory_run_events SET outcome='rewritten'")
        self.assertEqual(self.outputs('session-a')['entries'],first)

    def test_native_source_session_is_preferred_over_logical_capture_group(self):
        event,assertion=self.source('logical-group','Synthetic condition A.')
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_raw_events SET metadata=json_set(metadata,'$.native_session_id','native-chat') WHERE id=?",(event.id,))
        self.worker.run_once()
        self.assertTrue(self.outputs('native-chat')['entries'])
        self.assertEqual(self.outputs('logical-group')['entries'],[])
        first=self.outputs('native-chat')['entries'][0]
        with self.store.transaction() as db:
            causes=json.loads(provenance.run_page(db,first['run_id'],section='causes')['content'])
        self.assertEqual(causes[0]['source_session_id'],'native-chat')
        self.assertEqual(causes[0]['native_session_id'],'native-chat')
        self.assertEqual(causes[0]['capture_session_id'],'logical-group')
        self.assertEqual(causes[0]['session_id_basis'],'metadata.native_session_id')
    def test_two_native_sessions_under_one_group_and_unknown_fallback_stay_separate(self):
        a,aid=self.source('logical','Synthetic condition A.')
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_raw_events SET metadata=json_set(metadata,'$.native_session_id','native-a') WHERE id=?",(a.id,))
        self.worker.run_once()
        first=self.outputs('native-a')['entries']
        b=self.queue.capture_event('logical','second','Synthetic condition B.',source='fixture://logical/b',scope='s',position=1,metadata={'native_session_id':'native-b'})
        self.worker.run_once();self.worker.run_once()
        self.assertEqual(self.outputs('native-a')['entries'],first)
        self.assertTrue(self.outputs('native-b')['entries'])
        self.assertEqual(self.outputs('logical')['entries'],[])
        self.assertEqual(self.outputs('native-b-not-real')['entries'],[])
    def test_caller_context_is_explicit_optional_and_not_source_authentication(self):
        with provenance.execution(self.engine,owner_id='request:synthetic',request_session_id='caller-native'):
            self.engine.build_view('parent')
        entries=self.outputs('caller-native')['entries']
        self.assertTrue(entries)
        self.assertEqual({r['run_kind'] for r in entries},{'direct_request'})
        self.assertEqual({r['cause_status'] for r in entries},{'caller_context_only'})
        self.assertEqual(self.outputs('other-session')['entries'],[])
    def test_read_helpers_missing_history_do_not_create_schema(self):
        import sqlite3
        with sqlite3.connect(':memory:') as db:
            db.row_factory=sqlite3.Row
            self.assertEqual(provenance.session_outputs(db,'legacy')['unrecorded_history'],'unknown')
            with self.assertRaisesRegex(ValueError,'unknown provenance event'):provenance.event_page(db,1)
            with self.assertRaisesRegex(ValueError,'unknown execution run'):provenance.run_page(db,'absent')
            self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE 'memory_run_%'").fetchone()[0],0)
    def test_all_four_causal_tables_are_append_only(self):
        import sqlite3
        self.source('session-a','Synthetic condition A.');self.worker.run_once()
        for table in provenance.TABLES:
            for statement in ('UPDATE '+table+' SET rowid=rowid','DELETE FROM '+table):
                with self.subTest(statement=statement),self.assertRaises(sqlite3.IntegrityError):
                    with self.store.transaction(write=True) as db:db.execute(statement)
    def test_paged_event_and_pinned_run_events_reassemble_exact_upstream(self):
        event,assertion=self.source('session-a','Synthetic condition A.');self.worker.run_once()
        first=self.outputs('session-a')['entries'][0]
        with self.store.transaction() as db:
            page=provenance.run_page(db,first['run_id'],section='events')
        frozen=json.loads(page['content']);self.engine.refresh_from(assertion)
        with self.store.transaction() as db:
            self.assertEqual(json.loads(provenance.run_page(db,first['run_id'],section='events',snapshot_id=page['snapshot_id'])['content']),frozen)
            chunks=[];offset=0
            while True:
                part=provenance.event_page(db,first['event_id'],offset=offset,limit=37)
                chunks.append(part['content'])
                if part['next_offset'] is None:break
                offset=part['next_offset']
        pinned=json.loads(''.join(chunks))
        self.assertEqual(pinned['version_id'],first['version_id'])
        self.assertEqual(pinned['check_id'],first['check_id'])
        self.assertEqual(pinned['upstream'],self.engine.get_version(first['version_id']).upstream)
    def test_explicit_caller_context_works_without_raw_event_schema(self):
        with MemoryStore(':memory:') as store:
            engine=MemoryEngine(store);engine.define_view(ViewSpec('empty','Empty','Synthetic empty question'))
            with provenance.execution(engine,owner_id='query:synthetic',request_session_id='caller-native'):
                engine.build_view('empty')
            self.assertTrue(provenance.session_outputs(store._connection,'caller-native')['entries'])
    def test_matching_source_and_explicit_caller_same_run_is_not_duplicated(self):
        event=self.queue.capture_event('same-session','one','Synthetic source.',source='fixture://same',scope='s')
        with provenance.execution(self.engine,owner_id='query:synthetic',event_id=event.id,request_session_id='same-session'):
            self.engine.build_view('parent')
        result=self.outputs('same-session')
        self.assertEqual(result['total'],2)
        self.assertEqual(len({entry['event_id'] for entry in result['entries']}),2)
    def test_empty_offset_page_retains_total_and_snapshot(self):
        self.source('session-a','Synthetic condition A.');self.worker.run_once()
        first=self.outputs('session-a')
        empty=self.outputs('session-a',offset=100,snapshot_id=first['snapshot_id'])
        self.assertEqual(empty['entries'],[]);self.assertEqual(empty['total'],first['total'])
        self.assertEqual(empty['snapshot_id'],first['snapshot_id'])
        self.assertIsNone(empty['next_offset']);self.assertTrue(empty['response_complete'])
    def test_session_lookup_cost_is_not_causes_times_output_events(self):
        costs=[]
        for size in (128,256):
            with MemoryStore(':memory:') as store:
                engine=MemoryEngine(store);CaptureQueue(store)
                engine.define_view(ViewSpec('empty','Empty','Synthetic empty question'))
                version=engine.build_view('empty')
                with store.transaction(write=True) as db:
                    check_id=db.execute('SELECT max(id) FROM memory_view_checks').fetchone()[0]
                    db.executemany('INSERT INTO memory_raw_events VALUES(?,?,?,?,?,?,?,?,?)',
                        ((str(i),'logical','turn','Synthetic fixture','user','fixture://cost','s','2026-01-01',
                          json.dumps({'native_session_id':'native-'+str(i)})) for i in range(size)))
                    db.execute("INSERT INTO memory_run_provenance VALUES('coalesced','derived_scope','s','s','2026','complete','{}','{}')")
                    db.executemany("INSERT INTO memory_run_causes(id,scope,generation_from,generation_to,source_event_id,job_id,recorded_at) VALUES(?,'s',0,1,?,'fixture','2026')",((i+1,str(i)) for i in range(size)))
                    db.executemany("INSERT INTO memory_run_cause_links VALUES('coalesced',?)",((i+1,) for i in range(size)))
                    db.executemany("INSERT INTO memory_run_events(run_id,kind,outcome,recorded_at,view_id,version_id,check_id,upstream) VALUES('coalesced','view','reused','2026','empty',?,?,'{}')",((version.id,check_id) for _ in range(size)))
                steps=[0]
                def progress():steps[0]+=100;return 0
                store._connection.set_progress_handler(progress,100)
                try:
                    result=provenance.session_outputs(store._connection,'nonexistent-session',limit=1)
                finally:store._connection.set_progress_handler(None,0)
                self.assertEqual(result['total'],0)
                costs.append(steps[0])
        print('CAUSAL_QUERY_VM_STEPS '+json.dumps({'sizes':[128,256],'steps':costs}))
        self.assertLessEqual(costs[1],costs[0]*3+5000,
            'Doubling causes and outputs must not quadruple source-matching work')
    def test_provenance_mutation_rolls_back_with_source_publication(self):
        with patch.object(provenance,'record_changes',side_effect=ValueError('synthetic provenance persistence failure')):
            event = self.queue.capture_event('session-a','one','Synthetic condition.',source='fixture://one',scope='s')
            job = self.worker.run_once()
        self.assertIsNotNone(job.error)
        self.assertEqual(self.store.list_assertions(),[])
        self.assertEqual(self.queue.extraction_history(event.id),[])


if __name__=='__main__':unittest.main()
