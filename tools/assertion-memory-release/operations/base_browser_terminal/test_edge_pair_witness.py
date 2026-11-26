from unittest import TestCase
from edge_pair_witness import Edge,Pair,verify_edge_state,required_inverse
from test_terminal_verifier import Verify

class EdgeWitness(TestCase):
    def test_candidate_and_exact_two_way_inverse(self):
        f=Verify(); baseline=Edge('1'*64,'2'*64); candidate=Edge('3'*64,'4'*64)
        pair=Pair(f.old,f.new,baseline,candidate)
        self.assertTrue(verify_edge_state(pair,phase='candidate',current_release=f.new,observed=candidate))
        self.assertTrue(verify_edge_state(pair,phase='restore',current_release=f.old,observed=baseline))
        self.assertEqual(required_inverse(pair,observed=candidate),('gateway','nginx'))
        self.assertEqual(required_inverse(pair,observed=Edge(candidate.nginx,baseline.gateway)),('nginx',))
        self.assertEqual(required_inverse(pair,observed=baseline),())
        for live_version,edge in ((f.old,candidate),(f.new,baseline)):
            with self.assertRaises(ValueError):
                verify_edge_state(pair,phase='candidate',current_release=live_version,observed=edge)
        with self.assertRaises(ValueError):
            required_inverse(pair,observed=Edge(candidate.nginx,'0'*64))
