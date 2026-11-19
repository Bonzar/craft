"""Synthetic forward-only native capture/outbox; never opens personal sessions."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from . import MODULES_DIR


def load_capture():
    path = MODULES_DIR / 'assertion-memory' / 'lib' / '__init__.py'
    spec = importlib.util.spec_from_file_location('synthetic_memory_adapter', path,
        submodule_search_locations=[str(path.parent)])
    import sys
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
    from synthetic_memory_adapter import native_capture, outbox
    return native_capture, outbox


class CaptureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.capture, outbox = load_capture()
        self.store = outbox.Outbox(self.root / 'outbox.sqlite3')
        self.addCleanup(self.store.close)
        self.path = self.root / 'session.jsonl'
        self.append({'type': 'session_meta', 'payload': {'id': 'physical', 'cwd': str(self.root),
            'originator': 'Codex Desktop', 'source': 'vscode'}})
        self.raw = {'session_id': 'physical', 'cwd': str(self.root), 'transcript_path': str(self.path),
                    'source': 'startup'}
        self.config = {'actor_id': 'fiction-user', 'scope': 'fiction'}

    def append(self, row):
        payload = row.get('payload') if isinstance(row, dict) else None
        if isinstance(payload, dict) and row.get('type')=='event_msg' and payload.get('type')=='task_started':
            self.turn_id = payload['turn_id']
        with self.path.open('ab') as stream:
            stream.write((json.dumps(row, ensure_ascii=False) + '\n').encode())

    def turn(self, text='yes', turn='one', mid='u1', trigger=None):
        self.append({'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': turn,
            'root_turn_id': turn, 'turn_attribution': {'turn_trigger': trigger,
                'parent_turn_id': None, 'initiating_agent_path': None, 'root_turn_id': turn}}})
        self.pair(text, turn, mid)

    def pair(self, text='yes', turn='one', mid='u1'):
        self.append({'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
            'id': 'response-' + mid if mid else None, 'content': [{'type': 'input_text', 'text': text}],
            'internal_chat_message_metadata_passthrough': {'turn_id': turn,
                'content_item_kinds': ['user.text'], 'content_item_metadata': [{'provenance': {'type': 'user'}}]}}})
        self.append({'type': 'event_msg', 'payload': {'type': 'item_completed', 'thread_id': 'physical',
            'turn_id': turn, 'item': {'type': 'UserMessage', 'id': mid,
                'content': [{'type': 'text', 'text': text}]}}})

    def assistant(self, text='proposal', mid='a1', channel='final'):
        self.append({'type': 'response_item', 'payload': {'type': 'message', 'id': mid, 'role': 'assistant',
            'channel': channel, 'content': [{'type': 'output_text', 'text': text}]}})
        if channel == 'final':
            self.append({'type':'event_msg','payload':{'type':'task_complete',
                'turn_id':self.turn_id,'last_agent_message':text}})

    def poll(self):
        return self.capture.poll(self.store, 'physical', self.config)

    def messages(self):
        return self.store.messages('physical')

    def test_first_user_before_startup_hook_and_future_assistant_are_captured(self):
        self.turn()
        self.capture.register(self.store, self.raw)
        self.poll()
        self.assistant()
        self.poll()
        messages = self.messages()
        self.assertEqual([m['arguments']['role'] for m in messages], ['user', 'assistant'])
        self.assertEqual([m['arguments']['text'] for m in messages], ['yes', 'proposal'])
        self.assertTrue(all('/session/physical/turn/one/message/' in m['arguments']['source'] for m in messages))

    def test_all_user_messages_wait_for_the_confirmed_final_reply(self):
        from synthetic_memory_adapter.frontier import Coordinator
        from .test_assertion_memory_frontier import FakeRemote

        self.turn('request')
        self.capture.register(self.store, self.raw)
        remote = FakeRemote()
        coordinator = Coordinator(self.store, remote)
        coordinator.observe('physical', self.poll())
        self.assertEqual(coordinator.tick()['attempted'], 0)
        self.assertEqual(self.messages(), [])

        self.assistant('working on it', 'progress', 'commentary')
        self.pair('steering', 'one', 'u2')
        self.append({'type':'response_item','payload':{'type':'message','role':'assistant',
            'id':'answer','phase':'final_answer','content':[{'type':'output_text','text':'result'}]}})
        coordinator.observe('physical', self.poll())
        self.assertEqual(coordinator.tick()['attempted'], 0)
        self.assertEqual(self.messages(), [])

        self.append({'type':'event_msg','payload':{'type':'task_complete',
            'turn_id':'one','last_agent_message':'result'}})
        coordinator.observe('physical', self.poll())
        self.assertEqual(coordinator.tick()['acknowledged'], 3)
        self.assertEqual([m['arguments']['text'] for m in self.messages()],
                         ['request', 'steering', 'result'])

    def test_only_completed_turn_final_is_queued_once(self):
        self.turn('request')
        self.capture.register(self.store, self.raw)
        for text, phase in [('progress', 'commentary'), ('discarded final', 'final_answer'),
                            ('delivered final', 'final_answer')]:
            self.append({'type':'response_item','payload':{'type':'message','role':'assistant',
                'id':text, 'phase':phase,'content':[{'type':'output_text','text':text}]}})
        self.poll()
        self.assertEqual([m['arguments']['text'] for m in self.messages()], [])

        for turn in ('another', 'one', 'one'):
            self.append({'type':'event_msg','payload':{'type':'task_complete','turn_id':turn,
                'last_agent_message':'delivered final'}})
            self.poll()
            expected = [] if turn == 'another' else ['request', 'delivered final']
            self.assertEqual([m['arguments']['text'] for m in self.messages()], expected)

    def test_interrupted_final_and_commentary_only_turn_produce_no_assistant_message(self):
        self.turn('first request')
        self.append({'type':'response_item','payload':{'type':'message','role':'assistant',
            'phase':'final_answer','content':[{'type':'output_text','text':'interrupted final'}]}})
        self.append({'type':'event_msg','payload':{'type':'turn_aborted','turn_id':'one','reason':'interrupted'}})
        self.append({'type':'event_msg','payload':{'type':'task_complete','turn_id':'one',
            'last_agent_message':'interrupted final'}})
        self.turn('second request', turn='two', mid='u2')
        self.assistant('progress only', 'a1', 'commentary')
        self.append({'type':'event_msg','payload':{'type':'task_complete','turn_id':'two',
            'last_agent_message':'progress only'}})
        self.capture.register(self.store, self.raw)
        self.poll()
        self.assertEqual([m['arguments']['text'] for m in self.messages()],
                         [])
        self.assertEqual(self.store.session('physical')['exclusions']['user_messages_in_turn_without_final'], 2)

    def test_resume_without_registration_starts_after_existing_tail(self):
        self.turn('old')
        self.assistant('old reply')
        self.capture.register(self.store, {**self.raw, 'source': 'resume'})
        self.poll()
        self.assertEqual(self.messages(), [])
        self.turn('new', 'two', 'u2')
        self.assistant('new reply', 'a2')
        self.poll()
        self.assertEqual([m['arguments']['text'] for m in self.messages()], ['new', 'new reply'])

    def test_two_devices_use_identical_producer_id_source_and_position(self):
        self.turn()
        self.assistant()
        self.capture.register(self.store, self.raw)
        self.poll()
        otherpath = self.root / 'copy.jsonl'
        shutil.copyfile(self.path, otherpath)
        _, outbox = load_capture()
        other = outbox.Outbox(self.root / 'other-device.sqlite3')
        self.addCleanup(other.close)
        self.capture.register(other, {**self.raw, 'transcript_path': str(otherpath)})
        self.capture.poll(other, 'physical', self.config)
        self.assertEqual([m['arguments'] for m in self.messages()], [m['arguments'] for m in other.messages('physical')])
        self.assertNotIn(str(self.root), json.dumps([m['arguments'] for m in self.messages()]))

    def test_identical_text_occurrences_are_distinct_and_idless_restart_is_stable(self):
        self.turn(mid=None)
        self.pair(mid=None)
        self.assistant(mid=None)
        self.capture.register(self.store, self.raw)
        self.poll()
        before = self.messages()
        self.assertEqual(len(before), 3)
        self.assertEqual(len({m['arguments']['message_id'] for m in before}), 3)
        self.capture.register(self.store, {**self.raw, 'source': 'resume'})
        self.poll()
        self.assertEqual(before, self.messages())

    def test_only_user_and_visible_assistant_are_automatically_captured_and_acknowledged(self):
        from unittest.mock import patch
        from synthetic_memory_adapter.frontier import Coordinator
        from .test_assertion_memory_frontier import FakeRemote
        from .test_assertion_memory_artifacts import PNG
        import base64
        self.turn()
        self.assistant('secret reasoning','r1','analysis')
        for kind in ('function','custom_tool'):
            self.append({'type':'response_item','payload':{'type':kind+'_call','name':'fixture','call_id':kind,'arguments':'DO-NOT-CAPTURE'}})
            output='observed result' if kind=='function' else [{'type':'input_text','text':'tool-only words'},
                {'type':'input_image','image_url':'data:image/png;base64,'+base64.b64encode(PNG).decode()}]
            self.append({'type':'response_item','payload':{'type':kind+'_call_output','call_id':kind,'output':output}})
        self.append({'type':'response_item','payload':{'type':'function_call_output','call_id':'large','output':'x'*(self.capture.MAX_RECORD+1)}})
        self.assistant('visible progress','a1','commentary');self.assistant('visible final','a2','final')
        self.capture.register(self.store,self.raw)
        with patch.object(self.store,'store_artifact',side_effect=AssertionError('automatic tool artifacts are forbidden')):
            result=self.poll()
        self.assertEqual([m['arguments']['role'] for m in self.messages()],['user','assistant'])
        self.assertEqual([m['arguments']['text'] for m in self.messages()],['yes','visible final'])
        self.assertEqual(result['gaps'],[])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM artifacts').fetchone()[0],0)
        remote=FakeRemote();coordinator=Coordinator(self.store,remote);coordinator.observe('physical',result);coordinator.tick()
        self.assertEqual(coordinator.local('physical')['server_acked_through'],2)
        self.assertEqual(coordinator.local('physical')['scan_state'],'current')
        self.assertTrue(all(m['status']=='acknowledged' for m in self.messages()))
        self.assertEqual(self.store.session('physical')['cursor'],self.path.stat().st_size)

    def test_over_media_cap_keeps_explicit_gap_and_drops_unproven_following_assistant(self):
        from unittest.mock import patch
        self.turn()
        self.append({'type':'response_item','payload':{'type':'function_call_output','output':'x'*4097}})
        self.assistant('No binding may survive this unparsed record')
        self.capture.register(self.store,self.raw)
        with patch.object(self.capture,'MAX_MEDIA_RECORD',4096):
            result=self.poll()
        self.assertEqual(result['gaps'],['oversized_record'])
        self.assertEqual([m['arguments']['text'] for m in self.messages()],[])
        self.assertFalse(any(m['arguments']['role']=='tool' for m in self.messages()))
        self.assertIsNone(self.store.session('physical')['turn'])
        self.turn('Next proven source','next','next-user');self.assistant('Next proven reply','next-answer')
        self.poll()
        self.assertEqual([m['arguments']['text'] for m in self.messages()],['Next proven source','Next proven reply'])

    def test_nonhuman_and_unknown_origin_and_inherited_fork_history_are_excluded(self):
        for i, trigger in enumerate(('automation', 'subagent_message', 'auto_resume', 'unknown-new-trigger')):
            self.turn(turn=str(i), mid=str(i), trigger=trigger)
            self.assistant(mid='a'+str(i))
        self.capture.register(self.store, self.raw)
        self.poll()
        self.assertEqual(self.messages(), [])
        # A separately registered fork starts at its current boundary, even startup.
        fork = self.root / 'fork.jsonl'
        old = self.path
        self.path = fork
        self.append({'type': 'session_meta', 'payload': {'id': 'fork', 'cwd': str(self.root),
            'source': 'vscode', 'originator': 'Codex Desktop', 'forked_from_id': 'physical'}})
        self.turn('inherited')
        self.capture.register(self.store, {**self.raw, 'session_id': 'fork', 'transcript_path': str(fork)})
        self.capture.poll(self.store, 'fork', self.config)
        self.assertEqual(self.store.messages('fork'), [])
        self.path = old

    def test_mismatched_identity_and_replaced_file_fail_closed(self):
        self.turn()
        with self.assertRaises(ValueError): self.capture.register(self.store, {**self.raw, 'session_id': 'wrong'})
        self.capture.register(self.store, self.raw)
        self.poll()
        previous = self.messages()
        copy = self.root / 'replacement'
        shutil.copyfile(self.path, copy)
        copy.replace(self.path)
        with self.assertRaises(ValueError): self.poll()
        self.assertEqual(previous, self.messages())

    def test_partial_record_is_replayed_and_unpaired_user_never_proves_input(self):
        self.turn()
        lines = self.path.read_bytes().splitlines(keepends=True)
        partial = lines[-1]
        self.path.write_bytes(b''.join(lines[:-1])+partial[:22])
        self.capture.register(self.store, self.raw)
        self.poll()
        self.assertEqual(self.messages(), [])
        with self.path.open('ab') as stream: stream.write(partial[22:])
        self.assistant()
        self.poll()
        self.assertEqual(len(self.messages()), 2)

    def test_remote_commit_then_lost_ack_retries_same_arguments_and_keeps_receipts(self):
        self.turn()
        self.assistant()
        self.capture.register(self.store, self.raw)
        self.poll()
        seen, committed = [], {}
        def sender(arguments):
            seen.append(dict(arguments))
            committed[arguments['message_id']] = arguments
            if len(seen) == 1: raise OSError('synthetic lost ACK')
            return {'event': {'id': 'server-event', 'session_id': arguments['session_id'],
                'message_id': arguments['message_id'], 'source': arguments['source']}}
        self.assertEqual(self.store.deliver(sender, limit=1)['acknowledged'], 0)
        self.assertEqual(self.store.deliver(sender, limit=1)['acknowledged'], 1)
        self.assertEqual(seen[0], seen[1])
        self.assertEqual(len(committed), 1)
        self.assertEqual(self.messages()[0]['status'], 'acknowledged')
        self.assertEqual(self.messages()[0]['receipt']['event']['id'], 'server-event')
        self.assertEqual(self.store.deliver(sender)['attempted'], 1)
        self.assertEqual(self.store.deliver(sender)['attempted'], 0)

    def test_error_or_wrong_message_receipt_is_not_acknowledged(self):
        self.turn()
        self.assistant()
        self.capture.register(self.store, self.raw)
        self.poll()
        self.store.deliver(lambda args: {'event': {'id': 'foreign', 'session_id': 'other'}})
        self.assertEqual(self.messages()[0]['status'], 'pending')

    def test_inplace_retained_proof_mutation_is_detected_before_future_capture(self):
        self.turn('yes')
        self.capture.register(self.store, self.raw)
        self.poll()
        self.path.write_bytes(self.path.read_bytes().replace(b'yes', b'bad'))
        self.assistant()
        with self.assertRaisesRegex(ValueError, 'proof bytes changed'): self.poll()
        self.assertEqual(len(self.messages()), 0)

    def test_pending_header_and_pending_source_are_retried_without_missing_first_user(self):
        self.path.unlink()
        self.capture.register(self.store, self.raw)
        self.assertEqual(self.poll()['status'], 'waiting_transcript')
        self.path.write_bytes(b'{"type":')
        self.assertEqual(self.poll()['status'], 'waiting_header')
        self.path.write_bytes(b'')
        self.append({'type':'session_meta','payload':{'id':'physical','cwd':str(self.root),'source':'vscode','originator':'Codex Desktop'}})
        self.turn()
        self.assistant()
        self.poll()
        self.assertEqual(len(self.messages()), 2)

    def test_oversized_visible_record_is_explicit_gap_and_next_proven_turn_still_captures(self):
        self.turn()
        self.append({'type':'response_item','payload':{'type':'message','role':'assistant','channel':'final','content':[{'type':'output_text','text':'x'*(self.capture.MAX_RECORD+1)}]}})
        self.turn('later','two','u2');self.assistant()
        self.capture.register(self.store, self.raw)
        result = self.poll()
        self.assertIn('oversized_record', result['gaps'])
        self.assertEqual([m['arguments']['text'] for m in self.messages()], ['later','proposal'])

    def test_ambiguous_RR_does_not_borrow_a_later_item_as_proof(self):
        self.turn()
        lines = self.path.read_bytes().splitlines(keepends=True)
        self.path.write_bytes(b''.join(lines[:-1])+lines[-2]+lines[-1])
        self.capture.register(self.store, self.raw)
        self.poll()
        self.assertEqual(self.messages(), [])

    def test_unrelated_transcript_is_never_discovered(self):
        unrelated = self.root/'unrelated.jsonl'
        unrelated.write_bytes(self.path.read_bytes())
        self.turn()
        self.capture.register(self.store, self.raw)
        self.poll()
        self.assertEqual([s['path'] for s in self.store.sessions()], [str(self.path)])

    def test_cursor_and_enqueued_messages_roll_back_together_on_conflicting_id(self):
        self.turn('first', mid='same')
        self.capture.register(self.store, self.raw)
        self.poll()
        before = self.store.session('physical')['cursor']
        self.pair('changed', mid='same')
        self.assistant()
        with self.assertRaisesRegex(ValueError, 'conflicting'): self.poll()
        self.assertEqual(self.store.session('physical')['cursor'], before)
        self.assertEqual([m['arguments']['text'] for m in self.messages()], [])

    def test_worker_does_not_send_pending_messages_after_binding_disabled(self):
        self.turn()
        self.assistant()
        self.capture.register(self.store, self.raw)
        self.poll()
        from synthetic_memory_adapter import capture_worker
        from unittest.mock import patch
        binding = {'capture':self.config,'connection':{'url':'https://fiction.test/mcp'}}
        path = self.root/'binding.json'
        path.write_text(json.dumps({'capture':{'enabled':False}}))
        state = self.root/'worker-state'
        state.mkdir()
        # Use the same durable queue location as the worker.
        self.store.db.execute('PRAGMA wal_checkpoint(FULL)')
        import sqlite3
        target = sqlite3.connect(state/'outbox.sqlite3')
        self.store.db.backup(target)
        target.close()
        with patch.object(capture_worker.signal,'signal'), patch.object(capture_worker.Client,'capture',side_effect=AssertionError('must not send')):
            self.assertEqual(capture_worker.run(binding,state,once=True,binding_path=path),0)
        _, outbox = load_capture()
        copy = outbox.Outbox(state/'outbox.sqlite3')
        try: self.assertEqual(copy.messages('physical')[0]['status'],'pending')
        finally: copy.close()

    def test_user_clock_comes_from_R_not_completion_and_absent_R_clock_stays_unknown(self):
        for timestamp in ('2026-11-16T08:00:00Z', None):
            self.turn(turn='clock-'+str(timestamp),mid='clock-'+str(timestamp))
            rows=[json.loads(line) for line in self.path.read_text().splitlines()]
            if timestamp: rows[-2]['timestamp']=timestamp
            rows[-1]['timestamp']='2026-11-16T09:00:00Z'
            self.path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            self.assistant(mid='answer-'+str(timestamp))
        self.capture.register(self.store,self.raw)
        self.poll()
        messages=[m for m in self.messages() if m['arguments']['role']=='user']
        self.assertEqual(messages[0]['arguments']['source_message_at'],'2026-11-16T08:00:00Z')
        self.assertNotIn('source_message_at',messages[1]['arguments'])

    def test_provider_identity_survives_extra_service_lines_in_other_device_copy(self):
        self.turn()
        self.assistant()
        self.capture.register(self.store,self.raw)
        self.poll()
        copied=self.root/'service-lines-copy.jsonl'
        lines=self.path.read_bytes().splitlines(keepends=True)
        copied.write_bytes(lines[0]+b'{"type":"event_msg","payload":{"type":"token_count"}}\n'+b''.join(lines[1:]))
        _,outbox=load_capture()
        other=outbox.Outbox(self.root/'device2.sqlite3')
        self.addCleanup(other.close)
        self.capture.register(other,{**self.raw,'transcript_path':str(copied)})
        self.capture.poll(other,'physical',self.config)
        self.assertEqual([m['arguments'] for m in self.messages()],[m['arguments'] for m in other.messages('physical')])

    def test_explicit_human_lone_R_is_admitted_at_boundary_without_borrowing_I_clock(self):
        self.turn(trigger='user')
        lines=self.path.read_bytes().splitlines(keepends=True)
        response=json.loads(lines[-2]);response['timestamp']='2026-11-16T08:00:00Z'
        self.path.write_bytes(b''.join(lines[:-2])+(json.dumps(response)+'\n').encode())
        self.assistant()
        self.capture.register(self.store,self.raw)
        self.poll()
        self.turn('later','later','later-user')
        self.poll()
        self.assertEqual([m['arguments']['text'] for m in self.messages()],['yes','proposal'])
        self.assertEqual(self.messages()[0]['arguments']['source_message_at'],'2026-11-16T08:00:00Z')

    def test_unverified_native_client_R_is_reported_when_next_turn_begins(self):
        self.turn()
        lines=self.path.read_bytes().splitlines(keepends=True)
        self.path.write_bytes(b''.join(lines[:-1]))
        self.capture.register(self.store,self.raw)
        self.poll()
        self.turn('later','later','later-user');self.assistant()
        result=self.poll()
        self.assertEqual([m['arguments']['text'] for m in self.messages()],['later','proposal'])
        self.assertIn('unverified_native_user_at_turn_end',result['gaps'])

    def test_fork_recovers_current_session_first_prehook_input_but_not_inherited_history(self):
        self.turn('inherited','old','old-user',trigger='user')
        self.assistant('inherited answer','old-answer')
        self.turn('first current','new','new-user',trigger='user')
        self.assistant('current answer','new-answer')
        rows=[json.loads(line) for line in self.path.read_text().splitlines()]
        rows[0]['payload']['id']='fork'
        rows[0]['payload']['forked_from_id']='physical'
        for row in rows:
            if row.get('payload',{}).get('turn_id')=='new' and row.get('payload',{}).get('type')=='item_completed':
                row['payload']['thread_id']='fork'
        self.path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        self.capture.register(self.store,{**self.raw,'session_id':'fork'})
        self.capture.poll(self.store,'fork',self.config)
        self.assertEqual([m['arguments']['text'] for m in self.store.messages('fork')],['first current','current answer'])

    def test_canonical_position_is_unknown_not_a_local_ordinal(self):
        self.turn()
        self.assistant()
        self.capture.register(self.store,self.raw)
        self.poll()
        self.assertTrue(all('position' not in m['arguments'] for m in self.messages()))
        self.assertTrue(all(isinstance(m['position'],int) for m in self.messages()))

    def test_delayed_I_after_human_R_does_not_duplicate_or_change_source_clock_identity(self):
        self.turn(trigger='user')
        lines=self.path.read_bytes().splitlines(keepends=True)
        response=json.loads(lines[-2]);response['timestamp']='2026-11-16T08:00:00Z'
        completed=json.loads(lines[-1]);completed['timestamp']='2026-11-16T09:00:00Z'
        self.path.write_bytes(b''.join(lines[:-2])+(json.dumps(response)+'\n').encode())
        self.assistant()
        self.capture.register(self.store,self.raw)
        self.poll()
        first=self.messages()[0]['arguments']
        self.append(completed)
        self.poll()
        self.assertEqual(len(self.messages()),2)
        self.assertEqual(self.messages()[0]['arguments'],first)
        self.assertIn('response-u1',first['message_id'])
        self.assertEqual(first['source_message_at'],'2026-11-16T08:00:00Z')

    def test_human_singleton_I_allows_absent_thread_but_never_wrong_present_thread(self):
        for suffix,thread in (('absent',None),('wrong','other-physical')):
            self.turn(turn=suffix,mid=suffix,trigger='user')
            rows=[json.loads(line) for line in self.path.read_text().splitlines()]
            rows.pop(-2)
            if thread is None: rows[-1]['payload'].pop('thread_id')
            else: rows[-1]['payload']['thread_id']=thread
            rows[-1]['timestamp']='2026-11-16T09:00:00Z'
            self.path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            self.assistant(mid='answer-'+suffix)
        self.capture.register(self.store,self.raw)
        self.poll()
        self.assertEqual(len(self.messages()),2)
        self.assertIn('absent',self.messages()[0]['arguments']['message_id'])
        self.assertNotIn('source_message_at',self.messages()[0]['arguments'])

    def test_human_RR_ambiguity_is_never_admitted_as_singleton(self):
        self.turn(trigger='user')
        lines=self.path.read_bytes().splitlines(keepends=True)
        self.path.write_bytes(b''.join(lines[:-1])+lines[-2])
        self.assistant()
        self.turn('later','later','later-user');self.assistant(mid='later-answer')
        self.capture.register(self.store,self.raw)
        result=self.poll()
        self.assertEqual([m['arguments']['text'] for m in self.messages()],['later','proposal'])
        self.assertIn('unverified_native_user_at_turn_end',result['gaps'])

    def test_fork_unproven_singleton_input_is_explicit_gap_not_silent_loss(self):
        self.turn(trigger='user')
        rows=[json.loads(line) for line in self.path.read_text().splitlines()]
        rows[0]['payload']['forked_from_id']='parent'
        rows.pop()
        self.path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        self.append({'type':'event_msg','payload':{'type':'task_complete','turn_id':'one'}})
        self.capture.register(self.store,self.raw)
        result=self.poll()
        self.assertEqual(self.messages(),[])
        self.assertIn('fork_input_without_current_session_proof',result['gaps'])

    def test_pending_native_R_is_visible_as_local_pending_source_proof(self):
        self.turn()
        lines=self.path.read_bytes().splitlines(keepends=True)
        self.path.write_bytes(b''.join(lines[:-1]))
        self.capture.register(self.store,self.raw)
        self.poll()
        self.assertTrue(self.store.summary('physical')['pending_source_proof'])

    def test_concurrent_global_project_registration_never_resets_progressed_cursor(self):
        self.turn()
        self.assistant()
        _,outbox=load_capture()
        other=outbox.Outbox(self.root/'outbox.sqlite3')
        self.addCleanup(other.close)
        original=self.capture._header
        from unittest.mock import patch
        entered=False
        progressed=None
        def raced_header(stream,raw):
            nonlocal entered,progressed
            if not entered:
                entered=True
                self.capture.register(other,self.raw)
                self.capture.poll(other,'physical',self.config)
                progressed=other.session('physical')['cursor']
            return original(stream,raw)
        with patch.object(self.capture,'_header',side_effect=raced_header):
            registered=self.capture.register(self.store,self.raw)
        self.assertEqual(registered['cursor'],progressed)
        self.assertEqual(self.store.session('physical')['cursor'],progressed)
        self.assertEqual(len(self.messages()),2)

    def test_harness_service_context_is_excluded_without_gap_or_poisoning_user_pair(self):
        self.turn()
        rows=[json.loads(line) for line in self.path.read_text().splitlines()]
        rows.insert(3,{'type':'response_item','payload':{'type':'message','role':'user','content':[
            {'type':'input_text','text':'# AGENTS.md\nSynthetic service instructions'},
            {'type':'input_text','text':'<environment_context>synthetic</environment_context>'}],
            'internal_chat_message_metadata_passthrough':{'turn_id':'one',
                'content_item_kinds':['agents_md.instructions','environments.environment_context'],
                'content_item_metadata':[{'provenance':{'type':'agents_md'}},{'provenance':{'type':'harness'}}]}}})
        self.path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        self.assistant()
        self.capture.register(self.store,self.raw);result=self.poll()
        self.assertEqual([m['arguments']['text'] for m in self.messages()],['yes','proposal'])
        self.assertEqual(result['gaps'],[])
        self.assertEqual(self.store.session('physical')['exclusions']['harness_service_context'],1)

    def test_automatic_tool_blocks_are_excluded_and_explicit_projection_remains_lossless(self):
        self.turn()
        self.append({'type':'response_item','payload':{'type':'custom_tool_call','name':'exec','call_id':'structured'}})
        payload={'site_status':'draft','conditions':['keep all qualifications','first condition'],
                 'source_repository_credential':'SYNTHETIC_REPO_SECRET_314',
                 'service_bearer':'SYNTHETIC_SERVICE_SECRET_271',
                 'nested':{'Authorization':'Bearer SYNTHETIC_AUTH_SECRET_161'}}
        blocks=[{'type':'input_text','text':'Complete synthetic observation, including exceptions.'},
                {'type':'input_text','text':json.dumps(payload)}]
        self.append({'type':'response_item','payload':{'type':'custom_tool_call_output','call_id':'structured','output':blocks}})
        self.assistant()
        self.capture.register(self.store,self.raw);result=self.poll()
        self.assertEqual([m['arguments']['role'] for m in self.messages()],['user','assistant'])
        from synthetic_memory_adapter.tool_projection import project_result
        text=project_result(blocks)['text'];rendered=json.loads(text)
        self.assertEqual(rendered['format'],'native-tool-output/v1')
        self.assertEqual(rendered['output'][0],blocks[0])
        clean=json.loads(rendered['output'][1]['text'])
        self.assertEqual(clean['site_status'],'draft');self.assertEqual(clean['conditions'],payload['conditions'])
        for secret in ('SYNTHETIC_REPO_SECRET_314','SYNTHETIC_SERVICE_SECRET_271','SYNTHETIC_AUTH_SECRET_161'):self.assertNotIn(secret,text)
        self.assertEqual(rendered['credential_redactions'],3)
        self.assertEqual(result['gaps'],[])
        self.assertEqual(self.store.session('physical')['exclusions']['non_dialogue_custom_tool_call_output'],1)

    def test_genuine_user_multicontent_still_reports_gap(self):
        self.turn()
        rows=[json.loads(line) for line in self.path.read_text().splitlines()]
        rows[-2]['payload']['content'].append({'type':'input_text','text':'another actual user part'})
        self.path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        self.capture.register(self.store,self.raw);result=self.poll()
        self.assertEqual(self.messages(),[])
        self.assertIn('unsupported_user_schema',result['gaps'])

    def test_tool_projection_does_not_redact_ordinary_knowledge_or_credential_key_names(self):
        from synthetic_memory_adapter.tool_projection import project
        output=[{'type':'input_text','text':json.dumps({'token_limit':5000,'secret_note':'a business note',
            'explanation':'Bearer authentication preserves this full explanation and its exceptions.',
            'condition':'Only after the original review, except unfinished cases.'})}]
        text,count=project(output)
        self.assertEqual(count,0)
        self.assertEqual(json.loads(text)['output'],output)

    def test_host_env_credential_fields_are_redacted_without_reading_secret_values(self):
        from unittest.mock import patch
        import os
        secret='00000000-0000-4000-8000-000000000123'
        reference='00000000-0000-4000-8000-000000000456'
        condition='Only after independent review, except unfinished cases.'
        value={'ASSERTION_MEMORY_TOKEN':secret,'condition':condition,'reference_id':reference}
        outputs=[value,json.dumps(value),[{'type':'input_text','text':json.dumps(value)}],
            'ASSERTION_MEMORY_TOKEN='+secret+'\nCONDITION='+condition+'\nREFERENCE='+reference,
            'export ASSERTION_MEMORY_TOKEN="'+secret+'" NEXT_VALUE=keep\n'+condition+'\n'+reference,
            'ASSERTION_MEMORY_TOKEN='+secret+'; CONDITION='+condition+'; REFERENCE='+reference,
            'Final output:\n'+json.dumps(value),
            'Output follows:\n```json\n'+json.dumps(value,indent=2)+'\n```\nEnd of output.']
        for index,output in enumerate(outputs):
            self.turn('User words retain UUID '+reference,turn='env-'+str(index),mid='env-'+str(index))
            self.append({'type':'response_item','payload':{'type':'function_call','name':'fixture','call_id':'env-'+str(index)}})
            self.append({'type':'response_item','payload':{'type':'function_call_output','call_id':'env-'+str(index),'output':output}})
        self.capture.register(self.store,self.raw)
        from synthetic_memory_adapter.tool_projection import project_result
        with patch.object(os.environ,'get',side_effect=AssertionError('projection must not read environment values')):
            self.poll()
            tools=[project_result(output)['text'] for output in outputs]
        self.assertFalse(any(message['arguments']['role']=='tool' for message in self.messages()))
        self.assertEqual(len(tools),len(outputs))
        for text in tools:
            self.assertNotIn(secret,text);self.assertIn(condition,text);self.assertIn(reference,text)
            self.assertIn('[REDACTED:credential]',text)
        self.assertTrue(any('NEXT_VALUE=keep' in text for text in tools))
        self.assertTrue(any('; CONDITION=' in text for text in tools))
        self.assertTrue(any('Final output:' in text for text in tools))
        self.assertTrue(any('```json' in text and 'End of output.' in text for text in tools))
        self.assertTrue(all(reference in message['arguments']['text'] for message in self.messages() if message['arguments']['role']=='user'))
        self.assertEqual(self.store.session('physical')['exclusions']['non_dialogue_function_call_output'],len(outputs))
        from synthetic_memory_adapter.tool_projection import project_result
        for empty in ('ASSERTION_MEMORY_TOKEN=', 'export ASSERTION_MEMORY_TOKEN=""', "ASSERTION_MEMORY_TOKEN='' # remains unset"):
            self.assertEqual(project_result(empty)['text'],empty)
            self.assertEqual(project_result(empty)['redactions'],0)
        self.assertEqual(project_result({'reference_id':secret})['redactions'],0)
        self.assertIn(secret,project_result({'reference_id':secret})['text'])
        for invalid in ('A'*129,'TOKEN[anything]',42):
            with self.assertRaises(ValueError):project_result({},credential_env_var=invalid)

    def test_worker_forwards_configured_credential_name_only_without_changing_binding(self):
        from unittest.mock import patch
        self.turn()
        secret='00000000-0000-4000-8000-000000000789'
        self.append({'type':'response_item','payload':{'type':'function_call','name':'fixture','call_id':'custom-env'}})
        self.append({'type':'response_item','payload':{'type':'function_call_output','call_id':'custom-env',
            'output':{'CUSTOM_AUTH_ENV':secret,'condition':'Preserve this exact exception.',
                'wrapped':'Final output:\n'+json.dumps({'CUSTOM_AUTH_ENV':secret})}}})
        self.assistant('Visible final after excluded tool')
        self.capture.register(self.store,self.raw)
        from synthetic_memory_adapter import capture_worker
        from .test_assertion_memory_frontier import FakeRemote
        binding={'capture':dict(self.config),'connection':{'url':'https://fixture.invalid/mcp','bearer_token_env_var':'CUSTOM_AUTH_ENV'}}
        before=json.loads(json.dumps(binding))
        with patch.object(capture_worker.signal,'signal'),patch.object(capture_worker,'Client',return_value=FakeRemote()), \
                patch.object(capture_worker,'poll',wraps=capture_worker.poll) as observed_poll:
            self.assertEqual(capture_worker.run(binding,self.root,once=True),0)
        self.assertEqual(binding,before)
        self.assertEqual(observed_poll.call_args.args[2]['credential_env_var'],'CUSTOM_AUTH_ENV')
        self.assertEqual([message['arguments']['role'] for message in self.messages()],['user','assistant'])
        self.assertTrue(all(message['status']=='acknowledged' for message in self.messages()))
        self.assertNotIn(secret,json.dumps(self.messages()))
        self.assertNotIn('Preserve this exact exception.',json.dumps(self.messages()))

    def test_nonobject_payload_is_a_gap_and_breaks_source_chain(self):
        expected=[]
        for index,payload in enumerate((None,[],42,'unsupported payload')):
            text='Independent user '+str(index)
            self.turn(text,turn='payload-'+str(index),mid='payload-'+str(index))
            self.append({'type':'event_msg','payload':payload})
            self.assistant('Do not inherit the previous source',mid='unproven-'+str(index))
        expected.append('Next proven turn');self.turn(expected[-1],turn='after-payloads',mid='after-payloads')
        self.assistant()
        expected.append('proposal')
        self.capture.register(self.store,self.raw);result=self.poll()
        self.assertEqual([message['arguments']['text'] for message in self.messages()],expected)
        self.assertEqual(result['gaps'].count('invalid_record'),4)

    def test_nonobject_native_record_is_a_gap_and_breaks_source_chain(self):
        self.turn('Before unsupported record',turn='before',mid='before')
        for row in ([],None,42,'unsupported native envelope'):
            self.append(row)
        self.assistant('Must not cross unknown source boundary',mid='not-proven')
        self.turn('Next independently proven turn',turn='after',mid='after')
        self.assistant()
        self.capture.register(self.store,self.raw);result=self.poll()
        self.assertEqual([message['arguments']['text'] for message in self.messages()],
            ['Next independently proven turn','proposal'])
        self.assertIn('invalid_record',result['gaps'])
        from synthetic_memory_adapter.frontier import Coordinator
        coordinator=Coordinator(self.store,object());coordinator.observe('physical',result)
        self.assertEqual(coordinator.local('physical')['scan_state'],'gapped')

    def test_explicit_parser_replay_preserves_existing_receipts_and_recovers_only_known_session(self):
        self.turn();self.assistant()
        self.capture.register(self.store,self.raw);self.poll()
        def sender(args):return {'event':{'id':'accepted-'+args['message_id'],'session_id':args['session_id'],
            'message_id':args['message_id'],'source':args['source']}}
        self.store.deliver(sender)
        before=[(m['arguments'],m['receipt'],m['status']) for m in self.messages()]
        from synthetic_memory_adapter.repair import reset_parser_checkpoint,REASON
        state=self.store.session('physical')
        result=reset_parser_checkpoint(self.store,'physical',expected_header_sha256=state['header_hash'],reason=REASON)
        self.assertEqual(result['status'],'replay_pending')
        self.assertEqual(self.store.session('physical')['cursor'],0)
        self.poll()
        self.assertEqual(before,[(m['arguments'],m['receipt'],m['status']) for m in self.messages()])
        self.assertEqual(len(self.store.sessions()),1)

    def test_parser_repair_refuses_active_worker_wrong_header_and_resume_history_expansion(self):
        self.turn();self.capture.register(self.store,self.raw);self.poll()
        from synthetic_memory_adapter.repair import reset_parser_checkpoint,REASON
        import fcntl
        state=self.store.session('physical');cursor=state['cursor']
        with (self.root/'worker.lock').open('a+') as lock:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError,'Stop the existing'):
                reset_parser_checkpoint(self.store,'physical',expected_header_sha256=state['header_hash'],reason=REASON)
        with self.assertRaises(ValueError):reset_parser_checkpoint(self.store,'physical',expected_header_sha256='wrong',reason=REASON)
        self.assertEqual(self.store.session('physical')['cursor'],cursor)
        state['registration_source']='resume'
        with self.store.db:self.store.save_session(state)
        with self.assertRaisesRegex(ValueError,'history expansion'):
            reset_parser_checkpoint(self.store,'physical',expected_header_sha256=state['header_hash'],reason=REASON)

    def test_native_phase_commentary_is_excluded_and_frontier_becomes_current(self):
        self.turn()
        for i in range(3):
            self.append({'type':'response_item','payload':{'type':'message','role':'assistant','id':'phase-commentary-'+str(i),
                'phase':'commentary','content':[{'type':'output_text','text':'Synthetic progress '+str(i)}]}})
        self.append({'type':'response_item','payload':{'type':'message','role':'assistant','id':'phase-final',
            'phase':'final_answer','content':[{'type':'output_text','text':'Synthetic final'}]}})
        self.append({'type':'event_msg','payload':{'type':'task_complete','turn_id':'one','last_agent_message':'Synthetic final'}})
        self.capture.register(self.store,self.raw);result=self.poll()
        self.assertEqual([m['arguments']['role'] for m in self.messages()],['user','assistant'])
        self.assertIsNone(self.store.session('physical')['turn']['assistant'])
        from synthetic_memory_adapter.frontier import Coordinator
        coordinator=Coordinator(self.store,object());coordinator.observe('physical',result)
        self.assertEqual(coordinator.local('physical')['scan_state'],'current')

    def test_phase_reasoning_and_analysis_never_admitted_even_if_completion_matches(self):
        for phase in ('analysis','reasoning'):
            for channel in (None,'final','commentary'):
                identity=phase+'-'+str(channel)
                self.turn(turn=identity,mid='user-'+identity)
                self.append({'type':'response_item','payload':{'type':'message','role':'assistant','id':'private-'+identity,
                    'channel':channel,'phase':phase,'content':[{'type':'output_text','text':'Synthetic private '+identity}]}})
                self.append({'type':'event_msg','payload':{'type':'task_complete','turn_id':identity,'last_agent_message':'Synthetic private '+identity}})
        self.capture.register(self.store,self.raw);self.poll()
        self.assertEqual([m['arguments']['role'] for m in self.messages()],[])
        self.assertNotIn('Synthetic private',json.dumps(self.messages()))
        self.assertIsNone(self.store.session('physical')['turn']['assistant'])

    def test_explicit_final_clears_unproven_unknown_phase_instead_of_stale_pending(self):
        self.turn()
        self.append({'type':'response_item','payload':{'type':'message','role':'assistant','id':'unknown-phase',
            'phase':'unknown_future_phase','content':[{'type':'output_text','text':'Not proven visible'}]}})
        self.append({'type':'response_item','payload':{'type':'message','role':'assistant','id':'known-final',
            'phase':'final_answer','content':[{'type':'output_text','text':'Known visible final'}]}})
        self.append({'type':'event_msg','payload':{'type':'task_complete','turn_id':'one','last_agent_message':'Known visible final'}})
        self.capture.register(self.store,self.raw);result=self.poll()
        self.assertEqual([m['arguments']['text'] for m in self.messages()],['yes','Known visible final'])
        self.assertIsNone(self.store.session('physical')['turn']['assistant'])
        from synthetic_memory_adapter.frontier import Coordinator
        coordinator=Coordinator(self.store,object());coordinator.observe('physical',result)
        self.assertEqual(coordinator.local('physical')['scan_state'],'current')

    def test_phase_v2_replay_keeps_dialogue_receipts_and_excludes_commentary(self):
        from unittest.mock import patch
        from synthetic_memory_adapter.frontier import Coordinator
        from .test_assertion_memory_frontier import FakeRemote
        self.turn()
        for i in range(3):
            self.append({'type':'response_item','payload':{'type':'message','role':'assistant','id':'commentary-'+str(i),
                'phase':'commentary','content':[{'type':'output_text','text':'Synthetic commentary '+str(i)}]}})
        for i in range(30):
            self.append({'type':'response_item','payload':{'type':'custom_tool_call','name':'synthetic-tool','call_id':'call-'+str(i)}})
            output=[{'type':'input_text','text':json.dumps({'condition':'preserve exception '+str(i),'source_repository_credential':None})}] if i<18 else 'Synthetic scalar result '+str(i)
            self.append({'type':'response_item','payload':{'type':'custom_tool_call_output','call_id':'call-'+str(i),'output':output}})
        self.append({'type':'response_item','payload':{'type':'message','role':'assistant','id':'final',
            'phase':'final_answer','content':[{'type':'output_text','text':'Synthetic visible final'}]}})
        self.append({'type':'event_msg','payload':{'type':'task_complete','turn_id':'one','last_agent_message':'Synthetic visible final'}})
        self.capture.register(self.store,self.raw)
        mode=self.capture._assistant_mode
        def old_mode(payload):return 'pending' if payload.get('phase')=='commentary' and payload.get('channel') is None else mode(payload)
        with patch.object(self.capture,'_assistant_mode',side_effect=old_mode):before_result=self.poll()
        remote=FakeRemote();coordinator=Coordinator(self.store,remote);coordinator.observe('physical',before_result);coordinator.tick()
        before={m['message_id']:(m['arguments'],m['receipt'],m['status']) for m in self.messages()}
        self.assertEqual(len(before),2);self.assertTrue(all(value[2]=='acknowledged' for value in before.values()))
        from synthetic_memory_adapter.repair import reset_parser_checkpoint,REASON
        self.assertTrue(REASON.endswith('_v2'))
        reset_parser_checkpoint(self.store,'physical',expected_header_sha256=self.store.session('physical')['header_hash'],reason=REASON)
        result=self.poll();coordinator.observe('physical',result);coordinator.tick()
        after={m['message_id']:(m['arguments'],m['receipt'],m['status']) for m in self.messages()}
        self.assertEqual(len(after),2);self.assertTrue(all(after[key]==value for key,value in before.items()))
        self.assertTrue(all(value[2]=='acknowledged' for value in after.values()))
        self.assertEqual(coordinator.local('physical')['scan_state'],'current')
        self.assertEqual(coordinator.local('physical')['server_acked_through'],2)
        self.assertEqual(sum(m['arguments']['role']=='assistant' for m in self.messages()),1)
        self.assertEqual(sum(m['arguments']['role']=='tool' for m in self.messages()),0)
        self.assertIsNone(self.store.session('physical')['turn']['assistant'])
