import json
import unittest
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.derived_work import DerivedWorkQueue
from prototypes.assertion_memory import source_reprocessing as sources
from prototypes.assertion_memory import policy_work

class RolloutAdmissionTest(unittest.TestCase):
    def setUp(self):
        self.store=MemoryStore(':memory:');self.addCleanup(self.store.close)
        self.queue=CaptureQueue(self.store);self.old=MemoryEngine(self.store,model_key='old')
        self.work=DerivedWorkQueue(self.store)
    def legacy_event(self,key='old',position=0):
        event=self.queue.capture_event('session',key,'Synthetic source '+key,actor='user',source='fixture://'+key,scope='s',position=position)
        with self.store.transaction(write=True) as c:
            c.execute('INSERT INTO memory_extraction_runs(event_id,fingerprint,completed_at,result) VALUES(?,?,?,?)',(event.id,key,self.queue._time(),json.dumps({'assertion_ids':[]})))
            c.execute("UPDATE memory_capture_jobs SET status='completed',phase='done' WHERE event_id=?",(event.id,))
            c.execute("INSERT INTO memory_source_audits VALUES(?,'old-source-policy','old','old','accepted',NULL)",(event.id,))
            c.execute('DELETE FROM memory_source_context_changes')
        return event
    def add_assertion(self,key):
        return self.store.append_assertion(assertion_id=key,text='Synthetic '+key,actor='user',source='fixture://'+key,episode='session',scope='s',recorded_at=self.queue._time())
    def test_package_change_does_not_enqueue_old_sources_or_rewrite_receipts(self):
        event=self.legacy_event()
        sources.tick(self.queue)
        self.assertEqual(self.queue.get(event.id).status,'completed')
        c=self.store._connection
        self.assertEqual(c.execute('SELECT protocol FROM memory_source_audits').fetchone()[0],'old-source-policy')
        self.assertEqual(c.execute('SELECT cursor FROM memory_source_audit_cursor WHERE protocol=?',(sources.PROTOCOL,)).fetchone()[0],1)
    def test_constructor_pin_is_idempotent_and_skips_unadmitted_old_cursor(self):
        event=self.legacy_event()
        with self.store.transaction(write=True) as c:
            c.execute('INSERT INTO memory_source_audit_cursor VALUES(?,0)',(sources.PROTOCOL,))
            self.assertEqual(sources.initialize_admission(c),1)
        self.queue.capture_event('session','new','Synthetic new source',actor='user',source='fixture://new',scope='s',position=1)
        with self.store.transaction(write=True) as c:
            self.assertEqual(sources.initialize_admission(c),1)
            self.assertEqual(c.execute('SELECT cursor FROM memory_source_audit_cursor WHERE protocol=?',(sources.PROTOCOL,)).fetchone()[0],1)
        sources.tick(self.queue)
        self.assertEqual(self.queue.get(event.id).status,'completed')
    def test_explicit_material_gap_request_still_admits_historical_source(self):
        event=self.legacy_event();sources.tick(self.queue)
        self.queue.request_source_audit(event.id,'synthetic-explicit-gap')
        sources.tick(self.queue)
        self.assertEqual(self.queue.get(event.id).phase,'audit')
    def test_new_capture_keeps_its_extraction_job(self):
        old=self.legacy_event();sources.tick(self.queue)
        new=self.queue.capture_event('session','new','Synthetic new source',actor='user',source='fixture://new',scope='s',position=1)
        sources.tick(self.queue)
        self.assertEqual(self.queue.get(old.id).status,'completed')
        self.assertEqual(self.queue.get(new.id).phase,'extract');self.assertEqual(self.queue.get(new.id).status,'waiting_model')
    def test_real_inserted_context_suffix_still_audits_old_source(self):
        old=self.legacy_event(position=1);sources.tick(self.queue)
        self.queue.capture_event('session','inserted','Synthetic earlier context',actor='user',source='fixture://inserted',scope='s',position=0)
        sources.tick(self.queue)
        self.assertEqual(self.queue.get(old.id).phase,'audit')
    def test_explicit_component_range_admits_historical_source(self):
        event=self.legacy_event();outside=self.legacy_event('outside',position=1);sources.tick(self.queue)
        policy_work.register(self.queue,component='source',old='old',new=sources.PROTOCOL,scope='s',start=0,end=1)
        policy_work.tick(self.queue,self.old)
        self.assertEqual(self.queue.get(event.id).phase,'audit')
        self.assertEqual(self.queue.get(outside.id).status,'completed')
    def test_old_completed_navigation_is_idle_until_real_change(self):
        self.add_assertion('one');claim=self.work.claim(self.old,with_model=True);self.work.finish(claim,{'pipeline_key':self.old.pipeline_key})
        new=MemoryEngine(self.store,model_key='new')
        self.assertIsNone(self.work.claim(new,with_model=True))
        row=self.work.status()[0]
        self.assertEqual(row['state'],'idle');self.assertEqual(row['completed_frontier']['pipeline_key'],self.old.pipeline_key)
        self.add_assertion('two')
        admitted=self.work.claim(new,with_model=True)
        self.assertIsNotNone(admitted);self.assertEqual(admitted.frontier['pipeline_key'],new.pipeline_key)
    def test_explicit_view_policy_work_admits_scope(self):
        self.add_assertion('one');claim=self.work.claim(self.old,with_model=True);self.work.finish(claim,{'pipeline_key':self.old.pipeline_key})
        new=MemoryEngine(self.store,model_key='new')
        policy_work.register(self.queue,component='views',old='old',new='new',scope='s',start=0,end=0)
        policy_work.tick(self.queue,new)
        self.assertIsNotNone(self.work.claim(new,with_model=True))
    def test_hundred_thousand_historical_rows_do_not_become_work(self):
        c=self.store._connection
        with self.store.transaction(write=True):
            c.executemany('INSERT INTO memory_raw_events VALUES(?,?,?,?,?,?,?,?,?)',
                ((str(i),'large','turn','Synthetic fixture','assistant','fixture://large','large','2026-01-01','{}') for i in range(100000)))
            c.executemany("INSERT INTO memory_capture_jobs(id,event_id,status,phase,updated_at,available_at) VALUES(?,?,'completed','done','2026-01-01','2026-01-01')",((str(i),str(i)) for i in range(100000)))
        before=c.total_changes
        with self.store.transaction(write=True):self.assertEqual(sources.initialize_admission(c),100000)
        sources.tick(self.queue)
        self.assertLessEqual(c.total_changes-before,2)
        self.assertEqual(c.execute("SELECT count(*) FROM memory_capture_jobs WHERE status='completed'").fetchone()[0],100000)
    def test_readonly_legacy_counts_do_not_claim_new_coverage(self):
        self.legacy_event();self.add_assertion('one');claim=self.work.claim(self.old,with_model=True);self.work.finish(claim,{'pipeline_key':self.old.pipeline_key})
        new=MemoryEngine(self.store,model_key='new');before=self.store._connection.total_changes
        self.store._connection.execute('PRAGMA query_only=ON')
        self.assertEqual(sources.legacy_coverage(self.store._connection,scope='s')['legacy_event_count'],1)
        self.assertEqual(self.work.legacy_coverage(new,scope='s')['legacy_scope_count'],1)
        self.assertEqual(before,self.store._connection.total_changes)
    def test_public_service_initializes_admission_before_first_capture(self):
        from prototypes.assertion_memory.service import MemoryService
        old=self.legacy_event()
        service=MemoryService(self.store)
        service.capture_message('new-session','message','user','user','New synthetic source',None,'fixture://new')
        c=self.store._connection
        row=c.execute('SELECT cursor FROM memory_source_audit_cursor WHERE protocol=?',(sources.PROTOCOL,)).fetchone()
        self.assertIsNotNone(row,'service must pin admission before accepting new source')
        self.assertEqual(row[0],1)
        self.assertEqual(service.queue.get(old.id).status,'completed')
        coverage=service.status(section='policy_coverage')['policy_coverage']
        self.assertEqual(coverage['source']['legacy_event_count'],1)
        self.assertFalse(coverage['source']['coverage_current'])

if __name__=='__main__':unittest.main()
