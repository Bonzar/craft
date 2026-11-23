"""Real SQLite concurrency/admission, synthetic extraction and verification only."""
import json
import sqlite3
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from . import test_source_budget_references as helpers
from ..source_admission import stamp, unchanged, install
from ..turn_source.navigation import ClaimNavigation, OwnerScopeGrant
from ..turn_source.session_dialogue import SessionDialogue
from ..turn_source.reader import SourceReadDenied


class AdmissionTest(unittest.TestCase):
    setUp=helpers.BudgetReferencesTest.setUp
    event=helpers.BudgetReferencesTest.event
    run_source=helpers.BudgetReferencesTest.run_source
    proposal=staticmethod(helpers.BudgetReferencesTest.proposal)
    make_thought=helpers.BudgetReferencesTest.make_thought

    def test_checkpoint_and_writer_finish_while_verifier_is_blocked(self):
        primary=self.event('Primary exact source.')
        entered=threading.Event();release=threading.Event();results=[];errors=[]
        def concurrent_writer():
            try:
                self.assertTrue(entered.wait(5))
                with sqlite3.connect(self.memory.path,isolation_level=None,timeout=2) as db:
                    db.execute('CREATE TABLE checkpoint_probe(value TEXT)')
                    db.execute("INSERT INTO checkpoint_probe VALUES('concurrent committed write')")
                    results.append(db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone())
            except BaseException as error:errors.append(error)
            finally:release.set()
        thread=threading.Thread(target=concurrent_writer);thread.start()
        def verify(request):
            self.assertFalse(self.memory._connection.in_transaction)
            entered.set()
            self.assertTrue(release.wait(5),'checkpoint was blocked by a model-lifetime snapshot')
            return json.dumps({'verdict':'accept','text':'Synthetic approval.'})
        self.engine.model=verify
        try:job=self.run_source(primary,lambda _,r:self.proposal(r))
        finally:release.set();thread.join(5)
        self.assertFalse(thread.is_alive());self.assertFalse(errors,errors)
        self.assertEqual(results,[(0,0,0)])
        self.assertEqual(job.status,'completed',job.error)

    def test_primary_change_restore_between_reads_invalidates_attempt(self):
        event=self.event('Exact full primary, including unseen tail.')
        def turn(_,reader):
            # A restored string is not the original admission generation.
            with sqlite3.connect(self.memory.path) as db:
                db.execute('UPDATE memory_raw_events SET prompt=? WHERE id=?',('changed tail',event.id))
                db.execute('UPDATE memory_raw_events SET prompt=? WHERE id=?',(event.prompt,event.id))
            return self.proposal(reader)
        job=self.run_source(event,turn)
        self.assertEqual(job.status,'pending');self.assertFalse(job.extracted_ids)

    def test_issued_old_excerpt_rejects_change_outside_quoted_span(self):
        old=self.event('Visible start. Original remote tail.')
        event=self.event('Primary.',position=2)
        def turn(_,reader):
            with reader.snapshot() as db:row=reader.row(db,old.id)
            ref=reader.offer(old.id,0,7,'Visible',(row['ordinal'],row['position']))
            with sqlite3.connect(self.memory.path) as db:
                db.execute('UPDATE memory_raw_events SET prompt=? WHERE id=?',('Visible start. DIFFERENT tail.',old.id))
            with self.assertRaises(SourceReadDenied):reader.evidence([ref])
            # A referenced revoked source cannot be smuggled through a canned proposal.
            reader.selected.add(ref)
            return {'status':'proposal','text':'Canned','supports':[reader.primary_ref,ref]}
        job=self.run_source(event,turn)
        self.assertEqual(job.status,'pending');self.assertFalse(job.extracted_ids)

    def test_identical_raw_replacement_and_job_revocation_are_not_admitted(self):
        for mutation in ('replace','job','position'):
            with self.subTest(mutation=mutation):
                old=self.event('Old exact bytes.',session=mutation,position=1)
                event=self.event('Primary.',session=mutation,position=2)
                def turn(_,reader):
                    page=reader.older(None);ref=page['messages'][0]['ref']
                    with sqlite3.connect(self.memory.path) as db:
                        if mutation=='replace':
                            row=db.execute('SELECT rowid,* FROM memory_raw_events WHERE id=?',(old.id,)).fetchone()
                            db.execute('DELETE FROM memory_raw_events WHERE id=?',(old.id,))
                            db.execute('INSERT INTO memory_raw_events(rowid,id,session_id,turn_id,prompt,actor,source,scope,recorded_at,metadata) VALUES(?,?,?,?,?,?,?,?,?,?)',row)
                        elif mutation=='job':
                            db.execute("UPDATE memory_capture_jobs SET status='waiting_origin' WHERE event_id=?",(old.id,))
                            db.execute("UPDATE memory_capture_jobs SET status='pending' WHERE event_id=?",(old.id,))
                        else:
                            db.execute('UPDATE memory_event_positions SET position=9 WHERE event_id=?',(old.id,))
                            db.execute('UPDATE memory_event_positions SET position=1 WHERE event_id=?',(old.id,))
                    with self.assertRaises(SourceReadDenied):reader.evidence([ref])
                    return self.proposal(reader)
                job=self.run_source(event,turn)
                self.assertEqual(job.status,'completed',job.error)  # unchanged current RAW only

    def test_late_backdated_raw_and_thought_versions_with_reused_rowids_are_excluded(self):
        old=self.event('Old proof.',position=1)
        thought=self.make_thought(old,'Admitted thought.')
        event=self.event('Primary.',position=2)
        def turn(_,reader):
            nav=ClaimNavigation(reader,None)
            with sqlite3.connect(self.memory.path) as db:
                row=db.execute('SELECT rowid,* FROM memory_thought_versions WHERE thought_id=?',(thought.id,)).fetchone()
                db.execute('DELETE FROM memory_thought_versions WHERE thought_id=?',(thought.id,))
                db.execute('INSERT INTO memory_thought_versions(rowid,thought_id,version,payload,content_hash) VALUES(?,?,?,?,?)',row)
                raw=db.execute('SELECT rowid,* FROM memory_raw_events WHERE id=?',(old.id,)).fetchone()
                db.execute('DELETE FROM memory_raw_events WHERE id=?',(old.id,))
                db.execute('INSERT INTO memory_raw_events(rowid,id,session_id,turn_id,prompt,actor,source,scope,recorded_at,metadata) VALUES(?,?,?,?,?,?,?,?,?,?)',raw)
            self.assertEqual(reader.older(None)['messages'],[])
            with reader.snapshot() as db:self.assertEqual(nav._rows(db,None),[])
            return self.proposal(reader)
        job=self.run_source(event,turn)
        self.assertEqual(job.status,'completed',job.error)

    def test_final_writer_rejects_foreign_context_revocation_after_extractor_returns(self):
        foreign=self.event('AUTHORIZED-FOREIGN-SECRET',session='foreign')
        event=self.event('Primary.')
        grant=OwnerScopeGrant(Path(self.memory.path),'scope')
        def turn(_,reader):
            foreign_ref=SessionDialogue(reader,grant).read({'session_id':'foreign'})['messages'][0]['ref']
            return self.proposal(reader,[reader.primary_ref,foreign_ref])
        original=self.engine._publish
        def publish(*args,**kwargs):
            with sqlite3.connect(self.memory.path) as db:
                db.execute("UPDATE memory_capture_jobs SET status='waiting_origin' WHERE event_id=?",(foreign.id,))
            return original(*args,**kwargs)
        with patch.object(self.engine,'_publish',publish):job=self.run_source(event,turn,grant=grant)
        self.assertEqual(job.status,'pending');self.assertFalse(job.extracted_ids)
        self.assertNotIn('AUTHORIZED-FOREIGN-SECRET',job.error)
        self.assertFalse(self.thoughts.list_thoughts())

    def test_final_writer_rejects_identical_selected_thought_version_replacement(self):
        old=self.event('Old raw.');thought=self.make_thought(old,'Shown thought.')
        event=self.event('Primary.',position=2)
        def turn(_,reader):
            ClaimNavigation(reader,None)
            ref=reader.offer_thought(thought,local=True)
            return self.proposal(reader,[reader.primary_ref,ref])
        original=self.engine._publish
        def publish(*args,**kwargs):
            with sqlite3.connect(self.memory.path) as db:
                row=db.execute('SELECT * FROM memory_thought_versions WHERE thought_id=?',(thought.id,)).fetchone()
                db.execute('INSERT OR REPLACE INTO memory_thought_versions VALUES(?,?,?,?)',row)
            return original(*args,**kwargs)
        with patch.object(self.engine,'_publish',publish):job=self.run_source(event,turn)
        self.assertEqual(job.status,'pending');self.assertFalse(job.extracted_ids)

    def test_final_writer_does_no_filesystem_or_model_work(self):
        foreign=self.event('Context.',session='foreign');event=self.event('Primary.')
        grant=OwnerScopeGrant(Path(self.memory.path),'scope')
        def turn(_,reader):
            ref=SessionDialogue(reader,grant).read({'session_id':'foreign'})['messages'][0]['ref']
            return self.proposal(reader,[reader.primary_ref,ref])
        original=self.engine._publish
        def publish(*args,**kwargs):
            with (patch.object(Path,'stat',side_effect=AssertionError('stat inside publication')),
                  patch.object(Path,'resolve',side_effect=AssertionError('resolve inside publication')),
                  patch('builtins.open',side_effect=AssertionError('file IO inside publication')),
                  patch.object(self.engine,'model',side_effect=AssertionError('model inside publication'))):
                return original(*args,**kwargs)
        with patch.object(self.engine,'_publish',publish):job=self.run_source(event,turn,grant=grant)
        self.assertEqual(job.status,'completed',job.error)

    def test_direct_sql_triggers_rollback_and_id_changes(self):
        raw=self.event('Raw.')
        with self.memory.transaction() as db:before,_=stamp(db)
        with sqlite3.connect(self.memory.path,isolation_level=None) as db:
            db.execute('BEGIN')
            db.execute('UPDATE memory_raw_events SET prompt=? WHERE id=?',('temporary',raw.id))
            self.assertFalse(unchanged(db,before,'raw',raw.id))
            db.execute('ROLLBACK')
            self.assertEqual(stamp(db)[0],before)
            self.assertTrue(unchanged(db,before,'raw',raw.id))
            db.execute('UPDATE memory_raw_events SET id=? WHERE id=?',('new-identity',raw.id))
            self.assertFalse(unchanged(db,before,'raw',raw.id))
            self.assertFalse(unchanged(db,before,'raw','new-identity'))

    def test_migration_seeds_existing_ids_without_copying_bodies(self):
        raw=self.event('MIGRATION-SOURCE-TEXT')
        thought=self.make_thought(raw,'MIGRATION-THOUGHT-TEXT')
        with self.memory.transaction(write=True) as db:
            names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'source_admission_%'")]
            for name in names:db.execute('DROP TRIGGER '+name)
            db.execute('DROP TABLE memory_source_admission');db.execute('DROP TABLE memory_source_admission_clock')
            install(db);install(db,thoughts=True)
            watermark,instance=stamp(db)
            self.assertTrue(unchanged(db,watermark,'raw',raw.id))
            self.assertTrue(unchanged(db,watermark,'version',thought.id,1))
            self.assertNotIn('MIGRATION',str([tuple(r) for r in db.execute('SELECT * FROM memory_source_admission')]))
            install(db);install(db,thoughts=True)
            self.assertEqual(stamp(db),(watermark,instance))

    def test_final_publication_rejects_deleted_source_and_revoked_grant(self):
        for kind in ('delete','grant'):
            with self.subTest(kind=kind):
                foreign=self.event('Context.',session='foreign-'+kind)
                event=self.event('Primary.',session=kind)
                grant=OwnerScopeGrant(Path(self.memory.path),'scope')
                def turn(_,reader):
                    ref=SessionDialogue(reader,grant).read({'session_id':'foreign-'+kind})['messages'][0]['ref']
                    return self.proposal(reader,[reader.primary_ref,ref])
                original=self.engine._publish
                def publish(*args,**kwargs):
                    if kind=='delete':
                        with sqlite3.connect(self.memory.path) as db:
                            db.execute('DELETE FROM memory_raw_events WHERE id=?',(foreign.id,))
                    else:object.__setattr__(grant,'scope','revoked')
                    return original(*args,**kwargs)
                with patch.object(self.engine,'_publish',publish):job=self.run_source(event,turn,grant=grant)
                self.assertEqual(job.status,'pending');self.assertFalse(job.extracted_ids)

    def test_final_publication_rejects_head_advanced_after_verification(self):
        old=self.event('Old raw.');thought=self.make_thought(old,'Version one.')
        event=self.event('Primary.',position=2)
        def turn(_,reader):
            ClaimNavigation(reader,None)
            return self.proposal(reader,[reader.primary_ref,reader.offer_thought(thought,local=True)])
        original=self.engine._publish
        def publish(*args,**kwargs):
            # Independent producer, outside the source publication guard.
            with sqlite3.connect(self.memory.path) as db:
                db.execute('UPDATE memory_thoughts SET current_version=2 WHERE id=?',(thought.id,))
            return original(*args,**kwargs)
        with patch.object(self.engine,'_publish',publish):job=self.run_source(event,turn)
        self.assertEqual(job.status,'pending');self.assertFalse(job.extracted_ids)
