"""Missing producer order is unknown and does not invent a transcript ordinal."""
import unittest
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.service import MemoryService


class CaptureOrderTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.service = MemoryService(self.store)

    def capture(self, position, text='Same producer message'):
        return self.service.capture_message('s', 'message', 'speaker', 'user', text,
                                            position, 'producer://s/message')

    def test_unknown_order_is_idempotent_without_position_row(self):
        first = self.capture(None)
        again = self.capture(None)
        self.assertEqual(first['event']['id'], again['event']['id'])
        self.assertIsNone(self.service.session_messages('s')['messages'][0]['position'])

    def test_omission_on_replay_keeps_known_order(self):
        first = self.capture(17)
        self.assertEqual(first['event']['id'], self.capture(None)['event']['id'])
        self.assertEqual(self.service.session_messages('s')['messages'][0]['position'], 17)

    def test_known_order_does_not_rewrite_unknown_source_on_replay(self):
        first = self.capture(None)
        self.assertEqual(first['event']['id'], self.capture(17)['event']['id'])
        self.assertIsNone(self.service.session_messages('s')['messages'][0]['position'])

    def test_two_conflicting_known_orders_and_changed_content_still_fail(self):
        self.capture(17)
        with self.assertRaises(ValueError): self.capture(18)
        with self.assertRaises(ValueError): self.capture(None, 'Changed source')
