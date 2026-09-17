"""Хранилище: две зоны, атомарная запись, журнал."""

import tempfile
import unittest
from pathlib import Path

from jarvis.storage import PERSISTENT, SESSION, Storage


class StorageZonesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.storage = Storage(self.root, 'sess-1')

    def test_session_zone_is_a_subdirectory_by_session_id(self) -> None:
        self.assertEqual(self.storage.session_dir, self.root / 'sess-1')

    def test_persistent_zone_is_the_state_directory_itself(self) -> None:
        self.assertEqual(self.storage.persistent_dir, self.root)

    def test_zones_do_not_see_each_other(self) -> None:
        self.storage.write_json('value.json', {'zone': 'session'}, zone=SESSION)
        self.storage.write_json('value.json', {'zone': 'persistent'}, zone=PERSISTENT)
        self.assertEqual(self.storage.read_json('value.json', zone=SESSION), {'zone': 'session'})
        self.assertEqual(self.storage.read_json('value.json', zone=PERSISTENT), {'zone': 'persistent'})

    def test_another_session_gets_another_zone(self) -> None:
        self.storage.write_json('value.json', {'n': 1})
        other = Storage(self.root, 'sess-2')
        self.assertIsNone(other.read_json('value.json'))

    def test_missing_file_returns_the_default(self) -> None:
        self.assertEqual(self.storage.read_json('nope.json', default={'d': True}), {'d': True})

    def test_name_with_separator_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.storage.path('../escape.json')

    def test_storage_without_session_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Storage(self.root, '')

    def test_append_line_keeps_order(self) -> None:
        self.storage.append_line('log.jsonl', 'первая')
        self.storage.append_line('log.jsonl', 'вторая')
        self.assertEqual(self.storage.read_lines('log.jsonl'), ['первая', 'вторая'])

    def test_write_json_leaves_no_temporary_file(self) -> None:
        self.storage.write_json('value.json', {'n': 1})
        names = sorted(p.name for p in self.storage.session_dir.iterdir())
        self.assertEqual(names, ['value.json'])


if __name__ == '__main__':
    unittest.main()
