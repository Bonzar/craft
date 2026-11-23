"""Temporary SQLite rebuilds only. No real provider, runtime or deployment."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .. import rebuild, rebuild_admission as admission
from ..backup import snapshot_database
from ..source_admission import stamp
from ..storage import MemoryStore
from ..queue import CaptureQueue
from ..thought_store import ThoughtStore
from ..turn_source.reader import EarlierDialogue,SourceReadDenied
from ..source_extraction import SourceExtractionRequest


class RebuildAdmissionTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.live=self.root/'live.sqlite'
        with MemoryStore(self.live) as store:
            self.raw=CaptureQueue(store).capture_event('fixture','one','Original source.',source='fixture')
            ThoughtStore(store)
        self.config=self.root/'config.json'
        self.config.write_text(json.dumps({'db':str(self.live),'cwd_roots':[str(self.root)]}))
        # Stable synthetic identity; no provider construction or credentials.
        self.enterContext(patch.object(rebuild,'_provider_identity',return_value='offline-provider'))
        self.op=rebuild.start(self.config,self.root/'operation')
        self.state=rebuild.status(self.op)
        self.stage=Path(self.state['stage_db']);self.baseline=Path(self.state['baseline_db'])

    def write(self,path,sql,args=()):
        with sqlite3.connect(path) as db:db.execute(sql,args)

    def stage_work(self):
        with MemoryStore(self.stage) as store:
            CaptureQueue(store)
            store.append_assertion(assertion_id='synthetic-staged',text='Synthetic staged interpretation.',
                actor='user',source='fixture',episode='fixture',recorded_at='2024-01-01')
            with store.transaction(write=True) as db:
                db.execute("INSERT INTO memory_capture_jobs(id,assertion_id,status,phase,updated_at,available_at) VALUES('synthetic-staged','synthetic-staged','completed','done','2024-01-01','2024-01-01')")
                db.execute("UPDATE memory_capture_jobs SET status='completed',phase='done' WHERE event_id=?",(self.raw.id,))

    def ready(self,operation):
        # Only the semantic work/coverage is stubbed. All admission, raw/business
        # history, path, barrier, backup, candidate and replace checks are real.
        state=rebuild.status(operation)
        rebuild._fence(state,operation)
        with rebuild._read(self.live) as live:rebuild._live_guard(state,live)
        state.update(phase='ready',issues=[])
        return state

    def cutover(self):
        with patch.object(rebuild,'_validate',self.ready):
            return rebuild.cutover(self.op,writer_pids=[],profile_sha256=self.state['config_sha256'])

    def test_start_forks_epoch_and_normal_live_capture_stage_work_catchup(self):
        with rebuild._read(self.baseline) as base,rebuild._read(self.stage) as staged:
            before,identity=stamp(base);after,new_identity=stamp(staged)
            self.assertGreater(after,before);self.assertNotEqual(identity,new_identity)
            admission.stage_lineage(self.state,base,staged)
        self.stage_work()
        with MemoryStore(self.live) as store:
            extra=CaptureQueue(store).capture_event('fixture','two','Late new source.',source='fixture')
        result=rebuild.catchup(self.op)
        self.assertEqual(result,{'imported':1})
        self.assertEqual(rebuild.catchup(self.op),{'imported':0})
        rebuild._fence(rebuild.status(self.op),self.op)
        with rebuild._read(self.stage) as db:
            self.assertIsNotNone(db.execute('SELECT 1 FROM memory_raw_events WHERE id=?',(extra.id,)).fetchone())
        # Run actual coverage validator: unfinished semantic work stays explicit,
        # but mutable admission metadata no longer aborts validation.
        state=rebuild.validate(self.op)
        self.assertIn('source_generation_pending',state['issues'])

    def test_atomic_publication_confirm_and_runtime_capture(self):
        self.stage_work()
        original=rebuild.ledger_digest(self.live)
        result=self.cutover()
        self.assertEqual(result['phase'],'published')
        self.assertEqual(rebuild.ledger_digest(Path(result['precutover_db'])),original)
        with rebuild._read(self.live) as db,rebuild._read(self.baseline) as base:
            self.assertEqual(stamp(db)[1],self.state['admission']['stage_instance'])
            self.assertGreater(stamp(db)[0],stamp(base)[0])
        with MemoryStore(self.live) as store:
            CaptureQueue(store).capture_event('fixture','post','New runtime source.',source='fixture')
        proof=self.root/'verification.json'
        proof.write_text(json.dumps({'profile_sha256':self.state['config_sha256'],'source_db':str(self.live),
            'topic_projection_policy':rebuild.POLICIES['presentation'],'runtime_rebound':True}))
        self.assertEqual(rebuild.confirm(self.op,verification=proof)['phase'],'completed')

    def test_interrupted_replace_resumes_without_new_epoch_or_counter_drift(self):
        self.stage_work()
        original_replace=os.replace
        def interrupted(source,target):
            if Path(target)==self.live:raise RuntimeError('synthetic interruption before atomic replace')
            return original_replace(source,target)
        with patch.object(rebuild.os,'replace',interrupted):
            with self.assertRaisesRegex(RuntimeError,'synthetic interruption'):self.cutover()
        state=rebuild.status(self.op);self.assertEqual(state['phase'],'cutover')
        with rebuild._read(self.stage) as db:before=stamp(db)
        self.assertEqual(self.cutover()['phase'],'published')
        with rebuild._read(self.live) as db:self.assertEqual(stamp(db),before)
        self.assertEqual(self.cutover()['phase'],'published')

    def test_interruption_after_replace_recovers_exact_candidate(self):
        self.stage_work();write=rebuild._write
        def interrupted(path,state):
            if state['phase']=='published':raise RuntimeError('synthetic crash after replace')
            return write(path,state)
        with patch.object(rebuild,'_write',interrupted):
            with self.assertRaisesRegex(RuntimeError,'synthetic crash'):self.cutover()
        self.assertEqual(rebuild.status(self.op)['phase'],'cutover')
        self.assertEqual(self.cutover()['phase'],'published')

    def test_old_reader_rejects_cutover_and_atomic_backup_restore(self):
        class Lease:
            def check_reader_snapshot(self,db,path):pass
        request=SourceExtractionRequest(self.raw.id,self.raw.scope,self.raw.session_id,self.raw.session_id,Lease())
        reader=EarlierDialogue(self.live,request,self.raw,freeze_membership=True)
        self.addCleanup(reader.close)
        old_instance=reader.instance
        descriptor=reader._identity_fd
        original_inode=os.fstat(descriptor).st_ino
        self.stage_work();published=self.cutover()
        self.assertEqual(os.fstat(descriptor).st_ino,original_inode)
        self.assertNotEqual(self.live.stat().st_ino,original_inode)
        with self.assertRaises(SourceReadDenied):reader.evidence([reader.primary_ref])
        replacement=self.root/'rollback.sqlite'
        snapshot_database(Path(published['precutover_db']),replacement)
        os.replace(replacement,self.live)
        with rebuild._read(self.live) as db:self.assertEqual(stamp(db)[1],old_instance)
        with self.assertRaisesRegex(SourceReadDenied,'path changed'):
            reader.evidence([reader.primary_ref])
        reader.close()
        with self.assertRaises(OSError):os.fstat(descriptor)
        self.assertEqual(rebuild.ledger_digest(self.live),rebuild.ledger_digest(Path(published['precutover_db'])))

    def test_live_raw_aba_is_rejected_even_when_business_bytes_match(self):
        with sqlite3.connect(self.live) as db:
            db.execute('UPDATE memory_raw_events SET prompt=? WHERE id=?',('changed',self.raw.id))
            db.execute('UPDATE memory_raw_events SET prompt=? WHERE id=?',(self.raw.prompt,self.raw.id))
        with rebuild._read(self.live) as live:
            with self.assertRaisesRegex(ValueError,'identity revision divergence'):rebuild._live_guard(self.state,live)

    def test_stage_marker_regression_missing_trigger_and_epoch_swap_are_rejected(self):
        for mutation in ('marker','trigger','epoch','clock','missing','backdate'):
            with self.subTest(mutation=mutation):
                copy=self.root/(mutation+'.sqlite');snapshot_database(self.stage,copy)
                with sqlite3.connect(copy) as db:
                    if mutation=='marker':db.execute('UPDATE memory_source_admission SET revision=0')
                    elif mutation=='trigger':db.execute('DROP TRIGGER source_admission_memory_raw_events_update')
                    elif mutation=='epoch':db.execute('UPDATE memory_source_admission_clock SET instance=?',(self.state['admission']['source_instance'],))
                    elif mutation=='clock':db.execute('UPDATE memory_source_admission_clock SET revision=0')
                    elif mutation=='missing':db.execute('DELETE FROM memory_source_admission')
                    else:db.execute("INSERT INTO memory_source_admission VALUES('raw','unissued-old-id',0,0)")
                with rebuild._read(self.baseline) as base,rebuild._read(copy) as current:
                    with self.assertRaises(ValueError):admission.stage_lineage(self.state,base,current)

    def test_fork_rollback_and_publication_counter_floor_are_transactional(self):
        with sqlite3.connect(self.stage,isolation_level=None) as db:
            before=stamp(db);db.execute('BEGIN IMMEDIATE');admission.fork(db);db.execute('ROLLBACK')
            self.assertEqual(stamp(db),before)
        with sqlite3.connect(self.live) as db:db.execute('UPDATE memory_source_admission_clock SET revision=revision+10000')
        with rebuild._read(self.live) as live,sqlite3.connect(self.stage) as staged:
            admission.prepare_publication(self.state,live,staged)
            after=stamp(staged)
            self.assertGreater(after[0],stamp(live)[0])
            admission.prepare_publication(self.state,live,staged)
            self.assertEqual(stamp(staged),after)

    def test_stale_candidate_and_old_operation_contract_fail_closed(self):
        self.stage_work()
        candidate=self.live.with_name('.'+self.live.name+'.rebuild-'+self.state['operation_id'])
        snapshot_database(self.live,candidate)
        with self.assertRaisesRegex(ValueError,'stale publication candidate'):self.cutover()
        old=dict(self.state);old.pop('admission')
        with self.assertRaisesRegex(ValueError,'lacks admission lineage'):rebuild._fence(old,self.op)
        self.assertEqual(rebuild.raw_digest(self.live),rebuild.raw_digest(self.baseline))

    def test_candidate_and_interrupted_target_trigger_tampering_fail_closed(self):
        self.stage_work()
        original_replace=os.replace
        def interrupted(source,target):
            if Path(target)==self.live:raise RuntimeError('before replace')
            return original_replace(source,target)
        with patch.object(rebuild.os,'replace',interrupted):
            with self.assertRaises(RuntimeError):self.cutover()
        candidate=self.live.with_name('.'+self.live.name+'.rebuild-'+self.state['operation_id'])
        self.write(candidate,'DROP TRIGGER source_admission_memory_raw_events_insert')
        with self.assertRaisesRegex(ValueError,'trigger divergence'):self.cutover()
        # Simulate a closed-writer interruption after the tampered candidate was
        # externally replaced: equal table digests alone must not accept it.
        os.replace(candidate,self.live)
        with self.assertRaisesRegex(ValueError,'trigger divergence'):self.cutover()

    def test_whole_thought_markers_advance_without_rollback(self):
        copy=self.root/'thought-stage.sqlite';snapshot_database(self.stage,copy)
        with MemoryStore(copy) as store:
            thoughts=ThoughtStore(store);thought=thoughts.publish('New staged thought.')
        with rebuild._read(self.stage) as before,rebuild._read(copy) as after:
            admission.lineage(before,after)
        self.write(copy,'UPDATE memory_source_admission SET revision=0 WHERE identity=?',(thought.id,))
        with rebuild._read(self.stage) as before,rebuild._read(copy) as after:
            with self.assertRaisesRegex(ValueError,'Backdated'):admission.lineage(before,after)

    def test_empty_rebuild_uses_real_readiness_cutover_and_confirmation(self):
        empty=self.root/'empty.sqlite'
        with MemoryStore(empty) as store:CaptureQueue(store);ThoughtStore(store)
        config=self.root/'empty-config.json'
        config.write_text(json.dumps({'db':str(empty),'cwd_roots':[str(self.root)]}))
        op=rebuild.start(config,self.root/'empty-operation')
        state=rebuild.status(op)
        # There are zero sources, scopes or naming tasks to regenerate.
        state.update(derive_requested=True,naming_requested=True)
        rebuild._write(op,state)
        ready=rebuild.validate(op)
        self.assertEqual((ready['phase'],ready['issues']),('ready',[]))
        published=rebuild.cutover(op,writer_pids=[],profile_sha256=state['config_sha256'])
        self.assertEqual(published['phase'],'published')
        proof=self.root/'empty-proof.json'
        proof.write_text(json.dumps({'profile_sha256':state['config_sha256'],'source_db':str(empty),
            'topic_projection_policy':rebuild.POLICIES['presentation'],'runtime_rebound':True}))
        self.assertEqual(rebuild.confirm(op,verification=proof)['phase'],'completed')
