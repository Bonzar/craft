"""Invented sources verify upgrade scheduling and published-answer invalidation."""
import json
import unittest
from unittest.mock import patch

from prototypes.assertion_memory import source_coverage, source_reprocessing
from prototypes.assertion_memory.engine import ViewSpec
from prototypes.assertion_memory.worker import LinkWorker
from prototypes.assertion_memory.tests import test_answer_source_gaps as gap_fixture


class SourceGapUpgradeTest(unittest.TestCase):
    setUp=gap_fixture.AnswerSourceGapsTest.setUp
    extracted_gap=gap_fixture.AnswerSourceGapsTest.extracted_gap

    def published_root(self, *, canonical=True, scope='lab'):
        if canonical:
            self.store.append_assertion(assertion_id='lamp',text='The lamp is round.',actor='Mira',
                source='fixture://lamp',episode='known',scope=scope,recorded_at='2026-01-01')
        self.engine.define_view(ViewSpec('root','Known scope','What is reported?',scopes=(scope,)))
        root=self.engine.build_view('root')
        with self.store.transaction(write=True) as db:
            db.execute("INSERT OR IGNORE INTO memory_derived_work(scope,desired_frontier,updated_at,available_at) "
                "VALUES(?, '{\"generation\":0}', '2026-01-01', '2026-01-01')",(scope,))
            db.execute("UPDATE memory_derived_work SET status='idle',completed_frontier=desired_frontier,"
                "result=? WHERE scope=?",(json.dumps({'root_view_id':'root','version_id':root.id}),scope))
        return root

    def derived_row(self, scope='lab'):
        with self.store.transaction() as db:
            row=db.execute('SELECT * FROM memory_derived_work WHERE scope=?',(scope,)).fetchone()
        return dict(row) if row else None

    def publish_unresolved(self, turn='gap'):
        event=self.queue.capture_event('new-gap',turn,'The condition depends on that.',source='fixture://'+turn,
            actor='Mira',scope='lab',recorded_at='2026-01-01')
        def provider(request):
            if request.task.startswith('Extract'): return json.dumps({'assertions':[]})
            if request.task=='Review source material coverage':
                return json.dumps({'source_coverage':{'status':'unresolved','reason':'An antecedent is missing.','omissions':[]}})
            raise AssertionError(request.task)
        worker=LinkWorker(self.queue,self.engine,provider)
        self.assertEqual(worker.run_once().status,'completed')
        self.assertEqual(self.queue.extraction_history(event.id)[-1]['source_revision']['status'],'unresolved')
        return event

    def test_v2_untyped_empty_prefix_receipt_is_automatically_audited_once_after_upgrade(self):
        old_protocol='source-material-coverage-v2'
        with patch.object(source_coverage,'PROTOCOL',old_protocol), patch.object(source_reprocessing,'PROTOCOL',
                'source-reconciliation/v1:'+old_protocol), patch.object(source_coverage,'_terminal_empty_prefix',return_value=None), patch.object(
                    source_coverage,'_operational_error',return_value=None):
            # Simulate the historical controller, which had no operational receipt.
            event,_=self.extracted_gap()
        old=json.dumps(self.queue.extraction_history(event.id),sort_keys=True)
        before=self.store.version
        def provider(request):
            if request.task=='Review source material coverage':
                return json.dumps({'source_coverage':{'status':'unresolved','reason':'The antecedent remains unknown.',
                    'omissions':[{'quote':event.prompt,'reason':'Unresolved antecedent.'}]}})
            if request.task=='Repair omitted source meanings':
                return json.dumps({'context_requests':[{'operation':'search','query':'missing antecedent'}]})
            raise AssertionError(request.task)
        calls=[]
        def measured(request): calls.append(request); return provider(request)
        worker=LinkWorker(self.queue,self.engine,measured)
        for _ in range(20): worker.run_once()
        history=self.queue.extraction_history(event.id)
        self.assertEqual(len(history),2)
        self.assertEqual(json.dumps(history[:-1],sort_keys=True),old)
        self.assertEqual(history[-1]['source_revision']['protocol'],source_reprocessing.PROTOCOL)
        self.assertEqual(history[-1]['context_recovery'][-1]['recovery']['kind'],'empty_authorized_prefix')
        self.assertEqual(history[-1]['source_revision']['status'],'unresolved')
        self.assertFalse(self.service._query_processing_gaps('lab',None,{},freshness_only=True))
        count=len(calls)
        for _ in range(20): worker.run_once()
        self.assertEqual(len(calls),count)
        self.assertEqual(self.store.version,before)  # Still no canonical source or published answer.
        self.assertIsNone(self.derived_row())

    def test_v3_completed_empty_repair_gap_is_automatically_reaudited_once(self):
        old_protocol='source-material-coverage-v3'
        with patch.object(source_coverage,'PROTOCOL',old_protocol), patch.object(source_reprocessing,'PROTOCOL',
                'source-reconciliation/v1:'+old_protocol), patch.object(source_coverage,'_terminal_empty_prefix',return_value=None), patch.object(
                    source_coverage,'_operational_error',return_value=None):
            # Simulate the historical controller, which had no operational receipt.
            event,_=self.extracted_gap(empty_repair=True)
        old=json.dumps(self.queue.extraction_history(event.id),sort_keys=True)
        calls=[]
        def provider(request):
            calls.append(request)
            if request.task=='Review source material coverage':
                return json.dumps({'source_coverage':{'status':'unresolved','reason':'The antecedent remains unknown.',
                    'omissions':[{'quote':event.prompt,'reason':'Unresolved antecedent.'}]}})
            if request.task=='Repair omitted source meanings':
                if sum(r.task==request.task for r in calls)==1: return json.dumps({'assertions':[]})
                return json.dumps({'context_requests':[{'operation':'search','query':'missing antecedent'}]})
            raise AssertionError(request.task)
        worker=LinkWorker(self.queue,self.engine,provider)
        for _ in range(20): worker.run_once()
        history=self.queue.extraction_history(event.id)
        self.assertEqual(len(history),2)
        self.assertEqual(json.dumps(history[:-1],sort_keys=True),old)
        self.assertEqual(history[-1]['source_revision']['protocol'],source_reprocessing.PROTOCOL)
        self.assertEqual(history[-1]['source_revision']['status'],'unresolved')
        self.assertEqual(history[-1]['context_recovery'][-1]['recovery']['kind'],'empty_authorized_prefix')
        count=len(calls)
        for _ in range(20): worker.run_once()
        self.assertEqual(len(calls),count)
        self.assertEqual(self.store.list_assertions(include_unknown_valid=True),[])

    def test_zero_canonical_gap_rearms_an_idle_existing_scope_answer(self):
        root=self.published_root()
        before=self.store.version
        old=self.derived_row()
        event=self.publish_unresolved()
        row=self.derived_row()
        self.assertEqual(self.store.version,before+1)
        self.assertEqual(row['status'],'pending')
        desired=json.loads(row['desired_frontier'])
        self.assertGreater(desired['generation'],json.loads(old['desired_frontier'])['generation'])
        self.assertEqual(desired['source_revision_watermark'],self.queue.extraction_history(event.id)[-1]['id'])
        self.assertEqual(row['completed_frontier'],old['completed_frontier'])
        self.assertEqual(self.engine.get_version(root.id),root)
        self.assertEqual({a.assertion_id for a in self.store.list_assertions(include_unknown_valid=True)}, {'lamp'})

    def test_published_scoped_answer_without_any_canonical_still_rearms_on_new_gap(self):
        self.published_root(canonical=False)
        before=self.store.version
        self.publish_unresolved()
        self.assertEqual(self.store.version,before+1)
        self.assertEqual(self.derived_row()['status'],'pending')
        self.assertEqual(self.store.list_assertions(include_unknown_valid=True),[])

    def test_existing_canonical_scope_without_published_root_rearms_on_new_gap(self):
        self.store.append_assertion(assertion_id='lamp',text='The lamp is round.',actor='Mira',
            source='fixture://lamp',episode='known',scope='lab',recorded_at='2026-01-01')
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_derived_work SET status='idle',completed_frontier=desired_frontier")
        before=self.store.version
        self.publish_unresolved()
        self.assertEqual(self.store.version,before+1)
        self.assertEqual(self.derived_row()['status'],'pending')

    def test_empty_new_or_unpublished_or_other_scope_gets_no_fake_overview(self):
        for state in ('empty','unpublished','other_scope'):
            with self.subTest(state=state):
                self.setUp()
                if state=='unpublished':
                    self.engine.define_view(ViewSpec('draft','Draft','What is reported?',scopes=('lab',)))
                elif state=='other_scope': self.published_root(scope='other')
                before=self.store.version
                self.publish_unresolved()
                self.assertEqual(self.store.version,before)
                self.assertIsNone(self.derived_row())
                self.assertFalse(any(view.id=='root' and 'lab' in view.scopes for view in self.engine.list_views()))

    def test_identical_zero_canonical_publication_is_a_noop_for_idle_scope_work(self):
        self.published_root()
        event=self.publish_unresolved()
        original=self.queue.extraction_history(event.id)
        frozen=self.queue.freeze_input(event.id)
        result={'assertions':[],'reviews':original[-1]['reviews'],
            'context_event_ids':original[-1]['context_event_ids'],'context_truncated':False,
            'context_recovery':original[-1]['context_recovery'],'input_fingerprint':frozen.fingerprint}
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_derived_work SET status='idle',completed_frontier=desired_frontier")
            db.execute("UPDATE memory_capture_jobs SET status='pending',phase='audit' WHERE event_id=?",(event.id,))
        before=self.store.version; work=self.derived_row()
        claim=self.queue._claim(with_model=True,maintenance_only=True)
        self.queue._finish_extraction(claim,event,result)
        self.assertEqual(self.queue.extraction_history(event.id),original)
        self.assertEqual(self.store.version,before)
        self.assertEqual(self.derived_row(),work)
