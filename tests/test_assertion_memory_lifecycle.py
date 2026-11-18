"""Session hooks control synthetic transcript watching, independently of delivery."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from . import test_assertion_memory_capture as capture_tests
from .test_assertion_memory_frontier import FakeRemote


class MemoryLifecycleTest(unittest.TestCase):
    setUp = capture_tests.CaptureTest.setUp
    append = capture_tests.CaptureTest.append
    turn = capture_tests.CaptureTest.turn
    pair = capture_tests.CaptureTest.pair
    assistant = capture_tests.CaptureTest.assistant
    poll = capture_tests.CaptureTest.poll
    messages = capture_tests.CaptureTest.messages

    def context(self, event_name, **raw):
        import synthetic_memory_adapter as library
        from jarvis.event import Event
        from jarvis.storage import Storage
        binding = {'harness': 'codex', 'server_name': 'assertion_memory',
            'endpoint_sha256': 'fixture', 'connection': {},
            'capture': {**self.config, 'enabled': True, 'state_dir': str(self.root)}}
        path = self.root / 'module' / 'mcp' / 'installed.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(binding))
        source = {**self.raw, **raw}
        event = Event(event_name, source['session_id'], source['cwd'], 'codex', raw=source)
        with patch.object(library, '__file__', str(path.parent.parent / 'lib' / '__init__.py')), \
                patch.object(library.subprocess, 'Popen'):
            return library.context(event, Storage(self.root / 'hook-state', 'physical'))

    def hook_store(self):
        from synthetic_memory_adapter.outbox import Outbox
        store = Outbox(self.root / 'fixture' / 'outbox.sqlite3')
        self.addCleanup(store.close)
        return store

    def run_worker(self, remote):
        from synthetic_memory_adapter import capture_worker
        binding = {'capture': self.config, 'connection': {'url': 'https://fiction.test/mcp'}}
        with patch.object(capture_worker.signal, 'signal'), \
                patch.object(capture_worker, 'Client', return_value=remote):
            return capture_worker.run(binding, self.root, once=True)

    def test_session_end_disables_only_its_registered_session_without_reading_transcript(self):
        self.context('session-start')
        store = self.hook_store()
        store.register_session({'session_id': 'other', 'watching': True})
        before = store.session('physical')['cursor']
        self.path.unlink()
        self.assertEqual(self.context('session-end'), '')
        self.assertFalse(store.session('physical')['watching'])
        self.assertEqual(store.session('physical')['cursor'], before)
        self.assertTrue(store.session('other')['watching'])

    def test_session_end_does_not_register_an_unknown_session_or_spawn_worker(self):
        self.assertEqual(self.context('session-end'), '')
        self.assertIsNone(self.hook_store().session('physical'))

    def test_resume_reenables_watching_without_resetting_cursor_or_receipts(self):
        self.context('session-start')
        store = self.hook_store()
        self.turn()
        self.capture.poll(store, 'physical', self.config)
        before = store.messages('physical')
        cursor = store.session('physical')['cursor']
        self.context('session-end')
        self.context('session-start', source='resume')
        self.assertTrue(store.session('physical')['watching'])
        self.assertEqual(store.session('physical')['cursor'], cursor)
        self.capture.poll(store, 'physical', self.config)
        self.assertEqual(store.messages('physical'), before)

    def test_worker_does_not_read_or_heartbeat_a_stopped_transcript(self):
        self.capture.register(self.store, self.raw)
        self.turn()
        self.poll()
        remote = FakeRemote()
        self.run_worker(remote)
        self.store.set_watching('physical', False)
        before = self.store.session('physical')['cursor']
        self.turn('unread after end', 'two', 'u2')
        remote.calls.clear()
        with patch.object(Path, 'open', side_effect=AssertionError('no transcript reads')):
            # The worker itself opens its lock; test the parser guard directly here.
            self.assertEqual(self.poll()['status'], 'inactive')
        self.run_worker(remote)
        self.assertEqual(self.store.session('physical')['cursor'], before)
        self.assertEqual(remote.calls, [])
        self.assertEqual(len(self.messages()), 1)

    def test_pending_delivery_is_retried_after_watching_stops(self):
        self.capture.register(self.store, self.raw)
        self.turn()
        self.poll()
        self.store.set_watching('physical', False)
        self.path.unlink()
        self.run_worker(FakeRemote())
        self.assertEqual(self.messages()[0]['status'], 'acknowledged')

    def test_parser_save_cannot_undo_a_concurrent_session_end(self):
        self.capture.register(self.store, self.raw)
        stale = self.store.session('physical')
        self.store.set_watching('physical', False)
        stale['cursor'] = 123
        with self.store.db:
            self.store.save_session(stale)
        self.assertFalse(self.store.session('physical')['watching'])
        self.assertEqual(self.store.session('physical')['cursor'], 123)

    def test_session_end_stops_watching_while_another_session_holds_the_queue_write_lock(self):
        self.context('session-start')
        store = self.hook_store()
        store.db.execute('BEGIN IMMEDIATE')
        try:
            self.assertEqual(self.context('session-end'), '')
            self.assertFalse(store.session('physical')['watching'])
        finally:
            store.db.rollback()

    def test_stopped_session_retries_missing_receipts_even_when_server_prefix_is_acknowledged(self):
        from synthetic_memory_adapter.frontier import Coordinator
        self.capture.register(self.store, self.raw)
        self.turn()
        self.poll()
        self.store.set_watching('physical', False)
        remote = FakeRemote()
        now = 1000
        coordinator = Coordinator(self.store, remote, clock=lambda: now)
        remote.fail_capture = True
        coordinator.tick()
        now += 65
        remote.fail_capture = True
        coordinator.tick()
        self.assertEqual(coordinator.local('physical')['server_acked_through'], 1)
        self.assertEqual(self.messages()[0]['status'], 'pending')
        now += 65
        coordinator.tick()
        self.assertEqual(self.messages()[0]['status'], 'acknowledged')

    def test_session_end_is_translated_and_accepts_a_silent_response(self):
        from jarvis import events
        from jarvis.response import Silence
        from jarvis.wrappers import codex
        event = codex.to_event({'hook_event_name': 'SessionEnd', 'session_id': 'physical',
            'cwd': str(self.root), 'reason': 'other'})
        self.assertEqual(event.event, events.SESSION_END)
        self.assertTrue(codex.translate(event.event, Silence()).supported)
