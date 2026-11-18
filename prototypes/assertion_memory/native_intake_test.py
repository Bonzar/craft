"""Synthetic native transcript fixtures: never inspect Desktop session directories."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .queue import CaptureQueue
from .storage import MemoryStore
from .native_intake import NativeIntake, IntakeGap, IntakeOwned


class NativeIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'explicit.jsonl'
        self.config = {'db': str(self.root / 'memory.sqlite3'), 'mode': 'fast', 'scope': 'fiction',
            'cwd_roots': [str(self.root)], 'native_session_ids': ['physical'],
            'logical_session_aliases': {'physical': 'logical'}, 'user_actor_id': 'fiction-user',
            'auto_process': False}
        self.rows = []
        self.append({'type': 'session_meta', 'payload': {'id': 'physical', 'cwd': str(self.root),
            'originator': 'Codex Desktop', 'source': 'vscode'}})

    def tearDown(self):
        self.temp.cleanup()

    def append(self, row):
        self.rows.append(row)
        with self.path.open('ab') as stream:
            stream.write((json.dumps(row, ensure_ascii=False) + '\n').encode())

    def turn(self, turn='one', text='  Exact fictional input.\nKeep whitespace.  ', trigger=None):
        position = self.path.stat().st_size
        attribution = {'turn_trigger': trigger, 'parent_turn_id': None,
                       'initiating_agent_path': None, 'root_turn_id': turn}
        self.append({'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': turn,
            'root_turn_id': turn, 'turn_attribution': attribution}})
        self.append({'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
            'content': [{'type': 'input_text', 'text': text}],
            'internal_chat_message_metadata_passthrough': {'turn_id': turn,
                'content_item_kinds': ['user.text'], 'content_item_metadata': [{'provenance': {'type': 'user'}}]}}})
        self.append({'type': 'event_msg', 'payload': {'type': 'item_completed', 'thread_id': 'physical',
            'turn_id': turn, 'item': {'type': 'UserMessage', 'content': [{'type': 'text', 'text': text}]}}})
        return position

    def assistant(self, text='Exact assistant context.\nA proposal, not user adoption.', message='answer'):
        self.append({'type': 'response_item', 'payload': {'type': 'message', 'id': message,
            'role': 'assistant', 'channel': 'final', 'content': [{'type': 'output_text', 'text': text}]}})

    def _physical_rows(self):
        offset = 0
        for line in self.path.read_bytes().splitlines(keepends=True):
            row = json.loads(line)
            row['_memory_offset'] = offset
            yield row
            offset += len(line)

    def events(self):
        if not Path(self.config['db']).exists():
            return []
        with MemoryStore(self.config['db']) as store:
            return CaptureQueue(store).list_events()

    def watcher(self):
        return NativeIntake(self.config, [self.path])

    def test_native_capture_without_hooks_models_or_recall_preserves_roles_alias_and_source(self):
        position = self.turn()
        self.assistant()
        with patch('prototypes.assertion_memory.desktop.handle_event', side_effect=AssertionError('no hook')), \
             patch('prototypes.assertion_memory.runtime_config.build_model', side_effect=AssertionError('no model')), \
             patch('prototypes.assertion_memory.desktop.recall', side_effect=AssertionError('no recall')), \
             patch('prototypes.assertion_memory.runtime_config.build_candidate_search', side_effect=AssertionError('no embeddings')):
            with self.watcher() as watcher:
                result = watcher.poll()
        self.assertEqual(result['captured'], 2)
        user, assistant = self.events()
        self.assertEqual(user.prompt, '  Exact fictional input.\nKeep whitespace.  ')
        self.assertEqual((user.actor, assistant.actor), ('fiction-user', 'assistant'))
        self.assertEqual((user.session_id, user.metadata['native_session_id']), ('logical', 'physical'))
        self.assertGreater(user.metadata['source_position'], position)
        self.assertEqual(user.metadata['source_position'], next(row['_memory_offset'] for row in self._physical_rows() if row.get('type') == 'event_msg' and row['payload'].get('type') == 'item_completed'))
        self.assertEqual(user.metadata['transcript_path'], str(self.path))
        self.assertIn('/session/physical/turn/one/', user.source)

    def test_restart_deduplicates_and_captures_future_turn(self):
        self.turn()
        with self.watcher() as watcher:
            watcher.poll()
            self.assistant()
            watcher.poll()
        before = [(event.id, event.recorded_at) for event in self.events()]
        with self.watcher() as watcher:
            self.assertEqual(watcher.poll()['captured'], 0)
            self.turn('two', 'да')
            self.assertEqual(watcher.poll()['captured'], 1)
        self.assertEqual([(e.id, e.recorded_at) for e in self.events()[:2]], before)
        self.assertEqual(len(self.events()), 3)

    def test_partial_line_retries_without_advancing_or_inventing_evidence(self):
        self.turn()
        row = {'type': 'response_item', 'payload': {'type': 'message', 'id': 'partial', 'role': 'assistant',
            'channel': 'final', 'content': [{'type': 'output_text', 'text': 'Partial native final.'}]}}
        encoded = (json.dumps(row) + '\n').encode()
        with self.path.open('ab') as stream:
            stream.write(encoded[:31])
        with self.watcher() as watcher:
            self.assertEqual(watcher.poll()['captured'], 1)
            with self.path.open('ab') as stream:
                stream.write(encoded[31:])
            self.assertEqual(watcher.poll()['captured'], 1)
        self.assertEqual(self.events()[-1].prompt, 'Partial native final.')

    def test_nonhuman_and_crosscwd_or_wrong_physical_identity_fail_closed(self):
        for index, trigger in enumerate(('app_tool_create_thread', 'subagent_message', 'guardian_review', 'auto_resume', 'root_goal_continue')):
            self.turn(str(index), trigger=trigger)
            self.assistant(message=str(index))
        with self.watcher() as watcher:
            self.assertEqual(watcher.poll()['captured'], 0)
        self.assertEqual(self.events(), [])
        for field, value in [('id', 'unallowed'), ('cwd', '/unrelated-fiction')]:
            header = {**self.rows[0], 'payload': {**self.rows[0]['payload'], field: value}}
            other = self.root / (field + '.jsonl')
            other.write_text(json.dumps(header) + '\n')
            with NativeIntake({**self.config, 'db': str(self.root / (field + '.db'))}, [other]) as watcher:
                with self.assertRaises(ValueError):
                    watcher.poll()

    def test_cutoff_does_not_replay_old_turn_and_retains_seed(self):
        self.turn('old', 'Old fictional input.')
        self.assistant('Propose fictional option?', 'seed')
        self.config['capture_after_offsets'] = {'physical': self.path.stat().st_size}
        self.config['bootstrap_assistant_context'] = {'physical': {'turn_id': 'old', 'message_id': 'seed'}}
        self.turn('new', 'да')
        with self.watcher() as watcher:
            watcher.poll()
        self.assertEqual([event.prompt for event in self.events()], ['Propose fictional option?', 'да'])

    def test_replaced_or_truncated_native_file_stops_without_claiming_new_proof(self):
        self.turn()
        with self.watcher() as watcher:
            watcher.poll()
        previous = len(self.events())
        original = self.path.read_bytes()
        replacement = self.root / 'replacement'
        replacement.write_bytes(original)
        os.replace(replacement, self.path)
        with self.watcher() as watcher:
            with self.assertRaisesRegex(IntakeGap, 'replaced'):
                watcher.poll()
        self.assertEqual(len(self.events()), previous)

    def test_restart_streams_tool_backlog_exceeding_old_window(self):
        self.turn()
        with self.watcher() as watcher:
            watcher.poll()
        self.append({'type': 'response_item', 'payload': {'type': 'function_call_output',
            'output': 'x' * (2 * 1024 * 1024 + 1)}})
        with self.watcher() as watcher:
            for _ in range(20):
                if not watcher.poll()['backlog']:
                    break
            else:
                self.fail('bounded slices failed to resume')
        self.assertEqual(len(self.events()), 1)

    def test_exact_lock_owner_rejects_concurrent_watcher(self):
        with self.watcher() as watcher:
            code = ('import json,sys; from pathlib import Path; from prototypes.assertion_memory.native_intake import NativeIntake,IntakeOwned; '
                    'w=NativeIntake(json.loads(sys.argv[1]),[Path(sys.argv[2])]); w.__enter__()')
            child = subprocess.run([sys.executable, '-c', code, json.dumps(self.config), str(self.path)], capture_output=True, text=True)
            self.assertNotEqual(child.returncode, 0)
            self.assertIn('IntakeOwned', child.stderr)
            self.assertEqual(watcher.owner_pid, os.getpid())
        with self.watcher() as watcher:
            self.assertEqual(watcher.owner_pid, os.getpid())

    def test_worker_launch_occurs_only_after_raw_is_committed(self):
        self.turn()
        self.config['auto_process'] = True
        def launch(config):
            self.assertEqual(len(self.events()), 1)
            return {'scheduled': True}
        with patch('prototypes.assertion_memory.native_intake.launch_worker', side_effect=launch) as worker:
            with self.watcher() as watcher:
                watcher.poll()
            worker.assert_called_once()


    def test_same_inode_truncation_and_rewrite_stop_with_explicit_gap(self):
        self.turn()
        with self.watcher() as watcher:
            watcher.poll()
        original = self.path.read_bytes()
        for replacement in (original[:-20], original.replace(b'Exact fictional', b'Other fictional')):
            self.path.write_bytes(replacement)
            with self.watcher() as watcher:
                with self.assertRaises(IntakeGap):
                    watcher.poll()
        self.assertEqual(len(self.events()), 1)

    def test_daemon_automatically_captures_later_native_turn_without_agent_call(self):
        import time
        profile = self.root / 'profile.json'
        profile.write_text(json.dumps(self.config))
        child = subprocess.Popen([sys.executable, '-m', 'prototypes.assertion_memory.native_intake',
            '--config', str(profile), '--transcript', str(self.path), '--interval', '.1'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not Path(self.config['db'] + '.native-intake/cursor.json').exists():
                self.assertIsNone(child.poll())
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.02)
            self.turn('future', 'Automatically observed fictional input.')
            while len(self.events()) != 1:
                self.assertIsNone(child.poll())
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.02)
            self.assistant('Automatically observed assistant context.')
            while len(self.events()) != 2:
                self.assertIsNone(child.poll())
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.02)
        finally:
            child.terminate()
            output, errors = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 0, errors)
        self.assertIn('"captured": 1', output)
        self.assertEqual([event.prompt for event in self.events()],
            ['Automatically observed fictional input.', 'Automatically observed assistant context.'])

    def test_initial_large_transcript_streams_from_header_with_cutoff_preserved(self):
        self.append({'type': 'event_msg', 'payload': {'type': 'fiction_padding', 'text': 'x' * (2 * 1024 * 1024)}})
        cutoff = self.path.stat().st_size
        self.turn('allowed', 'New bounded fictional input.')
        self.config['capture_after_offsets'] = {'physical': cutoff}
        with self.watcher() as watcher:
            for _ in range(20):
                if not watcher.poll()['backlog']:
                    break
            else:
                self.fail('bounded slices failed to resume')
        self.assertEqual(self.events()[0].prompt, 'New bounded fictional input.')

    def test_unproven_native_user_is_never_captured_as_first_role(self):
        self.turn()
        all_bytes = self.path.read_bytes()
        lines = all_bytes.splitlines(keepends=True)
        self.path.write_bytes(b''.join(lines[:-1]))
        with self.watcher() as watcher:
            self.assertEqual(watcher.poll()['captured'], 0)
            self.assertEqual(self.events(), [])
            with self.path.open('ab') as stream:
                stream.write(lines[-1])
            self.assertEqual(watcher.poll()['captured'], 1)
        self.assertEqual(self.events()[0].actor, 'fiction-user')


    def test_derived_only_work_restarts_worker_after_durable_cursor_with_throttling(self):
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).isoformat()
        for status in ('pending', 'waiting_model', 'running', 'failed', 'scheduled'):
            with self.subTest(status=status):
                db = str(self.root / (status + '.sqlite3'))
                config = {**self.config, 'db': db, 'auto_process': True}
                with MemoryStore(db) as store:
                    CaptureQueue(store)
                    with store.transaction(write=True) as connection:
                        connection.execute("""INSERT INTO memory_derived_work
                            (scope,desired_frontier,status,updated_at,available_at,lease_until,next_wake_at)
                            VALUES ('fiction','{}',?,?,?,?,?)""",
                            ('completed' if status == 'scheduled' else status, stamp, stamp,
                             stamp if status == 'running' else None, stamp if status == 'scheduled' else None))
                def launch(profile):
                    cursor = Path(db + '.native-intake/cursor.json')
                    self.assertTrue(cursor.exists(), 'cursor must be durable before worker launch')
                    with MemoryStore(db) as store:
                        self.assertEqual(CaptureQueue(store).list_events(), [])
                    return {'scheduled': True}
                with patch('prototypes.assertion_memory.native_intake.launch_worker', side_effect=launch) as worker:
                    with NativeIntake(config, [self.path]) as watcher:
                        with patch('prototypes.assertion_memory.native_intake.time.monotonic', return_value=0):
                            watcher.poll()
                            self.assertEqual(worker.call_count, 1)
                            watcher.poll()
                            self.assertEqual(worker.call_count, 1)
                        with patch('prototypes.assertion_memory.native_intake.time.monotonic', return_value=31):
                            watcher.poll()
                            self.assertEqual(worker.call_count, 2)

    def test_unchanged_poll_reads_native_proof_without_opening_store_before_probe(self):
        self.turn()
        with self.watcher() as watcher:
            with patch('prototypes.assertion_memory.native_intake.time.monotonic', return_value=0):
                watcher.poll()
                with patch('prototypes.assertion_memory.native_intake.MemoryStore', side_effect=AssertionError('idle poll must not open DB')):
                    with patch.object(watcher, '_read', wraps=watcher._read) as proof_read:
                        self.assertEqual(watcher.poll()['captured'], 0)
                        proof_read.assert_called_once()
                    original = self.path.read_bytes()
                    self.path.write_bytes(original.replace(b'Exact fictional', b'Other fictional'))
                    with self.assertRaisesRegex(IntakeGap, 'bytes changed'):
                        watcher.poll()
                    self.path.write_bytes(original[:-20])
                    with self.assertRaisesRegex(IntakeGap, 'truncated'):
                        watcher.poll()

    def test_nonfinite_poll_interval_is_rejected(self):
        profile = self.root / 'finite-profile.json'
        profile.write_text(json.dumps(self.config))
        for interval in ('nan', 'inf'):
            result = subprocess.run([sys.executable, '-m', 'prototypes.assertion_memory.native_intake',
                '--config', str(profile), '--transcript', str(self.path), '--interval', interval, '--once'],
                capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn('finite', result.stderr)

    def test_explicit_path_and_physical_allowlist_are_required(self):
        with self.assertRaises(ValueError):
            NativeIntake({**self.config, 'native_session_ids': None}, [self.path])
        with self.assertRaises(ValueError):
            NativeIntake(self.config, [])
