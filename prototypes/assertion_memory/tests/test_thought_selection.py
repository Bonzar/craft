"""Historical queries freeze canonical versions and keep current writes fenced."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_store import ThoughtStore, ThoughtConflict
from prototypes.assertion_memory.thought_selection import ThoughtSelection


class ThoughtSelectionTest(unittest.TestCase):
    def setUp(self):
        self.memory = MemoryStore(':memory:')
        self.addCleanup(self.memory.close)
        self.thoughts = ThoughtStore(self.memory)
        self.first = self.publish_at(1, 'The room was blue.', thought_id='room',
            origin={'scope': 'home', 'valid_at': '2026-01-01', 'recorded_at': '2026-01-01'})
        self.latest = self.publish_at(3, 'The room is green.', thought_id='room', expected_version=1,
            origin={'scope': 'home', 'valid_at': '2026-01-03', 'recorded_at': '2026-01-03'})

    def publish_at(self, day, text, **kwargs):
        with patch('prototypes.assertion_memory.thought_store.datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 1, day, tzinfo=timezone.utc)
            return self.thoughts.publish(text, **kwargs)

    def selection(self):
        return ThoughtSelection(self.thoughts, scope='home', valid_as_of='2026-01-02', recorded_as_of='2026-01-02')

    def test_historical_version_and_saved_answer_reuse_only_same_selection(self):
        selected = self.selection()
        self.assertEqual(selected.get('room').text, self.first.text)
        answer = selected.publish('The room was blue then.', question='What colour was the room?',
            bases=[{'thought_id': 'room', 'version': 1}])
        self.assertNotIn('observed_head_version', answer.bases[0])
        self.assertEqual(self.selection().get(answer.id), answer)
        other = ThoughtSelection(self.thoughts, scope='home', recorded_as_of='2026-01-01')
        self.assertIsNone(other.get(answer.id))
        self.assertEqual(self.thoughts.get('room'), self.latest)
        self.assertNotIn(answer.id, {row.id for row in self.thoughts.list_thoughts()})
        self.assertEqual(self.thoughts.search_text('blue then'), [])

    def test_scope_follows_bases_and_unknown_valid_time_is_excluded(self):
        other = self.publish_at(1, 'Office fact.', thought_id='office', origin={'scope': 'work', 'valid_at': '2026-01-01'})
        unknown = self.publish_at(1, 'Undated fact.', thought_id='unknown', origin={'scope': 'home'})
        mixed = self.publish_at(4, 'Both home and work.', bases=[{'thought_id': 'room', 'version': 2},
            {'thought_id': other.id, 'version': 1}])
        home = ThoughtSelection(self.thoughts, scope='home')
        self.assertEqual({row.id for row in home.list_thoughts()}, {'room', unknown.id})
        self.assertIsNone(home.get(mixed.id))
        dated = self.selection()
        self.assertIsNone(dated.get(unknown.id))
        self.assertIsNone(dated.get(other.id))

    def test_historical_basis_fences_observed_current_head_without_weakening_normal_publish(self):
        selected = self.selection()
        self.publish_at(4, 'Newer result.', thought_id='room', expected_version=2,
            origin={'scope': 'home', 'valid_at': '2026-01-04'})
        with self.assertRaises(ThoughtConflict):
            selected.publish('Prepared before concurrent change.', bases=[{'thought_id': 'room', 'version': 1}])
        with self.assertRaises(ThoughtConflict):
            self.thoughts.publish('Unfenced stale result.', bases=[{'thought_id': 'room', 'version': 1}])
        self.assertEqual(selected.get('room'), self.first)
        self.assertEqual(selected.get('room', 2), self.latest)


if __name__ == '__main__':
    unittest.main()
