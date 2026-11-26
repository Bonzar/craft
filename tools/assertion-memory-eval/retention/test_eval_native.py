import unittest

from eval_native import DIAGNOSTIC, MANDATORY, grade, load_cases, main


class NativeRetentionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = load_cases()

    @staticmethod
    def observed(**override):
        return dict(raw_count=2, raw_integrity=True, construct_count=1, worker_complete=True,
                    thoughts=[{'text':'Use euros', 'refs':['u']}] ) | override

    def test_mandatory_and_red_are_separate(self):
        self.assertEqual(set(self.cases), set(MANDATORY) | {DIAGNOSTIC})
        good = grade(self.cases['R009'], self.observed())
        self.assertTrue(good['mechanical_pass'])
        self.assertTrue(good['semantic_review_pending'])
        red = grade(self.cases[DIAGNOSTIC], self.observed())
        self.assertFalse(red['mechanical_pass'])
        self.assertEqual(red['diagnostic'], 'known_red')

    def test_each_loss_and_wrong_refs_fail_mechanical_gate(self):
        group = self.cases['R009']
        for key, update in (
            ('raw_count', {'raw_count':1}), ('raw_integrity', {'raw_integrity':False}),
            ('construct_count', {'construct_count':2}), ('construct_count', {'construct_count':0}),
            ('worker_complete', {'worker_complete':False}),
            ('refs', {'thoughts':[{'text':'Use euros','refs':['other-case']}]}),
            ('thought_count', {'thoughts':[]}),
        ):
            with self.subTest(key=key):
                result = grade(group, self.observed(**update))
                self.assertFalse(result['checks'][key])
                self.assertFalse(result['mechanical_pass'])

    def test_prior_only_never_counts_as_new_provenance(self):
        result = grade(self.cases['R006'], self.observed(thoughts=[{'text':'Use euros','refs':['o']}]))
        self.assertFalse(result['checks']['refs'])
        self.assertFalse(result['checks']['primary_ref'])

    def test_no_model_without_explicit_case_selection_and_execute(self):
        self.assertEqual(main([]), 0)
        with self.assertRaises(SystemExit):
            main(['--execute'])
        with self.assertRaises(SystemExit):
            main(['--execute', *sum((['--case', i] for i in MANDATORY[:5]), [])])


if __name__ == '__main__':
    unittest.main()
