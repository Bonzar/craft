"""Retire only incompatible unpublished derived work before frozen hydration."""
import copy
import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from prototypes.assertion_memory.answer_relation_gaps import digest
from prototypes.assertion_memory.derived_work import DerivedWorkQueue
from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.query_snapshot import capture, _restore_frozen
from prototypes.assertion_memory.tests import test_answer_contract_v21 as fixtures


class DerivedPipelineUpgradeTest(unittest.TestCase):
    provider=fixtures.AnswerContractTest.provider
    build=fixtures.AnswerContractTest.build
    relation=fixtures.AnswerContractTest.relation

    def setUp(self):
        fixtures.AnswerContractTest.setUp(self)
        self.now=datetime.now(timezone.utc)
        self.engine=MemoryEngine(self.store,model=self.provider,verify_overviews=True,model_key='synthetic-v22')
        self.new_engine=MemoryEngine(self.store,model=self.provider,verify_overviews=True,model_key='synthetic-new-protocol')
        self.assertNotEqual(self.engine.pipeline_key,self.new_engine.pipeline_key)

    def queue(self):
        return DerivedWorkQueue(self.store,clock=lambda:self.now,lease_seconds=10)

    def row(self):
        with self.store.transaction() as db:
            return dict(db.execute("SELECT * FROM memory_derived_work WHERE scope='s'").fetchone())

    def durable(self):
        with self.store.transaction() as db:
            return {table:[tuple(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                for table in ('memory_views','memory_view_versions','memory_view_checks','memory_capture_jobs',
                              'memory_assertions','memory_edges','memory_raw_events')}

    def seed(self, *, nonempty=True, old_raw=True):
        if nonempty: self.relation()
        view,check=self.build()
        queue=self.queue(); claim=queue.claim(self.engine,with_model=True)
        descriptor=claim.snapshot.descriptor()
        if old_raw and nonempty:
            # Recreate only the old projection shape, with wholly synthetic rows.
            legacy=tuple({key:value for key,value in row.items() if key!='freshness_revision'}
                for row in claim.snapshot.relation_processing_evidence)
            descriptor={**descriptor,'relation_processing_frontier':digest(legacy)}
            self.assertNotEqual(descriptor['relation_processing_frontier'],claim.snapshot.relation_processing_frontier)
        checkpoint={'_scheduler':{'frontier':claim.frontier,'snapshot':descriptor},
            'overview':{'stage':'leaf','accepted':{'unpublished-leaf':{'version_id':123}},
                'failure':{'message':'Synthetic ordinary semantic rejection.'}}}
        result={'root_view_id':view.view_id,'version_id':view.id,'check_id':check['id']}
        completed={'pipeline_key':self.engine.pipeline_key,'generation':0}
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_derived_work SET status='failed',error_count=3,error=?,checkpoint=?,"
                "managed_ids=?,result=?,completed_frontier=?,lease_token=NULL,lease_until=NULL,available_at=? WHERE scope='s'",
                ('Synthetic old failure.',json.dumps(checkpoint),json.dumps(['unpublished-leaf']),
                    json.dumps(result),json.dumps(completed),self.now.isoformat()))
        return descriptor,checkpoint,result,completed

    def test_changed_pipeline_retires_nonterminal_v22_raw_checkpoint_before_hydration(self):
        descriptor,old,result,completed=self.seed(); durable=self.durable(); queue=self.queue()
        with self.assertRaisesRegex(ValueError,'frontier changed'):
            _restore_frozen(self.new_engine,descriptor)
        with patch.object(queue,'_frozen_snapshot',side_effect=AssertionError('old hydration is forbidden')) as hydrate:
            claim=queue.claim(self.new_engine,with_model=True)
        hydrate.assert_not_called()
        self.assertEqual(claim.frontier['pipeline_key'],self.new_engine.pipeline_key)
        self.assertEqual(set(claim.checkpoint),{'_scheduler'})
        self.assertNotEqual(claim.snapshot.descriptor()['relation_processing_frontier'],descriptor['relation_processing_frontier'])
        self.assertEqual(claim.snapshot.corpus(self.new_engine,self.spec),capture(self.new_engine).corpus(self.new_engine,self.spec))
        current=self.row()
        self.assertEqual(json.loads(current['result']),result)
        self.assertEqual(json.loads(current['completed_frontier']),completed)
        self.assertEqual(json.loads(current['managed_ids']),[])
        self.assertEqual(current['status'],'running'); self.assertEqual(current['error_count'],0)
        self.assertEqual(self.durable(),durable)

    def test_pipeline_change_retires_even_empty_or_unknown_owned_legacy_checkpoint(self):
        _,checkpoint,_,_=self.seed(nonempty=False)
        for ownership in ('other-pipeline',None,''):
            with self.subTest(ownership=ownership):
                old=copy.deepcopy(checkpoint)
                if ownership is None: old['_scheduler']['frontier'].pop('pipeline_key')
                else: old['_scheduler']['frontier']['pipeline_key']=ownership
                with self.store.transaction(write=True) as db:
                    db.execute("UPDATE memory_derived_work SET status='pending',checkpoint=?,lease_token=NULL,lease_until=NULL WHERE scope='s'",
                               (json.dumps(old),))
                queue=self.queue()
                with patch.object(queue,'_frozen_snapshot',side_effect=AssertionError('legacy hydration')) as hydrate:
                    claim=queue.claim(self.new_engine,with_model=True)
                hydrate.assert_not_called()
                self.assertEqual(claim.frontier['pipeline_key'],self.new_engine.pipeline_key)
                self.assertNotIn('overview',claim.checkpoint)

    def test_new_raw_desired_frontier_with_same_pipeline_preserves_coalesced_generation(self):
        _,checkpoint,_,_=self.seed(old_raw=False)
        self.store.append_assertion(assertion_id='later',text='New fictional source.',actor='reporter',
            source='fixture://later',episode='later',scope='s',recorded_at=self.now.isoformat())
        self.now+=timedelta(seconds=1)
        queue=self.queue()
        with patch.object(queue,'_frozen_snapshot',wraps=queue._frozen_snapshot) as hydrate:
            claim=queue.claim(self.engine,with_model=True)
        hydrate.assert_called_once()
        self.assertEqual(claim.frontier,checkpoint['_scheduler']['frontier'])
        self.assertEqual(claim.checkpoint['overview'],checkpoint['overview'])
        self.assertNotIn('later',claim.snapshot.assertions)
        self.assertNotEqual(json.loads(self.row()['desired_frontier']),claim.frontier)

    def test_same_pipeline_raw_tamper_still_rejects_hydration_without_mutation(self):
        descriptor,_,_,_=self.seed()
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_derived_work SET status='pending',error_count=0 WHERE scope='s'")
        before=self.row(); durable=self.durable()
        with self.assertRaisesRegex(ValueError,'frontier changed'):
            self.queue().claim(self.engine,with_model=True)
        self.assertEqual(self.row(),before); self.assertEqual(self.durable(),durable)
        with self.assertRaisesRegex(ValueError,'frontier changed'):
            _restore_frozen(self.engine,descriptor)

    def test_changed_pipeline_does_not_steal_unexpired_running_lease(self):
        self.seed()
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_derived_work SET status='running',lease_token='old-owner',lease_until=? WHERE scope='s'",
                ((self.now+timedelta(seconds=30)).isoformat(),))
        before=self.row(); durable=self.durable(); queue=self.queue()
        with patch.object(queue,'_frozen_snapshot',side_effect=AssertionError('active old hydration')) as hydrate:
            self.assertIsNone(queue.claim(self.new_engine,with_model=True))
        hydrate.assert_not_called(); after=self.row()
        for key in ('checkpoint','managed_ids','lease_token','lease_until','status','result','completed_frontier'):
            self.assertEqual(after[key],before[key])
        self.assertEqual(self.durable(),durable)

    def test_changed_pipeline_can_retire_expired_running_checkpoint(self):
        self.seed()
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_derived_work SET status='running',lease_token='expired-owner',lease_until=? WHERE scope='s'",
                ((self.now-timedelta(seconds=1)).isoformat(),))
        queue=self.queue()
        with patch.object(queue,'_frozen_snapshot',side_effect=AssertionError('expired old hydration')):
            claim=queue.claim(self.new_engine,with_model=True)
        self.assertNotEqual(claim.token,'expired-owner')
        self.assertEqual(claim.frontier['pipeline_key'],self.new_engine.pipeline_key)
        self.assertNotIn('overview',claim.checkpoint)

    def test_fresh_capture_failure_rolls_back_retirement_and_preserves_durable_history(self):
        self.seed(); before=self.row(); durable=self.durable(); queue=self.queue()
        with patch('prototypes.assertion_memory.derived_work.capture',side_effect=RuntimeError('Synthetic capture failure.')) as fresh:
            with self.assertRaisesRegex(RuntimeError,'Synthetic capture failure'):
                queue.claim(self.new_engine,with_model=True)
        fresh.assert_called_once()
        self.assertEqual(self.row(),before); self.assertEqual(self.durable(),durable)


    def test_fresh_claim_persistence_failure_rolls_back_without_completing_old_work(self):
        self.seed(); before=self.row(); durable=self.durable(); queue=self.queue()
        with self.store.transaction(write=True) as db:
            db.execute("CREATE TEMP TRIGGER synthetic_claim_abort BEFORE UPDATE OF checkpoint ON memory_derived_work "
                "WHEN new.status='running' BEGIN SELECT RAISE(ABORT,'Synthetic claim persistence failure.'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError,'Synthetic claim persistence failure'):
            queue.claim(self.new_engine,with_model=True)
        self.assertEqual(self.row(),before); self.assertEqual(self.durable(),durable)


if __name__=='__main__': unittest.main()
