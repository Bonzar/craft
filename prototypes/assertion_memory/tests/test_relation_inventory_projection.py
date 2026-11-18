"""Semantic endpoint projection must not spend review budget on duplicate proof."""
import copy
import json
import unittest

from prototypes.assertion_memory.answer_relation_gaps import MAX_BODY_CHARACTERS, inventory
from prototypes.assertion_memory.overview import _model_record
from prototypes.assertion_memory.query_snapshot import capture
from prototypes.assertion_memory.tests import test_answer_contract_v21 as fixtures


class RelationInventoryProjectionTest(unittest.TestCase):
    # Reuse the deterministic source-independent fixture, not its test methods.
    setUp=fixtures.AnswerContractTest.setUp
    provider=fixtures.AnswerContractTest.provider
    relation=fixtures.AnswerContractTest.relation
    build=fixtures.AnswerContractTest.build
    result=fixtures.AnswerContractTest.result
    def rich_endpoints(self):
        expected={}
        for index in range(6):
            key='endpoint-'+str(index)
            text=('Reported operation '+str(index)+': both the inspection AND actual acceptance remain required; '
                  'this reported condition does not establish permission.')
            metadata={'kind':'reported_decision','role':'user','source_method':'quoted_report',
                'reporting_status':'attributed','temporal_precision':'day',
                'source_recorded_at':'2026-01-01','source_available_at':'2026-01-02',
                'interpretation':{'status':'accepted','source_coverage':{'status':'accepted_complete',
                    'protocol':'fixture-coverage-v1','review_detail':'redundant proof '*300}},
                'evidence_refs':[{'event_id':'proof-'+str(index),'start':0,'end':len(text),
                    'text':text,'transport_audit':'raw proof '*400}]}
            self.store.append_assertion(assertion_id=key,text=text,actor='reporter-'+str(index),
                source='fixture://'+key,episode=key,scope='s',valid_at='2025-12-31',
                recorded_at='2026-01-02',metadata=metadata)
            expected[key]=metadata
        for index in range(3):
            self.relation(index,from_id='endpoint-'+str(index*2),to_id='endpoint-'+str(index*2+1))
        return expected

    def test_three_terminal_candidates_with_large_redundant_proof_receive_complete_review(self):
        expected=self.rich_endpoints()
        snapshot=capture(self.engine)
        before=copy.deepcopy(snapshot.assertions)
        self.assertGreater(sum(len(json.dumps(snapshot.assertions[key])) for key in expected),MAX_BODY_CHARACTERS*3)
        packet=snapshot.relation_gaps(('s',))
        self.assertTrue(packet['complete'])
        self.assertEqual(packet['total'],3)
        self.assertEqual(packet['unassessed_count'],0)
        self.assertLessEqual(sum(len(json.dumps(row,ensure_ascii=False)) for row in packet['gaps']),MAX_BODY_CHARACTERS)
        for candidate in packet['gaps']:
            for endpoint in candidate['endpoints']:
                self.assertEqual(endpoint,_model_record(snapshot.assertions[endpoint['id']]))
                self.assertEqual(endpoint['text'],snapshot.assertions[endpoint['id']]['text'])
                self.assertNotIn('evidence_refs',endpoint['metadata'])
        self.assertEqual(snapshot.assertions,before)
        view,check=self.build(); result=self.result(view,check)
        self.assertTrue(check['overview_review']['relation_gap_review']['inventory']['complete'])
        self.assertFalse(result['refresh_required'])
        self.assertFalse(result['source_status']['processing_gaps_apply_to_query'])
        self.assertEqual(result['unresolved_count'],3)
        for key,metadata in expected.items(): self.assertEqual(self.store.get_assertion(key).metadata,metadata)

    def test_projection_preserves_complete_text_qualification_clocks_method_and_current_source_status(self):
        self.relation()
        snapshot=capture(self.engine)
        assertions=copy.deepcopy(snapshot.assertions)
        record=assertions['permit']
        record.update(disposition='reported_decision',exception_basis={'kind':'reported_exception'},
            conflict_basis={'kind':'reported_conflict'},valid_at='2025-12-31',
            current_source_revalidation={'event_id':'event-proof','source_run_id':7,'source_status':'unresolved',
                'protocol':'fixture-v1','checked_at':'2026-01-03','last_positive_claim_run_id':6,
                'last_positive_source_status':'accepted_complete'})
        record['text']='Reported permission applies only if BOTH conditions hold; identity and authority remain unconfirmed.'
        record['metadata']={'kind':'reported_decision','role':'user','source_method':'attributed_quotation',
            'reporting_status':'reported_not_adopted','temporal_precision':'unknown',
            'source_recorded_at':'2026-01-01','source_available_at':'2026-01-02',
            'interpretation':{'status':'accepted','source_coverage':{'status':'unresolved','protocol':'coverage-v1',
                'audit':'large redundant proof '*1000}},'evidence_refs':[{'audit':'proof '*1000}]}
        before=copy.deepcopy(assertions)
        packet=inventory(snapshot.relation_processing_evidence,('s',),assertions)
        self.assertTrue(packet['complete'])
        endpoint=next(row for row in packet['gaps'][0]['endpoints'] if row['id']=='permit')
        self.assertEqual(endpoint,_model_record(record))
        for field in ('text','actor','source','scope','valid_at','recorded_at','disposition',
                      'exception_basis','conflict_basis','current_source_revalidation'):
            self.assertEqual(endpoint[field],record[field])
        for field in ('kind','role','source_method','reporting_status','temporal_precision',
                      'source_recorded_at','source_available_at'):
            self.assertEqual(endpoint['metadata'][field],record['metadata'][field])
        self.assertEqual(endpoint['metadata']['interpretation'],{'status':'accepted',
            'source_coverage':{'status':'unresolved','protocol':'coverage-v1'}})
        self.assertEqual(assertions,before)

    def test_over_budget_semantic_endpoint_text_stays_omitted_unassessed_never_truncated(self):
        self.relation()
        snapshot=capture(self.engine)
        assertions=copy.deepcopy(snapshot.assertions)
        text='The complete required semantic condition is '+('material qualifier '*1000)
        assertions['permit']['text']=text
        packet=inventory(snapshot.relation_processing_evidence,('s',),assertions)
        self.assertFalse(packet['complete'])
        self.assertEqual(packet['total'],1)
        self.assertEqual(packet['unassessed_count'],1)
        self.assertEqual(packet['gaps'],[])
        self.assertEqual(assertions['permit']['text'],text)


if __name__=='__main__': unittest.main()
