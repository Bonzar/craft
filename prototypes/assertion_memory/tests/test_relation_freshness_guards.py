"""Cold-review regressions: unknown authorization and Worker verification parity."""
import copy
import json
import unittest
from dataclasses import replace

from prototypes.assertion_memory.answer_relation_gaps import inventory, public_inventory
from prototypes.assertion_memory.query_snapshot import capture, freshness
from prototypes.assertion_memory.tests import test_relation_freshness as fixtures
from prototypes.assertion_memory.tests import test_answer_contract_v21 as contracts
from prototypes.assertion_memory.worker import _adjudicate


class RelationFreshnessGuardTest(unittest.TestCase):
    setUp=fixtures.RelationFreshnessTest.setUp
    provider=fixtures.RelationFreshnessTest.provider
    build=fixtures.RelationFreshnessTest.build
    result=fixtures.RelationFreshnessTest.result
    alias=fixtures.RelationFreshnessTest.alias
    update=fixtures.RelationFreshnessTest.update
    complete=fixtures.RelationFreshnessTest.complete
    secret='FICTIONAL-UNOWNED-PROPOSAL-BODY'

    def orphan(self, *, status='unresolved', job_status='completed'):
        proposal={'kind':'depends_on','from_id':'permit','to_id':'garden','status':status,
            'condition':self.secret+' condition','source_quote':self.secret+' source',
            'target_quote':self.secret+' target','reason':self.secret+' reason'}
        # Reproduce a legacy orphan without disabling constraints in product code.
        db=self.store._connection
        db.execute('PRAGMA foreign_keys=OFF')
        try:
            with self.store.transaction(write=True) as connection:
                connection.execute("DELETE FROM memory_capture_jobs WHERE id='orphan'")
                connection.execute('INSERT INTO memory_capture_jobs(id,assertion_id,status,phase,proposals,updated_at,available_at) '
                    'VALUES(?,?,?,?,?,?,?)',('orphan','unknown-owner',job_status,'done',json.dumps([proposal]),'2026-01-02','2026-01-02'))
        finally:
            db.execute('PRAGMA foreign_keys=ON')

    def test_unknown_authority_is_opaque_operational_for_scoped_and_global_inventories(self):
        for status in ('pending','unresolved','applied','existing','rejected','unknown'):
            with self.subTest(status=status):
                self.orphan(status=status)
                snapshot=capture(self.engine)
                self.assertIn(self.secret,json.dumps(snapshot.relation_processing_evidence))  # Full raw audit remains.
                for scopes in (('s',),()):
                    packet=snapshot.relation_gaps(scopes)
                    self.assertNotIn(self.secret,json.dumps(packet))
                    self.assertNotIn(self.secret,json.dumps(public_inventory(packet)))
                    self.assertEqual(packet['gaps'],[])
                    self.assertGreater(packet['operational_count'],0)
                with self.store.transaction(write=True) as db: db.execute("DELETE FROM memory_capture_jobs WHERE id='orphan'")

    def test_unknown_authority_never_reaches_provider_or_response_audit_or_gets_waived(self):
        for scope in ('s',None):
            with self.subTest(scope=scope):
                if scope is None:
                    self.spec=replace(self.spec,id='global-view',scopes=()); self.engine.define_view(self.spec)
                with self.store.transaction(write=True) as db: db.execute("DELETE FROM memory_capture_jobs WHERE id='relation-99'")
                self.orphan(); contracts.AnswerContractTest.relation(self,99)
                self.requests=[]; view,check=self.build()
                result=self.service._query_result(view,{},self.service.status(scope=scope),check,scope,None)
                self.assertNotIn(self.secret,json.dumps([r.records for r in self.requests]))
                self.assertNotIn(self.secret,json.dumps(check))
                self.assertNotIn(self.secret,json.dumps(result))
                self.assertTrue(result['refresh_required'])
                self.assertIs(result['source_status']['processing_gaps_apply_to_query'],True)
                audit=check['overview_review']['relation_gap_review']
                self.assertEqual(len(audit['dispositions']),1)  # Known proposal only, no opaque waiver.
                with self.store.transaction(write=True) as db:
                    db.execute("DELETE FROM memory_capture_jobs WHERE id IN ('orphan','relation-99')")

    def test_unknown_authority_changes_freshness_and_cannot_reuse_a_healthy_alias_clearance(self):
        self.build(); first=self.alias(); before=capture(self.engine)
        self.orphan(); after=capture(self.engine)
        self.assertNotEqual(before.corpus(self.engine,self.spec),after.corpus(self.engine,self.spec))
        self.assertTrue(freshness(self.engine,self.spec,before)['refresh_required'])
        self.requests=[]; result=self.alias()
        self.assertTrue(result['refresh_required'])
        self.assertIs(result['source_status']['processing_gaps_apply_to_query'],True)
        self.assertNotIn(self.secret,json.dumps([r.records for r in self.requests]))
        self.assertNotIn(self.secret,json.dumps(result))
        self.assertNotEqual(first.get('answer_certificate'),result.get('answer_certificate'))

    def test_known_foreign_scope_retains_authorized_body_and_known_scope_isolation(self):
        self.store.append_assertion(assertion_id='foreign',text='Known other-scope source.',actor='reporter',
            source='fixture://foreign',episode='foreign',scope='other',recorded_at='2026-01-01')
        proposal={'kind':'supports','from_id':'foreign','to_id':'foreign','status':'unresolved',
            'reason':'KNOWN-FOREIGN-SCOPE-PROPOSAL','source_quote':'Known other-scope source.','target_quote':'Known other-scope source.'}
        with self.store.transaction(write=True) as db:
            db.execute('INSERT INTO memory_capture_jobs(id,assertion_id,status,phase,proposals,updated_at,available_at) '
                'VALUES(?,?,?,?,?,?,?)',('foreign-job','foreign','completed','done',json.dumps([proposal]),'2026-01-02','2026-01-02'))
        snapshot=capture(self.engine)
        self.assertNotIn('KNOWN-FOREIGN',json.dumps(snapshot.relation_gaps(('s',))))
        for scopes in (('other',),()):
            self.assertIn('KNOWN-FOREIGN',json.dumps(snapshot.relation_gaps(scopes)))
            self.assertEqual(snapshot.relation_gaps(scopes)['total'],1)

    def modifying(self, kind, *, interpreted=True, status='applied', proof=None, basis='observation', anchors=False):
        self.clear_modifying()
        metadata={'kind':'observation' if basis=='observation' else 'decision'}
        if interpreted: metadata['interpretation']={'status':'model_verified'}
        for key,text in [('left','Observed route is closed.'),('right','Observed route is open.')]:
            record_metadata=copy.deepcopy(metadata)
            if anchors:
                record_metadata.update(event_id='event-'+key,evidence_refs=[{'event_id':'event-'+key,
                    'start':0,'end':len(text),'text':text,'role':'user','relation':'statement'}])
            self.store.append_assertion(assertion_id=key,text=text,actor='reporter',source='fixture://'+key,
                episode='route',scope='s',recorded_at='2026-01-01',metadata=record_metadata)
        source=self.store.get_assertion('left'); target=self.store.get_assertion('right')
        item={'kind':kind,'target_id':'right','source_quote':source.text,'target_quote':target.text}
        worker=_adjudicate(source,{'right':target},item)
        proposal={**item,'from_id':'left','to_id':'right','status':status,'basis':basis,
            'verification_required':False,'reason':'Synthetic settled disposition.'}
        if proof is not None: proposal['verification']=proof
        if status in ('applied','existing'):
            self.store.append_edge(edge_id='modifying-edge',from_id='left',to_id='right',kind=kind,recorded_at='2026-01-02')
            proposal['edge_id']='modifying-edge'
        with self.store.transaction(write=True) as db:
            db.execute('INSERT INTO memory_capture_jobs(id,assertion_id,status,phase,candidates,proposals,updated_at,available_at) '
                'VALUES(?,?,?,?,?,?,?,?)',('settled','left','running','refresh',json.dumps([{'id':'right','score':1.0,'method':'fixture'}]),
                json.dumps([proposal]),'2026-01-02','2026-01-02'))
        return worker,proposal

    def clear_modifying(self):
        with self.store.transaction(write=True) as db:
            db.execute("DELETE FROM memory_capture_jobs WHERE id='settled'")
            db.execute("DELETE FROM memory_edges WHERE edge_id='modifying-edge'")
            db.execute("DELETE FROM memory_assertions WHERE assertion_id IN ('left','right')")

    def test_interpreted_modifying_relations_require_worker_verification_before_normalization(self):
        for kind,basis in [('contradicts','observation'),('exception_to','observation'),('supersedes','decision')]:
            for status in ('applied','existing','rejected'):
                with self.subTest(kind=kind,status=status):
                    worker,proposal=self.modifying(kind,basis=basis,status=status)
                    # Worker may retain a structural pending blocker before proposing verification.
                    self.assertNotEqual(worker['status'],'eligible')
                    before=capture(self.engine); self.complete()
                    self.assertNotEqual(before.corpus(self.engine,self.spec),capture(self.engine).corpus(self.engine,self.spec))
                    self.clear_modifying()

    def test_accepted_verifier_proof_and_legacy_structural_relations_keep_normal_cleanup(self):
        for interpreted,proof in [(True,{'status':'accepted','reason':'Independent semantic verification.','version':1}),
                                  (False,None)]:
            for status in ('applied','existing'):
                with self.subTest(interpreted=interpreted,status=status):
                    self.modifying('contradicts',interpreted=interpreted,status=status,proof=proof)
                    before=capture(self.engine); self.complete()
                    self.assertEqual(before.corpus(self.engine,self.spec),capture(self.engine).corpus(self.engine,self.spec))
                    self.clear_modifying()

    def test_unsupported_verifier_is_distinct_from_missing_or_incomplete_proof(self):
        for proof,settled in [(None,False),({'status':'unsupported','reason':'Independent rejection.','version':1},True),
                              ({'status':'unsupported','reason':'Incomplete proof.'},False)]:
            with self.subTest(proof=proof):
                self.modifying('contradicts',status='rejected',proof=proof)
                before=capture(self.engine); self.complete()
                self.assertEqual(before.corpus(self.engine,self.spec)==capture(self.engine).corpus(self.engine,self.spec),settled)
                self.clear_modifying()

    def test_reported_decision_and_self_correction_cannot_use_generic_accepted_proof(self):
        for basis in ('reported_decision','self_correction'):
            with self.subTest(basis=basis):
                self.modifying('supersedes',basis=basis,proof={'status':'accepted','reason':'Generic only.','version':1})
                before=capture(self.engine); self.complete()
                self.assertNotEqual(before.corpus(self.engine,self.spec),capture(self.engine).corpus(self.engine,self.spec))
                self.clear_modifying()


    def test_reported_decision_and_self_correction_preserve_exact_compact_anchor_proof(self):
        for basis in ('reported_decision','self_correction'):
            source={'event_id':'event-left','start':0,'end':5,'role':'user'}
            target={'event_id':'event-right','start':0,'end':5,'role':'user'}
            evidence=({'decision_evidence':{'source_refs':[{**source,'relation':'statement'}],
                'target_refs':[{**target,'relation':'statement'}]}} if basis=='reported_decision'
                else {'correction_evidence':{'source':source,'target':target}})
            proof={'status':'accepted','reason':'Independent exact-source verification.','version':1,'basis':basis,**evidence}
            with self.subTest(basis=basis):
                self.modifying('supersedes',basis=basis,proof=proof,anchors=True)
                before=capture(self.engine); self.complete()
                self.assertEqual(before.corpus(self.engine,self.spec),capture(self.engine).corpus(self.engine,self.spec))
                changed=copy.deepcopy(proof)
                if basis=='reported_decision': changed['decision_evidence']['source_refs'][0]['role']='assistant'
                else: changed['correction_evidence']['source']['role']='assistant'
                self.clear_modifying(); self.modifying('supersedes',basis=basis,proof=changed,anchors=True)
                before=capture(self.engine); self.complete()
                self.assertNotEqual(before.corpus(self.engine,self.spec),capture(self.engine).corpus(self.engine,self.spec))
                self.clear_modifying()


if __name__=='__main__': unittest.main()
