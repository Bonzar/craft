"""Synthetic settled-LINK regression; never imports a captured user database."""
import copy
import json
import unittest
from dataclasses import replace

from prototypes.assertion_memory.answer_applicability import frontier
from prototypes.assertion_memory.query_snapshot import capture, _restore_frozen, freshness
from prototypes.assertion_memory.tests.test_answer_contract_v21 import AnswerContractTest
from prototypes.assertion_memory.tests.test_answer_applicability import ApplicabilityTest
from prototypes.assertion_memory.tests.test_navigation_relation_boundary import NavigationRelationBoundaryTest


class RelationFreshnessTest(unittest.TestCase):
    setUp = AnswerContractTest.setUp
    provider = AnswerContractTest.provider
    build = AnswerContractTest.build
    result = AnswerContractTest.result
    alias = ApplicabilityTest.alias

    def settled(self, source='permit', target='garden'):
        # Recreate the observed count and closed dispositions, not private data.
        proposals=[]
        for index in range(13):
            status=('applied','existing','rejected')[index % 3]
            proposal={'kind':'depends_on','from_id':source,'to_id':target,
                'target_id':target,'source_quote':self.store.get_assertion(source).text,
                'target_quote':self.store.get_assertion(target).text,
                'condition':'Both reported conditions remain required: '+str(index),
                'reason':'Independent synthetic verification retains the complete condition.',
                'status':status,'verification':{'status':'unsupported' if status=='rejected' else 'accepted',
                    'reason':'Synthetic proof for the exact complete candidate.', 'version':1}}
            if status!='rejected':
                proposal['edge_id']='synthetic-proof-'+str(index)
                self.store.append_edge(edge_id=proposal['edge_id'],from_id=source,to_id=target,
                    kind=proposal['kind'],recorded_at='2026-01-02',metadata={'condition':proposal['condition']})
            proposals.append(proposal)
        with self.store.transaction(write=True) as db:
            db.execute('INSERT INTO memory_capture_jobs(id,assertion_id,status,phase,candidates,proposals,updated_at,available_at) '
                'VALUES(?,?,?,?,?,?,?,?)',('settled',source,'running','refresh',
                    json.dumps([{'id':target,'score':1.0,'method':'fixture'}]),json.dumps(proposals),
                    '2026-01-02T00:00:00+00:00','2026-01-02'))
        return proposals

    def update(self, **changes):
        with self.store.transaction(write=True) as db:
            db.execute('UPDATE memory_capture_jobs SET '+','.join(key+'=?' for key in changes)+' WHERE id=?',
                (*[json.dumps(value) if key in ('candidates','proposals') else value for key,value in changes.items()], 'settled'))

    def complete(self):
        self.update(status='completed',phase='done',updated_at='2026-01-02T00:00:00.009063+00:00')

    def test_thirteen_closed_proposals_cleanup_preserves_view_corpus_and_exact_immutable_check(self):
        self.settled(); before=capture(self.engine); view,check=self.build()
        saved=copy.deepcopy(check); summary=view.summary
        self.complete(); after=capture(self.engine)
        self.assertEqual(before.relation_gaps(('s',))['total'],0)
        self.assertEqual(len(after.relation_processing_evidence),13)
        self.assertNotEqual(before.relation_processing_frontier,after.relation_processing_frontier)
        self.assertNotEqual(before.relation_gaps(('s',))['frontier_sha256'],after.relation_gaps(('s',))['frontier_sha256'])
        self.assertEqual(before.corpus(self.engine,self.spec),after.corpus(self.engine,self.spec))
        self.assertFalse(freshness(self.engine,self.spec,before)['refresh_required'])
        self.assertFalse(self.result(view,check)['refresh_required'])
        self.assertNotEqual(self.engine.inspect_view(self.spec.id)['status'],'stale')
        self.assertEqual(self.engine.latest_check(view),saved)
        self.assertEqual(self.engine.get_version(view.id).summary,summary)

    def test_existing_alias_stays_warm_zero_model_calls_with_same_body_and_proof(self):
        self.settled(); view,base=self.build(); first=self.alias()
        self.complete(); self.requests=[]; second=self.alias()
        self.assertTrue(second['answer_accepted']); self.assertFalse(second['refresh_required'])
        self.assertEqual(self.requests,[])
        self.assertEqual(second['view']['summary'],view.summary)
        self.assertEqual(second['answer_certificate']['check_id'],first['answer_certificate']['check_id'])
        self.assertEqual(self.engine.latest_check(view),base)

    def test_link_errors_waiting_source_jobs_and_rearmed_done_retain_operational_identity(self):
        self.settled(); stable=capture(self.engine).corpus(self.engine,self.spec)
        for changes in ({'phase':'link','status':'pending'}, {'phase':'link','status':'running'},
                {'phase':'link','status':'failed','error':'Synthetic failure'},
                {'phase':'refresh','status':'running','error':'Synthetic failure'},
                {'phase':'refresh','status':'running','waiting_model':1},
                {'phase':'done','status':'pending'}, {'phase':'done','status':'running'},
                {'phase':'done','status':'completed','waiting_model':1}):
            self.update(error=None,waiting_model=0,**{key:value for key,value in changes.items() if key not in ('error','waiting_model')})
            self.update(**changes)
            with self.subTest(changes=changes):
                self.assertNotEqual(stable,capture(self.engine).corpus(self.engine,self.spec))
        self.complete(); completed=capture(self.engine)
        self.update(status='waiting_model',phase='done')
        claim=self.service.queue._claim(with_model=True)
        self.assertEqual(claim.job.phase,'link')
        self.assertEqual(claim.job.status,'running')
        self.assertNotEqual(completed.corpus(self.engine,self.spec),capture(self.engine).corpus(self.engine,self.spec))
        # Raw/source work cannot use the assertion-only predicate.
        event=self.service.queue.capture_event('raw','raw','Raw source audit.',source='fixture://raw',scope='s')
        self.update(event_id=event.id,assertion_id=None,phase='refresh',status='running',waiting_model=0,error=None)
        before=capture(self.engine); self.complete()
        self.assertNotEqual(before.corpus(self.engine,self.spec),capture(self.engine).corpus(self.engine,self.spec))

    def test_redacted_shape_distribution_uses_only_assertion_bookkeeping_normalization(self):
        # Shape/count conformance only; every identity and body is newly fictional.
        from collections import Counter
        templates=self.settled()
        layouts=[ [('contradicts','rejected')], [('realizes','applied')], [('supports','applied')],
            [('realizes','applied'),('supports','applied')], [('realizes','existing'),('realizes','existing')],
            [('supersedes','rejected'),('realizes','applied')], [('supports','applied')],
            [('realizes','existing')], [('supports','applied'),('supports','applied')] ]
        with self.store.transaction(write=True) as db:
            db.execute("DELETE FROM memory_capture_jobs WHERE id='settled'")
            for index,layout in enumerate(layouts):
                proposals=[]
                for offset,(kind,status) in enumerate(layout):
                    proposal=copy.deepcopy(templates[index+offset]); proposal.update(kind=kind,status=status)
                    proposal.pop('condition'); proposal.pop('reason'); proposal.pop('verification')
                    if status=='rejected':
                        proposal.pop('edge_id',None)
                        proposal.update(reason='Independent rejection of synthetic meaning.',basis='reported_decision',
                            proposed_basis='reported_decision',verification={'status':'unsupported',
                            'reason':'The fictional source does not establish the proposed change.','version':1})
                    else:
                        proposal['edge_id']='shape-edge-'+str(index)+'-'+str(offset)
                        self.store.append_edge(edge_id=proposal['edge_id'],from_id=proposal['from_id'],
                            to_id=proposal['to_id'],kind=kind,recorded_at='2026-01-02')
                    proposals.append(proposal)
                db.execute('INSERT INTO memory_capture_jobs(id,assertion_id,status,phase,candidates,proposals,updated_at,available_at) '
                    'VALUES(?,?,?,?,?,?,?,?)',('shape-'+str(index),'permit','running','refresh',
                    json.dumps([{'id':'garden','score':0.75,'method':'fixture'}]),json.dumps(proposals),'2026-01-02','2026-01-02'))
        for group,count in enumerate((5,3,4)):
            event=self.service.queue.capture_event('shape-source',str(group),'Wholly fictional source.',
                source='fixture://shape-source-'+str(group),scope='s',recorded_at='2026-01-01')
            proposals=[{'kind':'interpretation','status':'accepted','index':index,'text':'Invented claim.',
                'reason':'Synthetic interpretation proof.','evidence_refs':[{'event_id':event.id,
                'start':0,'end':6,'role':'user','relation':'source'}]} for index in range(count)]
            with self.store.transaction(write=True) as db:
                db.execute("UPDATE memory_capture_jobs SET status='completed',phase='done',proposals=?,updated_at=? WHERE event_id=?",
                    (json.dumps(proposals),'2026-01-02',event.id))
        before=capture(self.engine)
        self.assertEqual(Counter(row['kind'] for row in before.relation_processing_evidence),
            {'realizes':6,'supports':5,'contradicts':1,'supersedes':1})
        self.assertEqual(Counter(row['status'] for row in before.relation_processing_evidence),
            {'applied':8,'existing':3,'rejected':2})
        with self.store.transaction(write=True) as db:
            rows=db.execute('SELECT event_id,proposals FROM memory_capture_jobs').fetchall()
            self.assertEqual(len(rows),12)
            self.assertEqual(sum(len(json.loads(row['proposals'])) for row in rows),25)
            db.execute("UPDATE memory_capture_jobs SET status='completed',phase='done',updated_at='2026-01-03' WHERE assertion_id IS NOT NULL")
        after=capture(self.engine)
        self.assertNotEqual(before.relation_processing_frontier,after.relation_processing_frontier)
        self.assertEqual(before.corpus(self.engine,self.spec),after.corpus(self.engine,self.spec))
        # Source jobs remain independently observable even when their interpretation is accepted.
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_capture_jobs SET status='running',phase='audit',updated_at='2026-01-04' WHERE event_id IS NOT NULL")
        self.assertTrue(self.service._query_processing_gaps('s',None,self.service.status(scope='s')))

    def test_candidate_proposal_condition_disposition_verification_and_scope_changes_are_significant(self):
        proposals=self.settled(); before=capture(self.engine)
        original=[{'id':'garden','score':1.0,'method':'fixture'}]
        self.update(candidates=original+[{'id':'serial','score':0.5,'method':'fixture'}]); self.complete()
        self.assertNotEqual(before.corpus(self.engine,self.spec),capture(self.engine).corpus(self.engine,self.spec))
        self.update(candidates=original)
        for field,value in [('condition','New meaning.'),('status','existing'),('to_id','serial'),
                            ('reason','Changed rejection basis.'),('kind','supports'),
                            ('verification',{'status':'unsupported','reason':'Changed independent proof.','version':1})]:
            changed=copy.deepcopy(proposals); changed[0][field]=value; self.update(proposals=changed)
            with self.subTest(field=field):
                self.assertNotEqual(before.corpus(self.engine,self.spec),capture(self.engine).corpus(self.engine,self.spec))
        self.update(proposals=proposals,assertion_id='serial')
        self.assertNotEqual(before.corpus(self.engine,self.spec),capture(self.engine).corpus(self.engine,self.spec))

    def test_pending_unknown_or_incomplete_containers_never_become_inert_even_when_total_zero(self):
        proposals=self.settled()
        malformed=[]
        for changes in ({'status':'pending'}, {'status':'unknown'}, {'to_id':None}, {'to_id':'missing'},
                {'verification':None}, {'verification':{'status':'unknown','reason':'Unknown proof.'}},
                {'source_quote':None}, {'verification_required':True}):
            changed=copy.deepcopy(proposals); changed[0].update(changes); malformed.append({'proposals':changed})
        malformed.extend([{'candidates':{}},{'candidates':[{}]}, {'candidates':[{'id':'missing'}]},
                          {'proposals':proposals+[{'kind':'branch','status':'pending'}]}])
        for change in malformed:
            self.update(proposals=proposals,candidates=[{'id':'garden','score':1.0,'method':'fixture'}],
                phase='refresh',status='running',updated_at='2026-01-02')
            self.update(**change); before=capture(self.engine); self.complete()
            with self.subTest(change=change):
                self.assertNotEqual(before.corpus(self.engine,self.spec),capture(self.engine).corpus(self.engine,self.spec))

    def test_closed_disposition_requires_complete_verification_and_actual_edge_identity(self):
        proposals=self.settled()
        for change in ('missing_verification','missing_version','missing_edge','wrong_edge'):
            changed=copy.deepcopy(proposals)
            if change=='missing_verification': changed[0].pop('verification')
            elif change=='missing_version': changed[0]['verification'].pop('version')
            elif change=='missing_edge': changed[0]['edge_id']='unknown-edge'
            else: changed[0]['edge_id']=changed[1]['edge_id']
            self.update(proposals=changed,phase='refresh',status='running',updated_at='2026-01-02')
            before=capture(self.engine); self.complete()
            with self.subTest(change=change):
                self.assertNotEqual(before.corpus(self.engine,self.spec),capture(self.engine).corpus(self.engine,self.spec))

    def test_canonical_edges_raw_sources_and_real_concurrent_link_meaning_still_mark_stale(self):
        proposals=self.settled(); view,check=self.build(); self.complete()
        self.store.append_edge(edge_id='real-new-edge',from_id='permit',to_id='garden',kind='depends_on',
            recorded_at='2026-01-03',metadata={'condition':'New independently accepted condition.'})
        self.assertEqual(self.engine.inspect_view(self.spec.id)['status'],'stale')
        view,check=self.build()
        self.service.queue.capture_event('unread','unread','Potential new condition.',source='fixture://unread',scope='s')
        self.assertTrue(self.result(view,check)['refresh_required'])
        # A fresh complete corpus pins sources, then a real new LINK proposal must diverge.
        before=capture(self.engine); changed=copy.deepcopy(proposals)
        changed.append({**changed[0],'condition':'New simultaneous prerequisite.','status':'pending'})
        self.update(phase='link',status='running',proposals=changed)
        self.assertNotEqual(before.corpus(self.engine,self.spec),capture(self.engine).corpus(self.engine,self.spec))

    def test_historical_restore_remains_raw_fail_closed_and_protocol_changes_pipeline(self):
        from unittest.mock import patch
        self.settled(); before=capture(self.engine); self.complete()
        with self.assertRaisesRegex(ValueError,'frontier changed'):
            _restore_frozen(self.engine,before.descriptor())
        historical=capture(self.engine,recorded_as_of='2026-01-02T00:00:00+00:00')
        self.assertEqual(historical.relation_processing_evidence,())
        from prototypes.assertion_memory.engine import MemoryEngine
        from prototypes.assertion_memory import answer_relation_gaps
        current=MemoryEngine(self.store,model=self.provider,verify_overviews=True).pipeline_key
        with patch.object(answer_relation_gaps,'FRESHNESS_PROTOCOL','legacy-test-protocol',create=True):
            legacy=MemoryEngine(self.store,model=self.provider,verify_overviews=True).pipeline_key
        self.assertNotEqual(current,legacy)

    def test_refresh_pending_running_done_are_equivalent_but_unknown_identity_is_not(self):
        self.settled(); before=capture(self.engine)
        for phase,status in [('refresh','pending'),('refresh','running'),('done','completed')]:
            self.update(phase=phase,status=status,updated_at='2026-01-03')
            self.assertEqual(before.corpus(self.engine,self.spec),capture(self.engine).corpus(self.engine,self.spec))
        self.store.append_assertion(assertion_id='other-scope',text='Other scope.',actor='reporter',
            source='fixture://other-scope',episode='other-scope',scope='other',recorded_at='2026-01-01')
        self.update(assertion_id='other-scope')
        unknown=capture(self.engine); self.update(updated_at='2026-01-04')
        global_spec=replace(self.spec,scopes=())
        self.assertNotEqual(unknown.corpus(self.engine,global_spec),capture(self.engine).corpus(self.engine,global_spec))


class NavigationFreshnessTest(unittest.TestCase):
    setUp=NavigationRelationBoundaryTest.setUp
    provider=NavigationRelationBoundaryTest.provider
    directory=NavigationRelationBoundaryTest.directory
    settled=RelationFreshnessTest.settled
    update=RelationFreshnessTest.update
    complete=RelationFreshnessTest.complete

    def test_navigation_current_guard_preserves_raw_audit_and_required_children(self):
        from prototypes.assertion_memory.navigation_index import validate_publication
        from prototypes.assertion_memory.background_overviews import navigation_context
        from prototypes.assertion_memory.query_snapshot import receipt
        self.settled('voltage','gate'); before,_,result,root=self.directory()
        check=receipt(self.engine,result['check_id']); frozen=copy.deepcopy(check)
        self.complete(); after=capture(self.engine)
        context=navigation_context(self.engine,self.engine._spec(root.view_id),before)
        self.assertTrue(validate_publication(self.engine,before,context,root,check))
        self.assertEqual(self.engine.inspect_view(root.view_id)['status'],'fresh')
        self.assertEqual(check,frozen)
        self.assertNotEqual(before.relation_processing_frontier,after.relation_processing_frontier)
        changed=copy.deepcopy(check)
        changed['overview_review']['relation_boundary']['frontier_sha256']='0'*64
        self.assertFalse(validate_publication(self.engine,before,context,root,changed))
        self.assertFalse(validate_publication(self.engine,before,context,replace(root,upstream={}),check))


def load_tests(loader, tests, pattern):
    return unittest.TestSuite([loader.loadTestsFromTestCase(RelationFreshnessTest),
                               loader.loadTestsFromTestCase(NavigationFreshnessTest)])


if __name__=='__main__': unittest.main()
