import importlib.util
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

fixture = load('fixture_for_oracle', HERE.parents[1] / 'scripts' / 'fixture.py')
oracle = load('source_oracle', HERE / 'source_oracle.py')


class TemporalOracleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.manifest = fixture.generate_fixture(Path(self.temporary.name) / 'synthetic', nodes=80, edges=120)

    def test_known_then_includes_undated_and_known_future_but_excludes_late_knowledge(self):
        result = oracle.expected_slice(self.manifest, mode='known_then', at=self.manifest['temporal']['early'])
        cases = self.manifest['cases']
        self.assertIn(cases['undated_requirement'], result['ids'])
        self.assertIn(cases['future_plan'], result['ids'])
        for key in ('late_fact', 'late_interpretation', 'late_available'):
            self.assertNotIn(cases[key], result['ids'])

    def test_effective_replacement_does_not_hide_predecessor_early(self):
        cases = self.manifest['cases']
        before = oracle.expected_slice(self.manifest, mode='known_then', at=self.manifest['temporal']['before_effective'])
        after = oracle.expected_slice(self.manifest, mode='known_then', at=self.manifest['temporal']['after_effective'])
        self.assertIn(cases['future_old'], before['visible_ids'])
        self.assertIn(cases['future_replacement'], before['ids'])
        self.assertNotIn(cases['future_old'], after['visible_ids'])
        self.assertIn(cases['future_old'], after['ids'])

    def test_known_now_surfaces_undated_bucket_and_source_disposition_exceptions(self):
        cases = self.manifest['cases']
        result = oracle.expected_slice(self.manifest, mode='known_now', at=self.manifest['temporal']['before_effective'])
        self.assertNotIn(cases['future_replacement'], result['ids'])
        self.assertNotIn(cases['undated_requirement'], result['ids'])
        self.assertIn(cases['undated_requirement'], result['undated_ids'])
        for key in ('conditional_old','cross_scope_old','proposal_old','inference_old','cycle_a','cycle_b'):
            self.assertIn(cases[key], result['visible_ids'])
        self.assertNotIn(cases['superseded_old'], result['visible_ids'])


if __name__ == '__main__':
    unittest.main()
