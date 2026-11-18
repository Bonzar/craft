"""Only anchored independently verified policy reports bypass coarse kind conflict."""
import copy
import unittest

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.storage import MemoryStore


class PolicyEvidenceTest(unittest.TestCase):
    def build(self, *, verification=None, condition=None, edge_basis='reported_decision'):
        store = MemoryStore(':memory:')
        self.addCleanup(store.close)
        for key, kind, text in (('old', 'decision', 'The council adopted the base release policy.'),
                                ('new', 'assertion', 'A reporter records the same council adopting its replacement.')):
            refs = [{'event_id': key + '-event', 'start': 0, 'end': len(text), 'role': 'user', 'relation': 'statement'}]
            store.append_assertion(assertion_id=key, text=text, actor=key + '-reporter', source='fixture://' + key,
                episode='fixture', scope='s', recorded_at='2026-01-01',
                metadata={'kind': kind, 'model_verified': True, 'event_id': key + '-event', 'evidence_refs': refs})
        proof = {'status': 'accepted', 'basis': 'reported_decision', 'decision_evidence': {
            'source_refs': [{'event_id': 'new-event', 'start': 0, 'end': 12, 'role': 'user', 'relation': 'statement'}],
            'target_refs': [{'event_id': 'old-event', 'start': 0, 'end': 12, 'role': 'user', 'relation': 'statement'}]}}
        metadata = {'basis': edge_basis, 'verification': proof if verification is None else verification}
        if condition:
            metadata['condition'] = condition
        store.append_edge(edge_id='revision', from_id='new', to_id='old', kind='supersedes',
                          recorded_at='2026-01-02', metadata=metadata)
        engine = MemoryEngine(store)
        engine.define_view(ViewSpec('v', 'Policy', 'Release policy', scopes=('s',), root_ids=('old',)))
        return engine.build_view('v'), proof

    def test_accepted_reported_decision_supersedes_without_retyping_report(self):
        version, _ = self.build()
        rows = {row['id']: row for row in version.evidence}
        self.assertEqual(rows['old']['disposition'], 'superseded')
        self.assertEqual(rows['new']['disposition'], 'assertion')
        self.assertEqual(rows['new']['metadata']['kind'], 'assertion')

    def test_unverified_or_unanchored_cross_kind_edge_remains_conflict(self):
        _, good = self.build()
        invalid = [dict(good, status='unresolved'), {'status': 'accepted', 'basis': 'reported_decision'}, [], 'accepted', 1]
        for field, value in (('role', 'assistant'), ('event_id', 'unavailable-event'), ('end', 10000)):
            proof = copy.deepcopy(good)
            proof['decision_evidence']['source_refs'][0][field] = value
            invalid.append(proof)
        for proof in invalid:
            with self.subTest(proof=proof):
                version, _ = self.build(verification=proof)
                self.assertEqual({row['disposition'] for row in version.evidence}, {'conflict'})
        manual, _ = self.build(edge_basis='manual')
        self.assertEqual({row['disposition'] for row in manual.evidence}, {'conflict'})

    def test_conditioned_verified_report_keeps_base_outside_condition(self):
        version, _ = self.build(condition='Only for the authorized trial scope.')
        self.assertNotIn('superseded', {row['disposition'] for row in version.evidence})
        self.assertIn('old', {row['id'] for row in version.evidence})
        self.assertIn('Only for the authorized trial scope.', version.summary)
