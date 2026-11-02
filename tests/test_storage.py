"""Хранилище: две зоны, атомарная запись, журнал."""

import multiprocessing
import os
import tempfile
import threading
import time
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


# Строка длиннее буфера записи: без лока ядро могло бы записать её не одним вызовом.
LONG_LINE_CHARS = 200_000
WRITERS = 6
LINES_PER_WRITER = 5
LOCK_HOLD_SECONDS = 0.3


def _append_many(root: str, writer: int) -> None:
    storage = Storage(Path(root), 'sess-1')
    for index in range(LINES_PER_WRITER):
        storage.append_line('log.jsonl', f'{writer}-{index}:' + str(writer) * LONG_LINE_CHARS)


class JournalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(Path(self.tmp.name), 'sess-1')

    def test_parallel_processes_append_whole_lines(self) -> None:
        processes = [multiprocessing.Process(target=_append_many, args=(self.tmp.name, writer))
                     for writer in range(WRITERS)]
        for process in processes:
            process.start()
        for process in processes:
            process.join()
        lines = self.storage.read_lines('log.jsonl')
        self.assertEqual(len(lines), WRITERS * LINES_PER_WRITER)
        for line in lines:
            head, body = line.split(':', 1)
            writer = head.split('-')[0]
            self.assertEqual(body, writer * LONG_LINE_CHARS)

    def test_a_line_waiting_for_a_rewrite_lands_in_the_new_file(self) -> None:
        self.storage.append_line('log.jsonl', 'старая')
        appended = threading.Event()

        def append_later() -> None:
            self.storage.append_line('log.jsonl', 'новая')
            appended.set()

        with self.storage.journal_lock('log.jsonl') as path:
            writer = threading.Thread(target=append_later)
            writer.start()
            time.sleep(LOCK_HOLD_SECONDS)
            self.assertFalse(appended.is_set())
            tmp = path.with_name(path.name + '.tmp')
            tmp.write_text('переписана\n', encoding='utf-8')
            os.replace(tmp, path)
        writer.join()
        self.assertEqual(self.storage.read_lines('log.jsonl'), ['переписана', 'новая'])


if __name__ == '__main__':
    unittest.main()
