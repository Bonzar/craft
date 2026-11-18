"""Synthetic source files only: exact source clock proof and immutable replay."""
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from prototypes.assertion_memory import dialogue
from prototypes.assertion_memory.native_intake import NativeIntake, IntakeGap
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore

class NativeSourceMessageClockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.path = self.root / 'synthetic.jsonl'
        self.clock = datetime.fromisoformat('2026-03-13T12:00:00+00:00')
        self.config = {'db': str(self.root / 'fiction.sqlite'), 'mode': 'fast', 'scope': 'fiction',
            'cwd_roots': [str(self.root)], 'native_session_ids': ['physical'],
            'logical_session_aliases': {'physical': 'logical'}, 'user_actor_id': 'Mira', 'auto_process': False}
        self.append('session_meta', {'id': 'physical', 'cwd': str(self.root),
            'originator': 'Codex Desktop', 'source': 'vscode'}, '2026-03-01T00:00:00Z')

    def append(self, kind, payload, stamp=None):
        row = {'type': kind, 'payload': payload}
        if stamp is not None: row['timestamp'] = stamp
        data = (json.dumps(row) + '\n').encode()
        start = self.path.stat().st_size if self.path.exists() else 0
        with self.path.open('ab') as stream: stream.write(data)
        return {'start': start, 'end': start + len(data), 'hash': hashlib.sha256(data).hexdigest()}

    def turn(self, turn='one', text='Yesterday I repaired a fictional radio.', stamp='2026-03-10T12:00:00Z', completion='2026-03-11T12:00:00Z', trigger=None, completed=True):
        self.append('event_msg', {'type': 'task_started', 'turn_id': turn, 'root_turn_id': turn,
            'turn_attribution': {'turn_trigger': trigger, 'parent_turn_id': None,
                'initiating_agent_path': None, 'root_turn_id': turn}}, '2026-03-09T00:00:00Z')
        response = self.append('response_item', {'type': 'message', 'role': 'user',
            'content': [{'type': 'input_text', 'text': text}],
            'internal_chat_message_metadata_passthrough': {'turn_id': turn, 'content_item_kinds': ['user.text'],
                'content_item_metadata': [{'provenance': {'type': 'user'}}]}}, stamp)
        if completed: self.complete(turn, text, completion)
        return response

    def complete(self, turn='one', text='Yesterday I repaired a fictional radio.', stamp='2026-03-11T12:00:00Z'):
        self.append('event_msg', {'type': 'item_completed', 'thread_id': 'physical', 'turn_id': turn,
            'item': {'type': 'UserMessage', 'content': [{'type': 'text', 'text': text}]}}, stamp)

    def assistant(self, stamp='2026-03-10T12:00:01Z'):
        return self.append('response_item', {'type': 'message', 'id': 'answer', 'role': 'assistant',
            'channel': 'final', 'content': [{'type': 'output_text', 'text': 'Fictional proposal.'}]}, stamp)

    def poll(self):
        def queue(store): return CaptureQueue(store, clock=lambda: self.clock)
        with patch('prototypes.assertion_memory.native_intake.CaptureQueue', side_effect=queue), \
             patch('prototypes.assertion_memory.native_intake.launch_worker', side_effect=AssertionError('no worker')):
            with NativeIntake(self.config, [self.path]) as intake: return intake.poll()

    def events(self):
        with MemoryStore(self.config['db']) as store: return CaptureQueue(store).list_events()

    def reset_cursor(self):
        (Path(self.config['db'] + '.native-intake') / 'cursor.json').unlink()

    def alter_clock(self, event, stamp):
        # Manufacture a historical contradictory raw in this isolated synthetic database.
        metadata = dict(event.metadata)
        metadata['source_message_at'] = stamp
        if 'source_message_clock_basis' not in metadata:
            metadata['source_message_clock_basis'] = {'kind': 'producer', 'field': 'sent_at',
                'source': event.source, 'value': stamp}
        with MemoryStore(self.config['db']) as store, store.transaction(write=True) as db:
            db.execute('UPDATE memory_raw_events SET metadata=? WHERE id=?', (json.dumps(metadata), event.id))

    def strip_clocks(self, receipts=False):
        with MemoryStore(self.config['db']) as store, store.transaction(write=True) as db:
            for row in db.execute('SELECT id,metadata FROM memory_raw_events').fetchall():
                metadata = json.loads(row['metadata'])
                for key in ('source_message_at', 'source_message_clock_basis'): metadata.pop(key, None)
                db.execute('UPDATE memory_raw_events SET metadata=? WHERE id=?', (json.dumps(metadata), row['id']))
            if receipts: db.execute('DELETE FROM native_occurrence_receipts')

    def test_delayed_native_capture_uses_exact_R_not_later_I_and_assistant_own_row(self):
        response = self.turn(); answer = self.assistant()
        self.assertEqual(self.poll()['captured'], 2)
        events = self.events(); user = next(e for e in events if e.actor == 'Mira')
        assistant = next(e for e in events if e.actor == 'assistant')
        self.assertEqual(user.recorded_at, '2026-03-13T12:00:00.000000+00:00')
        self.assertEqual(user.metadata['source_message_at'], '2026-03-10T12:00:00.000000+00:00')
        basis = user.metadata['source_message_clock_basis']
        self.assertEqual(basis['record_kind'], 'response_item/message/user')
        self.assertEqual((basis['start'], basis['end'], basis['sha256']), (response['start'], response['end'], response['hash']))
        self.assertEqual(basis['value'], '2026-03-10T12:00:00Z')
        self.assertEqual(assistant.metadata['source_message_clock_basis']['start'], answer['start'])
        self.assertEqual(assistant.metadata['source_message_at'], '2026-03-10T12:00:01.000000+00:00')
        with MemoryStore(self.config['db']) as store:
            queue = CaptureQueue(store)
            window_records, _ = dialogue._context_records(user, queue.context_events(user.id))
            seen = []
            def boundary(provider, request): seen.append(request); raise RuntimeError('boundary only')
            with patch.object(dialogue, 'response_object', side_effect=boundary), self.assertRaisesRegex(RuntimeError, 'boundary only'):
                dialogue.extract_dialogue(None, user, queue.context_events(user.id))
            self.assertEqual(seen[0].records[0]['source_message_at'], user.metadata['source_message_at'])
            self.assertEqual(seen[0].records[0]['base_available_at'], user.recorded_at)
            self.assertIsNone(seen[0].records[0]['timezone'])
        before = [asdict(e) for e in events]
        self.clock = datetime.fromisoformat('2026-03-14T12:00:00+00:00')
        self.assertEqual(self.poll()['captured'], 0)
        self.reset_cursor(); self.assertEqual(self.poll()['captured'], 0)
        self.assertEqual([asdict(e) for e in self.events()], before)
        self.turn('two', 'Yesterday I checked a fictional lamp.', '2026-03-12T12:00:00Z', trigger='user')
        self.assertEqual(self.poll()['captured'], 1)
        late = next(e for e in self.events() if e.turn_id == 'two')
        self.assertEqual(late.metadata['source_message_at'], '2026-03-12T12:00:00.000000+00:00')
        self.assertEqual(late.recorded_at, '2026-03-14T12:00:00.000000+00:00')

    def test_missing_R_clock_does_not_substitute_I_task_or_session(self):
        self.turn(stamp=None); self.poll()
        self.assertNotIn('source_message_at', self.events()[0].metadata)
        self.assertIsNone(dialogue._event_record(self.events()[0], is_target=True)['source_message_at'])

    def test_invalid_present_native_clock_is_explicit_diagnostic(self):
        self.turn(stamp='2026-03-10T12:00:00')
        with self.assertRaisesRegex((IntakeGap, ValueError), 'clock|source.message'): self.poll()

    def test_clockless_legacy_user_and_assistant_receipt_and_exact_adoption_stay_immutable(self):
        self.turn(); self.assistant(); self.poll()
        for remove_receipts in (False, True):
            self.strip_clocks(receipts=remove_receipts)
            before = [asdict(e) for e in self.events()]
            self.reset_cursor(); self.assertEqual(self.poll()['captured'], 0)
            self.assertEqual([asdict(e) for e in self.events()], before)

    def test_receipt_fast_path_and_missing_receipt_reject_conflicting_known_clock(self):
        self.turn(); self.assistant(); self.poll()
        event = next(e for e in self.events() if e.actor == 'assistant')
        self.alter_clock(event, '2026-03-09T12:00:01.000000+00:00')
        self.reset_cursor()
        with self.assertRaisesRegex((IntakeGap, ValueError), 'clock|source.message'): self.poll()
        with MemoryStore(self.config['db']) as store, store.transaction(write=True) as db:
            db.execute('DELETE FROM native_occurrence_receipts')
        with self.assertRaisesRegex((IntakeGap, ValueError), 'clock|source.message'): self.poll()

    def test_lone_human_R_to_RI_preserves_raw_and_rejects_known_conflict(self):
        self.turn(trigger='user', completed=False); self.poll()
        old = self.events()[0]
        self.assertEqual(old.metadata['source_message_at'], '2026-03-10T12:00:00.000000+00:00')
        self.complete(); self.assertEqual(self.poll()['captured'], 0)
        self.assertEqual(self.events(), [old])
        self.alter_clock(old, '2026-03-09T12:00:00.000000+00:00')
        self.reset_cursor()
        with self.assertRaisesRegex((IntakeGap, ValueError), 'clock|source.message'): self.poll()

    def test_fast_hook_and_recovery_forward_exact_controlled_clock(self):
        from prototypes.assertion_memory import desktop_fast
        self.turn(); self.assistant()
        raw = {'hook_event_name': 'UserPromptSubmit', 'session_id': 'physical', 'turn_id': 'one',
            'cwd': str(self.root), 'transcript_path': str(self.path),
            'prompt': 'Yesterday I repaired a fictional radio.'}
        with patch('prototypes.assertion_memory.queue.CaptureQueue._time',
                   return_value='2026-03-13T12:00:00.000000+00:00'):
            self.assertTrue(desktop_fast.handle_event(raw, self.config))
            desktop_fast.recover(self.config)
        events = self.events()
        self.assertEqual(len(events), 2)
        user = next(e for e in events if e.actor == 'Mira')
        self.assertEqual(user.metadata['source_message_at'], '2026-03-10T12:00:00.000000+00:00')
        before = [asdict(e) for e in events]
        self.assertEqual(self.poll()['captured'], 0)
        self.assertEqual([asdict(e) for e in self.events()], before)
