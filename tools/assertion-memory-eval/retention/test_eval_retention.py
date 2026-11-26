import json
import pathlib
import unittest
from eval_retention import HERE, blind, decode_predictions, score, validate_cases
class HarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows=validate_cases(json.loads((HERE/'fixtures.json').read_text()))
    def test_unique_complete_fixture_set(self):
        self.assertEqual(len(self.rows),310)
        self.assertEqual(sum(x['id'].startswith('R') for x in self.rows),10)
        self.assertEqual(self.rows[-2]['native_expectations']['max_construct_calls'],1)
    def test_blind_never_exposes_answers(self):
        for c in self.rows:self.assertEqual(set(blind(c)),{'id','messages','shape','already_retained'})
    def test_score_wrong_source_and_extra(self):
        cases=[x for x in self.rows if x['id'] in {'R003','R004'}]
        actual=[{'id':'R003','thoughts':[{'text':'a','refs':['p']}]},
                {'id':'R004','thoughts':[{'text':'a','refs':['not-a-source']}]}]
        s=score(cases,actual,{})['counts']
        self.assertEqual((s['missing_original_ref'],s['invalid_reference'],s['unwanted_thought']),(1,1,1))
    def test_wrong_answer_ids_fail(self):
        with self.assertRaises(AssertionError):decode_predictions([],self.rows)
if __name__=='__main__':unittest.main()
