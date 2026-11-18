"""Synthetic ledger fixtures test selection, not model reprocessing quality."""
import json
import unittest
from dataclasses import asdict

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.query_snapshot import capture, _restore_frozen
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.discovery_frontier import corpus_state


class SourceRevisionSelectionTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.queue = CaptureQueue(self.store)
        self.engine = MemoryEngine(self.store)
        self.event = self.queue.capture_event('fiction', 'one',
            'The assistant suggested blue. Noor rejected blue and selected green. The lamp is round.',
            actor='Noor', source='fixture://one', scope='garden', recorded_at='2026-01-01')
        for key in ('blue', 'round', 'green', 'direct'):
            self.store.append_assertion(assertion_id=key, text=key+' lamp', actor='Noor',
                source='fixture://one', episode='fiction', scope='garden',
                recorded_at='2026-01-01', metadata={'event_id': self.event.id,'interpretation':{
                    'status':'model_verified','source_coverage':{'status':'unresolved','protocol':'legacy-v1',
                    'reason':'NEVER_COPY_SOURCE_AUDIT_REASON'}}})
        self.run_result({'assertion_ids':['blue','round']}, '2026-01-02')

    def run_result(self, result, at):
        with self.store.transaction(write=True) as connection:
            cursor = connection.execute('INSERT INTO memory_extraction_runs(event_id,fingerprint,completed_at,result) '
                'VALUES(?,?,?,?)', (self.event.id, str(connection.total_changes), at+'T00:00:00.000000+00:00',json.dumps(result)))
            return cursor.lastrowid

    def manifest(self, ids, decisions, status='accepted_complete'):
        return {'source_revision':{'schema':'source-interpretation/v1','event_id':self.event.id,
            'protocol':'fixture-v2','input_fingerprint':'synthetic-target-context-v2','status':status,
            'assertion_ids':ids,'reconciliation':[{'assertion_id':key,'decision':decision,
                'verification':{'status':'accepted','verifier':'fixture-independent-review',
                    'reason':'Exact invented source was independently checked.',
                    'evidence_refs':[{'event_id':self.event.id,'start':0,'end':len(self.event.prompt)}]}}
                for key,decision in decisions.items()]}}

    def keys(self, at=None):
        return set(capture(self.engine,recorded_as_of=at,captured_at='2026-02-01T00:00:00+00:00').assertions)

    def replace(self, at='2026-01-04'):
        return self.run_result(self.manifest(['round','green'],
            {'blue':'withdrawn','round':'retained','green':'introduced'}),at)

    def test_failed_partial_preserves_prior_then_specific_withdrawal_then_complete(self):
        self.run_result(self.manifest([],{},'failed'),'2026-01-03')
        self.assertEqual(self.keys('2026-01-03'),{'blue','round','green','direct'})
        self.run_result(self.manifest([],{'blue':'withdrawn'},'unresolved'),'2026-01-04')
        self.assertEqual(self.keys('2026-01-04'),{'round','green','direct'})
        self.run_result(self.manifest(['round','green'],{'round':'retained','green':'introduced'}),'2026-01-05')
        self.assertEqual(self.keys('2026-01-05'),{'round','green','direct'})
        self.assertEqual(self.keys('2026-01-03'),{'blue','round','direct'})
        self.assertIsNotNone(self.store.get_assertion('blue'))

    def test_replacement_filters_fts_before_limit_counts_closure_and_old_roots(self):
        self.store.append_edge(edge_id='old',from_id='direct',to_id='blue',kind='depends_on',recorded_at='2026-01-02')
        self.replace()
        self.assertEqual({hit.assertion.assertion_id for hit in self.store.search('blue green',limit=1)}, {'green'})
        self.assertEqual(self.store.list_edges(),[])
        self.assertEqual(self.store.scope_version('garden',follow_kinds='depends_on'),3)
        snapshot=capture(self.engine,captured_at='2026-02-01T00:00:00+00:00')
        records,edges,_,_=corpus_state(ViewSpec('old-topic','Old','Lamp?',scopes=('elsewhere',),root_ids=('blue',)),snapshot)
        self.assertEqual(records,[])
        self.assertEqual(edges,[])
        self.assertEqual(set(snapshot.assertions),{'round','green','direct'})

    def test_accepted_empty_does_not_remove_direct_metadata_lookalike(self):
        self.run_result(self.manifest([],{'blue':'withdrawn','round':'withdrawn'}),'2026-01-04')
        self.assertEqual(self.keys(),{'green','direct'})
        self.assertEqual(self.keys('2026-01-03'),{'blue','round','green','direct'})

    def test_frozen_watermark_restores_same_time_old_rows_and_edges(self):
        self.store.append_edge(edge_id='old',from_id='direct',to_id='blue',kind='depends_on',recorded_at='2026-01-02')
        self.run_result(self.manifest([],{},'failed'),'2026-01-04')
        before=capture(self.engine,recorded_as_of='2026-01-04',captured_at='2026-02-01T00:00:00+00:00')
        self.replace()
        restored=_restore_frozen(self.engine,before.descriptor())
        self.assertEqual(asdict(restored),asdict(before))
        self.assertIn('source_revision_watermark',before.descriptor())
        self.assertEqual({h.assertion.assertion_id for h in self.store.search('blue',
            assertion_watermark=before.assertion_watermark,**before.limits)}, {'blue'})
        self.assertNotIn('blue',self.keys('2026-01-04'))

    def test_old_descriptor_explicitly_restores_legacy_membership(self):
        before=capture(self.engine,captured_at='2026-02-01T00:00:00+00:00')
        descriptor=before.descriptor()
        descriptor.pop('source_revision_watermark',None)
        self.replace()
        restored=_restore_frozen(self.engine,descriptor)
        self.assertEqual(set(restored.assertions),{'blue','round','green','direct'})
        self.assertIsNone(restored.source_revision_watermark)

    def test_later_complete_can_reintroduce_only_with_explicit_verification(self):
        self.replace()
        self.run_result(self.manifest(['blue','round'],{'blue':'introduced','round':'retained','green':'withdrawn'}),'2026-01-05')
        self.assertEqual(self.keys(),{'blue','round','direct'})
        self.assertEqual(self.keys('2026-01-04'),{'round','green','direct'})

    def test_membership_only_dirty_contract_advances_version_and_frontier(self):
        from prototypes.assertion_memory.source_revision import mark_revision_changed
        before=self.store.version
        with self.store.transaction(write=True) as connection:
            mark_revision_changed(connection,self.event.id,'2026-01-04')
            frontier=json.loads(connection.execute('SELECT desired_frontier FROM memory_derived_work WHERE scope=?',('garden',)).fetchone()[0])
        self.assertGreater(self.store.version,before)
        self.assertIn('source_revision_watermark',frontier)

    def test_publication_validator_rejects_other_events_owned_assertion(self):
        from prototypes.assertion_memory.source_revision import validate_result
        other=self.queue.capture_event('fiction','two','A separate invented event.',
            actor='Noor',source='fixture://two',scope='garden',recorded_at='2026-01-01')
        result=self.manifest(['blue'],{'blue':'introduced'})
        result['source_revision']['event_id']=other.id
        result['source_revision']['reconciliation'][0]['verification']['evidence_refs']=[
            {'event_id':other.id,'start':0,'end':len(other.prompt)}]
        with self.assertRaises(ValueError):
            validate_result(self.store._connection,other.id,result,prior_ids=set())

    def test_immutable_view_bytes_survive_revision_and_current_summary_rebuild(self):
        self.engine.define_view(ViewSpec('lamps','Lamps','What about lamps?',scopes=('garden',),max_seeds=10))
        before=self.engine.build_view('lamps')
        with self.store.transaction() as connection:
            original=connection.execute('SELECT snapshot FROM memory_view_versions WHERE id=?',(before.id,)).fetchone()[0]
        self.replace()
        after=self.engine.build_view('lamps')
        self.assertNotIn('blue', {row['id'] for row in after.evidence})
        self.assertNotEqual(before.id,after.id)
        with self.store.transaction() as connection:
            self.assertEqual(connection.execute('SELECT snapshot FROM memory_view_versions WHERE id=?',(before.id,)).fetchone()[0],original)

    def test_inspect_coverage_omits_retired_review_but_preserves_review_history(self):
        self.engine.define_view(ViewSpec('outside','Outside','Lamp?',scopes=('other-scope',),root_ids=('blue',)))
        with self.store.transaction(write=True) as connection:
            for key in ('blue','direct'):
                connection.execute("INSERT INTO memory_branch_reviews VALUES ('outside',?,'accepted','fixture',"
                    "'Independently accepted branch relevance','2026-01-02T00:00:00.000000+00:00','{}')",(key,))
        history=[asdict(row) for row in self.engine.list_branch_reviews('outside')]
        self.assertEqual(self.engine.inspect_view('outside')['source_version'],4)
        self.run_result(self.manifest([],{'blue':'withdrawn'},'unresolved'),'2026-01-04')
        self.assertEqual(self.engine.inspect_view('outside')['source_version'],2,
                         'only the current direct assertion and its valid review count')
        self.run_result(self.manifest([],{'round':'withdrawn'}),'2026-01-05')
        self.assertEqual(self.engine.inspect_view('outside')['source_version'],2)
        self.assertIsNotNone(self.store.get_assertion('blue'))
        self.assertEqual([asdict(row) for row in self.engine.list_branch_reviews('outside')],history)
        self.assertIn('blue',self.queue.extraction_history(self.event.id)[0]['assertion_ids'])

    def test_first_source_unresolved_verified_claim_is_current_with_honest_gap(self):
        before=capture(self.engine,captured_at='2026-02-01T00:00:00+00:00')
        self.event=self.queue.capture_event('fiction','first-partial','An invented square lamp.',
            actor='Noor',source='fixture://partial',scope='garden',recorded_at='2026-01-01')
        self.store.append_assertion(assertion_id='fresh',text='The lamp is square.',actor='Noor',
            source='fixture://partial',episode='fiction',scope='garden',recorded_at='2026-01-01')
        self.run_result(self.manifest(['fresh'],{'fresh':'introduced'},'unresolved'),'2026-01-04')
        current=capture(self.engine,captured_at='2026-02-01T00:00:00+00:00')
        self.assertIn('fresh',current.assertions)
        self.assertEqual(current.source_revision_evidence[-1][3]['status'],'unresolved')
        self.assertNotIn('fresh',self.keys('2026-01-03'))
        self.assertEqual(asdict(_restore_frozen(self.engine,before.descriptor())),asdict(before))

    def test_unresolved_retained_subset_never_implicitly_removes_other_prior_truth(self):
        self.run_result(self.manifest(['blue'],{'blue':'retained'},'unresolved'),'2026-01-04')
        self.assertEqual(self.keys(),{'blue','round','green','direct'})

    def test_unresolved_verified_addition_and_withdrawal_preserve_other_prior_truth(self):
        before=capture(self.engine,recorded_as_of='2026-01-03',captured_at='2026-02-01T00:00:00+00:00')
        self.run_result(self.manifest(['green'],{'green':'introduced','blue':'withdrawn'},'unresolved'),'2026-01-04')
        self.assertEqual(self.keys(),{'round','green','direct'})
        self.assertEqual(self.keys('2026-01-03'),{'blue','round','direct'})
        self.assertEqual(asdict(_restore_frozen(self.engine,before.descriptor())),asdict(before))
        self.assertIsNotNone(self.store.get_assertion('blue'))

    def test_unresolved_addition_requires_accepted_independent_proof_and_failed_cannot_add(self):
        from prototypes.assertion_memory.source_revision import validate_result
        invalid=[self.manifest(['green'],{},'unresolved'),
                 self.manifest(['green'],{'green':'introduced'},'failed'),
                 self.manifest(['green'],{'green':'introduced'},'unresolved')]
        invalid[-1]['source_revision']['reconciliation'][0]['verification']['status']='unresolved'
        for result in invalid:
            with self.subTest(result=result):
                with self.assertRaises(ValueError):
                    validate_result(self.store._connection,self.event.id,result,prior_ids={'blue','round'})

    def projected_requests(self,snapshot,key='round'):
        from prototypes.assertion_memory.overview_review import (
            _writer_request,_make_review_request,_query_frame,RetrievalLedger)
        spec=ViewSpec('lamps','Lamps','What about lamps?',scopes=('garden',))
        records=[{**snapshot.assertions[key],'disposition':'assertion'}]
        writer=_writer_request(spec,records,[],[],{},None,False)
        rows=[row for row in writer.records if row.get('id')]
        ledger=RetrievalLedger(None,snapshot.assertions,{key})
        reviewer=_make_review_request(rows,[],'The lamp is round [round].',
            _query_frame(spec,{},'fixture'),ledger,'global')
        return writer,reviewer

    def test_current_revalidation_reaches_writer_reviewer_without_rewriting_creation_or_old_views(self):
        original=asdict(self.store.get_assertion('round'))
        self.engine.define_view(ViewSpec('lamps','Lamps','What about lamps?',scopes=('garden',)))
        old=self.engine.build_view('lamps')
        with self.store.transaction() as connection:
            old_bytes=connection.execute('SELECT snapshot FROM memory_view_versions WHERE id=?',(old.id,)).fetchone()[0]
        before=capture(self.engine,captured_at='2026-02-01T00:00:00+00:00')
        for request in self.projected_requests(before):
            row=next(row for row in request.records if row.get('id')=='round')
            self.assertEqual(row['metadata']['interpretation']['source_coverage']['status'],'unresolved')
            self.assertNotIn('current_source_revalidation',row)
        run=self.replace()
        after=capture(self.engine,captured_at='2026-02-01T00:00:00+00:00')
        expected={'event_id':self.event.id,'source_run_id':run,'source_status':'accepted_complete',
            'protocol':'fixture-v2','checked_at':'2026-01-04T00:00:00.000000+00:00',
            'last_positive_claim_run_id':run,'last_positive_source_status':'accepted_complete'}
        for request in self.projected_requests(after):
            row=next(row for row in request.records if row.get('id')=='round')
            self.assertEqual(row.get('current_source_revalidation'),expected)
            self.assertEqual(row['metadata']['interpretation']['source_coverage']['status'],'unresolved')
            self.assertIn('current_source_revalidation',request.instructions)
            self.assertNotIn('NEVER_COPY_SOURCE_AUDIT_REASON',json.dumps(request.records))
            self.assertNotIn('Exact invented source was independently checked.',json.dumps(request.records))
            self.assertEqual(row['recorded_at'],original['recorded_at'])
        self.assertEqual(asdict(self.store.get_assertion('round')),original)
        self.assertNotIn('current_source_revalidation',after.assertions['direct'])
        with self.store.transaction() as connection:
            self.assertEqual(connection.execute('SELECT snapshot FROM memory_view_versions WHERE id=?',(old.id,)).fetchone()[0],old_bytes)

    def test_revalidation_projection_respects_recorded_cutoff_and_original_frozen_watermark(self):
        before=capture(self.engine,recorded_as_of='2026-01-03',captured_at='2026-02-01T00:00:00+00:00')
        self.replace()
        historical=capture(self.engine,recorded_as_of='2026-01-03',captured_at='2026-02-01T00:00:00+00:00')
        self.assertNotIn('current_source_revalidation',historical.assertions['round'])
        self.assertEqual(asdict(_restore_frozen(self.engine,before.descriptor())),asdict(before))
        current=capture(self.engine,captured_at='2026-02-01T00:00:00+00:00')
        self.assertIn('current_source_revalidation',current.assertions['round'])
        self.assertEqual(asdict(_restore_frozen(self.engine,current.descriptor())),asdict(current))

    def test_latest_partial_and_failed_outcomes_preserve_prior_positive_claim_attestations(self):
        complete=self.replace()
        partial=self.run_result(self.manifest(['direct'],{'direct':'introduced'},'unresolved'),'2026-01-05')
        failed=self.run_result(self.manifest([],{},'failed'),'2026-01-06')
        snapshot=capture(self.engine,captured_at='2026-02-01T00:00:00+00:00')
        for key,positive,status in (('round',complete,'accepted_complete'),('direct',partial,'unresolved')):
            self.assertIn(key,snapshot.assertions)
            row=next(row for row in self.projected_requests(snapshot,key)[0].records if row.get('id')==key)
            current=row.get('current_source_revalidation')
            self.assertIsNotNone(current)
            self.assertEqual(current['source_status'],'failed')
            self.assertEqual(current['source_run_id'],failed)
            self.assertEqual(current['last_positive_claim_run_id'],positive)
            self.assertEqual(current['last_positive_source_status'],status)
        self.assertNotIn('blue',snapshot.assertions)

    def test_unreviewed_request_also_distinguishes_current_inventory_from_creation_coverage(self):
        from prototypes.assertion_memory.overview import _request
        self.replace()
        snapshot=capture(self.engine,captured_at='2026-02-01T00:00:00+00:00')
        record={**snapshot.assertions['round'],'disposition':'assertion'}
        request=_request(ViewSpec('lamp','Lamp','What about the lamp?'),[record],[],[])
        self.assertEqual(request.records[0]['current_source_revalidation']['source_status'],'accepted_complete')
        self.assertIn('current_source_revalidation',request.instructions)

    def test_untrusted_revision_omission_or_bad_proof_fails_closed(self):
        from prototypes.assertion_memory.source_revision import validate_result
        for result in (self.manifest([],{}), self.manifest(['round'],{'round':'retained','blue':'withdrawn'})):
            if result['source_revision']['reconciliation']:
                result['source_revision']['reconciliation'][0]['verification']['evidence_refs'][0]['end']=100000
            with self.subTest(result=result):
                with self.assertRaises(ValueError):
                    validate_result(self.store._connection,self.event.id,result,prior_ids={'blue','round'})


if __name__=='__main__':
    unittest.main()
