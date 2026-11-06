"""Очистка ревью не удаляет строки, появившиеся после проверенного снимка."""

import json
import tempfile
import unittest
from pathlib import Path

from jarvis.storage import Storage

from .test_turn_review import review_lib, write_record


class ConditionalClearTest(unittest.TestCase):
    def test_a_new_write_prevents_clear_and_preserves_state(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory), 'scope')
            first = write_record('blocks add first')
            storage.append_line(review_lib.VOLUME_FILE, json.dumps(review_lib.record_row(first)))
            state = review_lib.ReviewState()
            review_lib.save_state(storage, state)
            expected = review_lib.fingerprint([first])
            storage.append_line(review_lib.VOLUME_FILE,
                                json.dumps(review_lib.record_row(write_record('blocks add second'))))
            cleared = review_lib.clear_volume(storage, state, expected_fingerprint=expected)
            self.assertIsNone(cleared)
            self.assertEqual(review_lib.count_writes(review_lib.peek_volume(storage)), 2)
            self.assertEqual(review_lib.load_state(storage)[0], state)

    def test_new_corruption_is_preserved_for_the_next_review(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory), 'scope')
            first = write_record('blocks add first')
            storage.append_line(review_lib.VOLUME_FILE, json.dumps(review_lib.record_row(first)))
            expected = review_lib.fingerprint([first])
            storage.append_line(review_lib.VOLUME_FILE, 'broken-json')
            before = storage.path(review_lib.VOLUME_FILE).read_text()
            cleared = review_lib.clear_volume(storage, review_lib.ReviewState(), expected_fingerprint=expected)
            self.assertIsNone(cleared)
            self.assertEqual(storage.path(review_lib.VOLUME_FILE).read_text(), before)
