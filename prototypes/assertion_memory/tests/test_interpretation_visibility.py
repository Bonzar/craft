"""Historical answers must not use a derivation that was produced later."""
import unittest

from prototypes.assertion_memory.storage import MemoryStore


class InterpretationVisibilityTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.store.append_assertion(assertion_id='old', text='North gate was approved.', actor='manager',
            source='chat://original', episode='original', scope='campus',
            valid_at='2026-11-04', recorded_at='2026-11-05')
        self.store.append_assertion(assertion_id='derived', text='North gate exception was learned later.',
            actor='manager', source='chat://later', episode='later', scope='campus',
            valid_at='2026-11-04', recorded_at='2026-11-15',
            metadata={'source_available_at': '2026-11-15T00:00:00.000000+00:00',
                      'interpretation': {'status': 'model_verified',
                                         'interpreted_at': '2026-11-20T00:00:00.000000+00:00'}})
        self.store.append_edge(edge_id='exception', from_id='derived', to_id='old',
                               kind='exception_to', recorded_at='2026-11-16')

    def test_recorded_cutoff_excludes_later_interpretation_and_its_relationship(self):
        filters = {'valid_as_of': '2026-11-06', 'recorded_as_of': '2026-11-17'}
        self.assertEqual([a.assertion_id for a in self.store.list_assertions(**filters)], ['old'])
        self.assertIsNone(self.store.get_assertion('derived', **filters))
        self.assertEqual(self.store.list_edges(**filters), [])
        self.assertEqual([hit.assertion.assertion_id for hit in self.store.search('gate', **filters)], ['old'])

    def test_reconstruction_with_current_knowledge_includes_both_source_times(self):
        rows = self.store.list_assertions(valid_as_of='2026-11-06', recorded_as_of='2026-11-21')
        self.assertEqual({row.assertion_id for row in rows}, {'old', 'derived'})
        derived = next(row for row in rows if row.assertion_id == 'derived')
        self.assertTrue(derived.recorded_at.startswith('2026-11-15'))
        self.assertTrue(derived.valid_at.startswith('2026-11-04'))

    def test_scoped_version_does_not_follow_future_interpretations(self):
        self.assertEqual(self.store.scope_version('campus', follow_kinds='exception_to', active_at='2026-11-17'), 1)
        self.assertEqual(self.store.scope_version('campus', follow_kinds='exception_to', active_at='2026-11-21'), 3)


if __name__ == '__main__':
    unittest.main()
