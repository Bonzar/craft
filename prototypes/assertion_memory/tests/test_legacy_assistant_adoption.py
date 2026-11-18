"""Synthetic migration regression: immutable old assistant provenance is adopted."""
import hashlib
import json
import unittest

from .. import native_intake_test as fixtures
from ..desktop_fast import _capture
from ..native_intake import IntakeGap
from ..queue import CaptureQueue
from ..storage import MemoryStore


class LegacyAssistantAdoptionTests(unittest.TestCase):
    setUp = fixtures.NativeIntakeTests.setUp
    tearDown = fixtures.NativeIntakeTests.tearDown
    append = fixtures.NativeIntakeTests.append
    turn = fixtures.NativeIntakeTests.turn
    assistant = fixtures.NativeIntakeTests.assistant
    events = fixtures.NativeIntakeTests.events
    watcher = fixtures.NativeIntakeTests.watcher

    def seed_v1(self, *, repeated=False, mutate=None):
        start = self.turn(trigger='user')
        if repeated:
            for row in list(self.rows[2:4]):
                self.append(row)
        assistant_position = self.path.stat().st_size
        self.assistant()
        raw = {'session_id': 'physical', 'cwd': str(self.root), 'transcript_path': str(self.path),
               'turn_id': 'one', 'prompt': self.rows[2]['payload']['content'][0]['text']}
        proof = {'origin_kind': 'human'}
        with MemoryStore(self.config['db']) as store:
            queue = CaptureQueue(store)
            _capture(queue, raw, self.config, proof, start)
            assistant = _capture(queue, raw, self.config, proof, assistant_position,
                assistant={'text': self.rows[-1]['payload']['content'][0]['text'], 'message_id': 'answer'})
            if mutate:
                mutate(store, assistant.id)
        source = self.path.read_bytes()
        header = source.splitlines(keepends=True)[0]
        stat = self.path.stat()
        watcher = self.watcher()
        watcher.root.mkdir()
        watcher.state_path.write_text(json.dumps({'version': 1, 'authority': watcher.authority,
            'paths': watcher.paths, 'files': {str(self.path): {'identity': [stat.st_dev, stat.st_ino],
            'header': hashlib.sha256(header).hexdigest(), 'start': start, 'end': len(source),
            'digest': hashlib.sha256(source[start:]).hexdigest()}}}))
        return assistant.id

    def test_v1_migration_adopts_exact_assistant_without_rewriting_immutable_rows(self):
        self.seed_v1()
        before = [(e.id, e.source, e.metadata, e.recorded_at) for e in self.events()]
        with self.watcher() as watcher:
            result = watcher.poll()
            self.assertEqual(result['captured'], 0)
            self.assertEqual(result['gaps'], [])
            self.assertEqual(watcher.poll()['captured'], 0)
        with self.watcher() as watcher:
            self.assertEqual(watcher.poll()['captured'], 0)
            self.assertEqual(json.loads(watcher.state_path.read_text())['version'], 2)
        self.assertEqual([(e.id, e.source, e.metadata, e.recorded_at) for e in self.events()], before)

    def test_already_labeled_exact_assistant_is_adopted_immutably(self):
        def mutate(store, eid):
            with store.transaction(write=True) as connection:
                row = connection.execute('SELECT metadata FROM memory_raw_events WHERE id=?', (eid,)).fetchone()
                metadata = json.loads(row[0])
                metadata.update(source_position_unit='byte', source_position_origin=str(self.path))
                connection.execute('UPDATE memory_raw_events SET metadata=? WHERE id=?', (json.dumps(metadata), eid))
        self.seed_v1(mutate=mutate)
        before = [(e.id, e.metadata, e.recorded_at) for e in self.events()]
        with self.watcher() as watcher:
            self.assertEqual(watcher.poll()['captured'], 0)
        self.assertEqual([(e.id, e.metadata, e.recorded_at) for e in self.events()], before)

    def test_adopting_assistant_preserves_repeated_identical_user_occurrences(self):
        self.seed_v1(repeated=True)
        with self.watcher() as watcher:
            self.assertEqual(watcher.poll()['captured'], 1)
        self.assertEqual(len(self.events()), 3)
        self.assertEqual(sum(e.actor == 'fiction-user' for e in self.events()), 2)

    def test_conflicting_legacy_assistant_provenance_is_rejected_without_duplication(self):
        for field, value in [('prompt', 'Different synthetic body'), ('actor', 'fiction-user'),
                             ('source', 'codex://wrong'), ('id', 'wrong-event-identity')]:
            with self.subTest(field=field):
                self.tearDown(); self.setUp()
                def mutate(store, eid):
                    with store.transaction(write=True) as connection:
                        if field == 'id':
                            # The test is about the raw identity, so remove its referencing seed job first.
                            connection.execute('DELETE FROM memory_capture_jobs WHERE event_id=?', (eid,))
                            connection.execute('DELETE FROM memory_event_positions WHERE event_id=?', (eid,))
                        connection.execute('UPDATE memory_raw_events SET '+field+'=? WHERE id=?', (value, eid))
                self.seed_v1(mutate=mutate)
                before = [(e.id, e.prompt, e.metadata) for e in self.events()]
                with self.watcher() as watcher:
                    with self.assertRaises(IntakeGap): watcher.poll()
                self.assertEqual([(e.id, e.prompt, e.metadata) for e in self.events()], before)

    def test_conflicting_legacy_assistant_byte_origin_or_unit_is_rejected(self):
        for key, value in [('source_position_origin', '/wrong/source'), ('source_position_unit', 'line'),
                           ('native_turn_id', 'wrong-turn'), ('source_position', 1)]:
            with self.subTest(key=key):
                self.tearDown(); self.setUp()
                def mutate(store, eid):
                    with store.transaction(write=True) as connection:
                        row = connection.execute('SELECT metadata FROM memory_raw_events WHERE id=?', (eid,)).fetchone()
                        metadata = json.loads(row[0]); metadata[key] = value
                        connection.execute('UPDATE memory_raw_events SET metadata=? WHERE id=?', (json.dumps(metadata), eid))
                self.seed_v1(mutate=mutate)
                before = [(e.id, e.prompt, e.metadata) for e in self.events()]
                with self.watcher() as watcher:
                    with self.assertRaises(IntakeGap): watcher.poll()
                self.assertEqual([(e.id, e.prompt, e.metadata) for e in self.events()], before)
