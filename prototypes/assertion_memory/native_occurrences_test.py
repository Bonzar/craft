"""Fictional protocol regressions; never read the user's native sessions."""
import json
import tracemalloc
import hashlib
import unittest
from unittest.mock import patch

from . import native_intake_test as fixture
from .native_intake import IntakeGap, WINDOW


class NativeOccurrenceTests(unittest.TestCase):
    setUp = fixture.NativeIntakeTests.setUp
    tearDown = fixture.NativeIntakeTests.tearDown
    append = fixture.NativeIntakeTests.append
    events = fixture.NativeIntakeTests.events
    watcher = fixture.NativeIntakeTests.watcher
    assistant = fixture.NativeIntakeTests.assistant
    # Reuse fixture helpers, not old window-policy expectations.
    def pair(self, text='yes', response='r1', item='i1', turn='one'):
        self.append({'type': 'response_item', 'payload': {'type': 'message', 'id': response, 'role': 'user',
            'content': [{'type': 'input_text', 'text': text}],
            'internal_chat_message_metadata_passthrough': {'turn_id': turn, 'content_item_kinds': ['user.text'],
                'content_item_metadata': [{'provenance': {'type': 'user'}}]}}})
        self.append({'type': 'event_msg', 'payload': {'type': 'item_completed', 'thread_id': 'physical',
            'turn_id': turn, 'item': {'id': item, 'type': 'UserMessage', 'content': [{'type': 'text', 'text': text}]}}})

    def start(self, turn='one'):
        self.append({'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': turn, 'root_turn_id': turn,
            'turn_attribution': {'turn_trigger': None, 'parent_turn_id': None,
                'initiating_agent_path': None, 'root_turn_id': turn}}})

    def drain(self, watcher, limit=400):
        total = 0
        for _ in range(limit):
            result = watcher.poll()
            total += result['captured']
            if not result.get('backlog', False):
                return total
        self.fail('bounded polls did not drain')

    def test_identical_physical_occurrences_are_separate_and_restart_idempotent(self):
        self.start()
        self.pair()
        self.pair(response='r2', item='i2')
        with self.watcher() as watcher:
            self.assertEqual(self.drain(watcher), 2)
        events = self.events()
        self.assertEqual(len({e.id for e in events}), 2)
        self.assertEqual(len({e.metadata['source_position'] for e in events}), 2)
        self.assertTrue(all('/message/' in e.source for e in events))
        with self.watcher() as watcher:
            self.assertEqual(self.drain(watcher), 0)
        self.assertEqual([(e.id, e.recorded_at) for e in self.events()], [(e.id, e.recorded_at) for e in events])

    def test_ambiguous_repeated_response_records_do_not_borrow_text_proof(self):
        self.start()
        self.pair()
        rows = self.path.read_bytes().splitlines(keepends=True)
        self.path.write_bytes(b''.join(rows[:-1]) + rows[-2] + rows[-1])
        with self.watcher() as watcher:
            result = watcher.poll()
        self.assertEqual(self.events(), [])
        self.assertTrue(result.get('pending') or result.get('gaps'))

    def test_streams_huge_irrelevant_scalar_with_late_discriminator_and_no_body_capture(self):
        self.start()
        self.pair()
        self.append({'payload': {'content': 'x' * (WINDOW * 4) + '\\"task_started user.text {fake}',
                    'type': 'function_call_output'}, 'type': 'response_item'})
        self.pair(response='r2', item='i2')
        tracemalloc.start()
        try:
            with self.watcher() as watcher:
                self.assertEqual(self.drain(watcher), 2)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, WINDOW * 2)
        self.assertEqual([e.prompt for e in self.events()], ['yes', 'yes'])
        self.assertLess(len((self.root / 'memory.sqlite3.native-intake/cursor.json').read_bytes()), 100000)

    def test_pending_mirror_survives_partial_utf8_restart_and_long_intervening_tool(self):
        self.start()
        self.pair(text='да 🌲')
        lines = self.path.read_bytes().splitlines(keepends=True)
        completed = lines[-1]
        self.path.write_bytes(b''.join(lines[:-1]))
        with self.watcher() as watcher:
            self.assertEqual(self.drain(watcher), 0)
        self.append({'type': 'response_item', 'payload': {'type': 'function_call_output', 'output': 'z' * (WINDOW * 2)}})
        boundary = completed.index('🌲'.encode()) + 2
        with self.path.open('ab') as stream:
            stream.write(completed[:boundary])
        with self.watcher() as watcher:
            self.assertEqual(self.drain(watcher), 0)
        with self.path.open('ab') as stream:
            stream.write(completed[boundary:-1])
        with self.watcher() as watcher:
            self.assertEqual(self.drain(watcher), 0)
            with self.path.open('ab') as stream:
                stream.write(b'\n')
            self.assertEqual(self.drain(watcher), 1)
        self.assertEqual(self.events()[0].prompt, 'да 🌲')

    def test_v1_upgrade_adopts_only_proven_first_legacy_raw_and_crash_replay_is_exact(self):
        from .native_intake import _durable_json
        from .desktop_fast import _capture
        from .queue import CaptureQueue
        from .storage import MemoryStore
        self.start()
        self.pair()
        original = self.path.read_bytes()
        header = original.splitlines(keepends=True)[0]
        start = len(header)
        with self.watcher() as watcher:
            with MemoryStore(self.config['db']) as store:
                queue = CaptureQueue(store)
                old = _capture(queue, {'session_id': 'physical', 'turn_id': 'one', 'prompt': 'yes',
                    'cwd': str(self.root), 'transcript_path': str(self.path)}, self.config,
                    {'origin_kind': 'native-client'}, start)
                with store.transaction() as connection:
                    before = tuple(connection.execute('SELECT * FROM memory_raw_events WHERE id=?', (old.id,)).fetchone())
            stat = self.path.stat()
            _durable_json(watcher.state_path, {'version': 1, 'authority': watcher.authority, 'paths': watcher.paths,
                'files': {str(self.path): {'identity': [stat.st_dev, stat.st_ino],
                    'header': hashlib.sha256(header).hexdigest(), 'start': start, 'end': len(original),
                    'digest': hashlib.sha256(original[start:]).hexdigest()}}})
        self.pair(response='r2', item='i2')
        with self.watcher() as watcher:
            with patch('prototypes.assertion_memory.native_intake._durable_json', side_effect=RuntimeError('crash after receipt')):
                with self.assertRaisesRegex(RuntimeError, 'crash after receipt'):
                    watcher.poll()
        self.assertEqual(len(self.events()), 2)
        with self.watcher() as watcher:
            self.assertEqual(self.drain(watcher), 0)
        with MemoryStore(self.config['db']) as store:
            with store.transaction() as connection:
                self.assertEqual(tuple(connection.execute('SELECT * FROM memory_raw_events WHERE id=?', (old.id,)).fetchone()), before)
                self.assertEqual(connection.execute('SELECT count(*) FROM native_occurrence_receipts').fetchone()[0], 2)

    def test_oversized_interesting_message_is_visible_and_later_independent_pair_survives(self):
        from .native_stream import MAX_BODY
        self.start()
        self.pair()
        self.pair(text='o' * (MAX_BODY + 1), response='too-big-r', item='too-big-i')
        self.pair(text='later', response='r3', item='i3')
        self.assistant('Complete other context.', 'a1')
        with self.watcher() as watcher:
            self.assertEqual(self.drain(watcher), 3)
            result = watcher.poll()
        self.assertTrue(any('oversized interesting user body' in gap for gap in result['gaps']))
        self.assertEqual([e.prompt for e in self.events()], ['yes', 'later', 'Complete other context.'])

    def test_retained_pending_proof_mutation_is_gap_and_restoration_resumes(self):
        self.start()
        self.pair()
        original = self.path.read_bytes()
        lines = original.splitlines(keepends=True)
        self.path.write_bytes(b''.join(lines[:-1]))
        with self.watcher() as watcher:
            watcher.poll()
        self.path.write_bytes(b''.join(lines[:-1]).replace(b'"r1"', b'"xx"'))
        with self.watcher() as watcher:
            with self.assertRaisesRegex(IntakeGap, 'bytes changed'):
                watcher.poll()
        self.path.write_bytes(original)
        with self.watcher() as watcher:
            self.assertEqual(self.drain(watcher), 1)

    def test_body_lookalike_json_has_no_origin_and_cutoff_rejects_entire_old_turn(self):
        self.start()
        self.pair()
        self.config['capture_after_offsets'] = {'physical': self.path.stat().st_size}
        self.pair(text='old steer', response='r2', item='i2')
        self.assistant('old final', 'a1')
        self.append({'type': 'response_item', 'payload': {'type': 'function_call_output',
            'output': '{"type":"event_msg","payload":{"type":"task_started","turn_id":"fake"}}'}})
        self.start('new')
        self.pair(text='new source', response='r3', item='i3', turn='new')
        with self.watcher() as watcher:
            self.assertEqual(self.drain(watcher), 1)
        self.assertEqual([e.prompt for e in self.events()], ['new source'])

    def test_human_lone_response_and_delayed_mirror_keep_one_physical_occurrence(self):
        self.append({'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'one',
            'turn_attribution': {'turn_trigger': 'user'}}})
        self.pair()
        original = self.path.read_bytes().splitlines(keepends=True)
        self.path.write_bytes(b''.join(original[:-1]))
        with self.watcher() as watcher:
            self.assertEqual(self.drain(watcher), 1)
        with self.path.open('ab') as stream:
            stream.write(original[-1])
        with self.watcher() as watcher:
            self.assertEqual(self.drain(watcher), 0)
        self.assertEqual(len(self.events()), 1)
        with self.watcher() as watcher:
            self.assertEqual(self.drain(watcher), 0)

    def test_real_daemon_streams_appends_and_restarts_without_replaying_occurrences(self):
        import subprocess
        import sys
        import time
        profile = self.root / 'daemon-profile.json'
        profile.write_text(json.dumps(self.config))
        def launch():
            return subprocess.Popen([sys.executable, '-m', 'prototypes.assertion_memory.native_intake',
                '--config', str(profile), '--transcript', str(self.path), '--interval', '.1'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def wait_events(child, count):
            deadline = time.monotonic() + 15
            while len(self.events()) != count:
                self.assertIsNone(child.poll())
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.03)
        self.start()
        self.pair()
        child = launch()
        try:
            wait_events(child, 1)
            self.append({'payload': {'output': 'tool text ' * (WINDOW // 2), 'type': 'function_call_output'},
                'type': 'response_item'})
            self.pair(response='r2', item='i2')
            wait_events(child, 2)
        finally:
            child.terminate()
            _, errors = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 0, errors)
        saved = [(e.id, e.recorded_at) for e in self.events()]
        child = launch()
        try:
            self.pair(response='r3', item='i3')
            wait_events(child, 3)
        finally:
            child.terminate()
            _, errors = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 0, errors)
        self.assertEqual([(e.id, e.recorded_at) for e in self.events()[:2]], saved)

    def test_duplicate_keys_and_invalid_escapes_fail_structurally(self):
        initial = self.path.read_bytes()
        for malformed in (b'{"type":"response_item","type":"event_msg"}\n',
                          b'{"type":"response_item","payload":{"output":"bad\\q"}}\n'):
            self.path.write_bytes(initial + malformed)
            with self.watcher() as watcher:
                with self.assertRaisesRegex(IntakeGap, 'structure'):
                    watcher.poll()
            self.assertEqual(self.events(), [])

    def test_excluded_visible_turn_creates_explicit_context_barrier_without_admin_raw(self):
        from .queue import CaptureQueue
        from .storage import MemoryStore
        from .dialogue_context import ContextWindow
        from .dialogue import _context_records, _event_record, MAX_CONTEXT_CHARACTERS
        self.start('human-a')
        self.pair(text='Discuss the fictional roof.', turn='human-a')
        self.assistant('Choose the fictional blue roof?', 'proposal-p1')
        self.append({'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'admin',
            'turn_attribution': {'turn_trigger': 'app_tool_admin'}}})
        self.assistant('Administrative operation completed.', 'admin-visible')
        self.start('human-b')
        self.pair(text='Да.', response='r-b', item='i-b', turn='human-b')
        with self.watcher() as watcher:
            self.drain(watcher)
        with MemoryStore(self.config['db']) as store:
            queue = CaptureQueue(store)
            events = queue.list_events()
            reply = next(event for event in events if event.prompt == 'Да.')
            self.assertFalse(any(event.turn_id == 'admin' for event in events))
            descriptors = reply.metadata.get('unavailable_context_before', [])
            self.assertEqual(len(descriptors), 1)
            self.assertEqual(descriptors[0]['turn_id'], 'admin')
            self.assertEqual(descriptors[0]['reason'], 'excluded_visible_antecedent')
            self.assertNotIn('Administrative operation completed.', json.dumps(descriptors))
            frozen = queue.freeze_input(reply.id)
            before = frozen.events[-2]
            self.assertTrue(before.metadata.get('_source_unavailable'))
            self.assertEqual(before.metadata.get('reason'), 'explicit_source_unavailable')
            initial, _ = _context_records(reply, list(frozen.events))
            window = ContextWindow(reply, list(frozen.events), initial, _event_record, budget=MAX_CONTEXT_CHARACTERS)
            target = next(row for row in window.records() if row['id'] == reply.id)
            self.assertEqual(target['previous_event_id'], before.id)
            self.assertTrue(next(row for row in window.records() if row['id'] == before.id)['context_unavailable'])
            self.assertEqual(window.recover([{'operation': 'read', 'event_id': before.id, 'start': 0, 'end': 1}])[0]['status'], 'unavailable')

    def test_uninterrupted_native_byte_positions_keep_real_immediate_antecedent(self):
        from .queue import CaptureQueue
        from .storage import MemoryStore
        self.start('human-a')
        self.pair(text='Discuss the fictional roof.', turn='human-a')
        self.assistant('Choose the fictional blue roof?', 'proposal-p1')
        self.start('human-b')
        self.pair(text='Да.', response='r-b', item='i-b', turn='human-b')
        with self.watcher() as watcher:
            self.drain(watcher)
        with MemoryStore(self.config['db']) as store:
            queue = CaptureQueue(store)
            reply = next(event for event in queue.list_events() if event.prompt == 'Да.')
            frozen = queue.freeze_input(reply.id)
            self.assertEqual(frozen.events[-2].prompt, 'Choose the fictional blue roof?')
            self.assertFalse(reply.metadata.get('unavailable_context_before'))

    def _run_context_pipeline(self, interposed):
        from .queue import CaptureQueue
        from .storage import MemoryStore
        from .worker import LinkWorker
        from .engine import MemoryEngine
        self.start('human-a')
        self.pair(text='Discuss the fictional roof.', turn='human-a')
        self.assistant('Choose the fictional blue roof?', 'proposal-p1')
        if interposed:
            self.append({'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'admin',
                'turn_attribution': {'turn_trigger': 'app_tool_admin'}}})
            self.assistant('Administrative operation completed.', 'admin-visible')
        self.start('human-b')
        self.pair(text='Да.', response='r-b', item='i-b', turn='human-b')
        self.start('human-c')
        self.pair(text='The fictional gate is round.', response='r-c', item='i-c', turn='human-c')
        with self.watcher() as watcher:
            self.drain(watcher)
        with MemoryStore(self.config['db']) as store:
            queue = CaptureQueue(store)
            proposal = next(event for event in queue.list_events() if event.actor == 'assistant')
            reply = next(event for event in queue.list_events() if event.prompt == 'Да.')
            fact = next(event for event in queue.list_events() if event.prompt == 'The fictional gate is round.')
            seen = []
            def provider(request):
                seen.append(request)
                # Deterministic extractor deliberately offers the stale proposal;
                # independent fixture verifier consumes the actual gap markers.
                target = next((row for row in request.records if row.get('is_target')), {})
                immediate = target.get('previous_event_id')
                blocked = target.get('id') == reply.id and any(row.get('context_unavailable')
                    and (row.get('id') == immediate or row.get('first_event_id') == immediate or row.get('last_event_id') == immediate)
                    for row in request.records)
                if request.task.startswith('Extract'):
                    if target.get('id') == reply.id:
                        return json.dumps({'assertions': [{'text': 'The user agreed to the fictional blue roof.',
                            'quote': reply.prompt, 'kind': 'decision', 'evidence': [{'event_id': proposal.id,
                                'quote': proposal.prompt, 'relation': 'antecedent'}]}]})
                    return json.dumps({'assertions': [fact.prompt] if target.get('id') == fact.id else []})
                if request.task.startswith('Verify contextual'):
                    return json.dumps({'verdicts': [{'index': row['index'], 'status': 'unresolved' if blocked else 'accepted',
                        'reason': 'Immediate antecedent is explicitly unavailable.' if blocked else 'Exact fictional source is supported.'}
                        for row in request.records if row.get('role') == 'interpretation']})
                if request.task.startswith('Repair'):
                    return json.dumps({'assertions': []})
                if request.task == 'Review source material coverage':
                    return json.dumps({'source_coverage': {'status': 'unresolved' if blocked else 'accepted',
                        'reason': 'Unavailable immediate antecedent.' if blocked else 'Fictional target checked.', 'omissions': []}})
                return json.dumps({'links': []})
            LinkWorker(queue, MemoryEngine(store), provider).drain()
            meanings = [row.text for row in store.list_assertions()]
            self.assertIn(fact.prompt, meanings)
            self.assertEqual('The user agreed to the fictional blue roof.' in meanings, not interposed)
            self.assertFalse(any(event.turn_id == 'admin' for event in queue.list_events()))
            history = queue.extraction_history(reply.id)
            self.assertTrue(history)
            if interposed:
                self.assertEqual(history[-1]['source_revision']['status'], 'unresolved')
                for task in ('Extract attributable assertions from the user event', 'Verify contextual assertion interpretations'):
                    relevant = [request for request in seen if request.task == task and any(row.get('id') == reply.id and row.get('is_target') for row in request.records)]
                    self.assertTrue(relevant)
                    self.assertTrue(all(any(row.get('context_unavailable') and str(row.get('id', '')).startswith('explicit-source-gap-') for row in request.records) for request in relevant))
            self.assertEqual(queue.get_event(reply.id).prompt, 'Да.')

    def test_nonhuman_interposition_leaves_durable_reply_unresolved_but_independent_fact_usable(self):
        self._run_context_pipeline(True)

    def test_without_interposition_real_human_assent_resolves_immediate_proposal(self):
        self._run_context_pipeline(False)

    def test_human_missing_thread_policy_never_accepts_present_wrong_thread(self):
        from .native_occurrence import candidate
        row = {'type': 'event_msg', '_memory_offset': 1, 'payload': {'type': 'item_completed',
            'turn_id': 'one', 'item': {'type': 'UserMessage', 'content': [{'type': 'text', 'text': 'fiction'}]}}}
        self.assertIsNotNone(candidate(row, 'physical', 'one', human=True))
        self.assertIsNone(candidate(row, 'physical', 'one', human=False))
        row['payload']['thread_id'] = 'wrong-physical-session'
        self.assertIsNone(candidate(row, 'physical', 'one', human=True))
