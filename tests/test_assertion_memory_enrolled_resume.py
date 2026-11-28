"""A re-opened admitted physical session never imports a previous/crossing turn."""
from datetime import datetime
import hashlib
import os
import unittest
from unittest.mock import patch

from . import test_assertion_memory_capture as fixtures


def ns(iso):
    return int(datetime.fromisoformat(iso).timestamp())*10**9


class ResumeFixture(unittest.TestCase):
    def setUp(self):
        f=self.f=fixtures.CaptureTest(methodName='runTest')
        f.setUp();self.addCleanup(f.doCleanups)
        f.path.unlink()
        f.append({'type':'session_meta','timestamp':'2025-01-01T00:00:00Z',
            'payload':{'id':'physical','cwd':str(f.root),'originator':'Codex Desktop','source':'vscode'}})
        f.config['capture_batch']=True
        self.cutover='2024-01-01T00:00:00Z'
        self.end='2025-01-02T00:00:00+00:00'
        self.resume={**f.raw,'source':'resume'}
        self.marker=f.root/'inactive'/hashlib.sha256(b'physical').hexdigest()

    def start(self,turn,stamp=None):
        f=self.f
        row={'type':'event_msg','payload':{'type':'task_started','turn_id':turn,'root_turn_id':turn,
            'turn_attribution':{'turn_trigger':'user','parent_turn_id':None,
                'initiating_agent_path':None,'root_turn_id':turn}}}
        if stamp:row['timestamp']=stamp
        f.append(row)
        f.pair(turn,turn,'user-'+turn)

    def ended(self):
        self.f.store.set_watching('physical',False)
        os.utime(self.marker,ns=(ns(self.end),ns(self.end)))

    def arm(self,**overrides):
        return self.f.capture.resume_enrolled(self.f.store,{**self.resume,**overrides},fresh_since=self.cutover)

    def test_first_post_resume_turn_logged_early_is_captured_once_without_crossing_turn(self):
        f=self.f
        f.capture.register(f.store,f.raw,fresh_since=self.cutover)
        self.start('already-recorded','2025-01-01T23:50:00Z')
        f.assistant('old completed','old-done')
        self.assertEqual(f.poll()['queued'],2)
        self.start('crossing','2025-01-01T23:59:59Z')
        self.assertEqual(f.poll()['queued'],0)
        cursor=f.store.session('physical')['cursor']
        self.ended()
        f.assistant('old crossing final','crossing-done')
        self.start('historical-tail','2025-01-01T23:59:58Z')
        f.assistant('historical not new','historical-final')
        self.start('timestamp-unproven')
        f.assistant('unproven not new','timestamp-final')
        self.start('first-new','2025-01-02T00:00:00.200000Z')
        # Exact race: first new turn is already on disk before hook.
        f.assistant('new answer','new-final')
        self.start('late-stale','2025-01-01T23:59:59Z')
        f.assistant('late historical final','late-stale-final')
        self.assertEqual(self.arm()['cursor'],cursor)
        self.assertEqual(f.poll()['queued'],2)
        before=f.messages()
        self.assertEqual([m['arguments']['text'] for m in before],
                         ['already-recorded','old completed','first-new','new answer'])
        self.assertEqual(before[2]['arguments']['_capture_batch'],before[3]['arguments']['_capture_batch'])
        self.assertEqual(f.poll()['queued'],0)
        with self.assertRaises((OSError,ValueError)):self.arm()
        self.assertEqual(before,f.messages())

    def test_first_post_resume_visible_agent_turn_needs_card_and_current_time(self):
        f=self.f
        f.capture.register(f.store,f.raw,fresh_since=self.cutover)
        f.poll();self.ended();self.arm()
        def agent(turn,stamp, namespace='codex_app'):
            f.append({'type':'event_msg','timestamp':stamp,
                'payload':{'type':'task_started','turn_id':turn,'root_turn_id':turn,
                'turn_attribution':{'turn_trigger':'delegation','root_turn_id':turn,
                                    'parent_turn_id':'other-turn','initiating_agent_path':['agent']}}})
            f.append({'type':'event_msg','payload':{'type':'item_completed','thread_id':'physical',
                'turn_id':turn,'item':{'type':'FunctionCallOutput','namespace':namespace,'name':'create_thread',
                'id':'input-'+turn,'output':'<codex_delegation>\n  <source_thread_id>55c0d244-eaed-4115-ac28-45d14178eb1e</source_thread_id>\n  <input>Visible '+turn+'</input>\n</codex_delegation>'}}})
            f.assistant('answer '+turn,'answer-'+turn)
        agent('historical','2025-01-01T23:59:59Z')
        agent('private','2025-01-02T00:00:00.100000Z','unrelated')
        self.assertEqual(f.poll()['queued'],0)
        agent('admitted','2025-01-02T00:00:00.200000Z','codex_tui')
        self.assertEqual(f.poll()['queued'],2)
        rows=[m['arguments'] for m in f.messages()]
        self.assertEqual([m['text'] for m in rows],['Visible admitted','answer admitted'])
        self.assertEqual([m['role'] for m in rows],['assistant','assistant'])
        self.assertEqual(rows[0]['_capture_batch'],rows[1]['_capture_batch'])
        self.assertEqual(f.poll()['queued'],0)

    def test_wrong_path_fork_cutover_inode_or_checkpoint_fail_closed(self):
        f=self.f
        f.capture.register(f.store,f.raw,fresh_since=self.cutover)
        self.start('old','2025-01-01T23:00:00Z');f.assistant('old-answer','old-final')
        f.poll();self.ended()
        cursor=f.store.session('physical')['cursor']
        for override in ({'session_id':'foreign'},{'cwd':'/'},{'transcript_path':str(f.root/'other')},
                       {'source':'fork'},{'parent_session_id':'foreign'}):
            with self.subTest(override=override),self.assertRaises((OSError,ValueError)):
                self.arm(**override)
        with self.assertRaises(ValueError):
            f.capture.resume_enrolled(f.store,self.resume,fresh_since='2024-02-01T00:00:00Z')
        with f.path.open('r+b') as stream:
            stream.seek(cursor-2);stream.write(b'!')
        with self.assertRaisesRegex(ValueError,'checkpoint changed'):
            self.arm()
        content=f.path.read_bytes()
        replacement=f.path.with_suffix('.old')
        f.path.rename(replacement);f.path.write_bytes(content)
        with self.assertRaisesRegex(ValueError,'replaced'):
            self.arm()
        self.assertTrue(self.marker.exists())
        self.assertEqual(f.store.session('physical')['cursor'],cursor)

    def test_new_turn_without_completion_stays_out_of_outbox(self):
        f=self.f
        f.capture.register(f.store,f.raw,fresh_since=self.cutover)
        f.poll();self.ended()
        self.arm()
        self.start('new','2025-01-02T00:00:01Z')
        self.assertEqual(f.poll()['queued'],0)
        self.assertEqual(f.messages(),[])
        f.assistant('new final','new-final')
        self.assertEqual(f.poll()['queued'],2)

    def test_session_end_during_poll_cannot_commit_a_crossing_turn(self):
        f=self.f
        f.capture.register(f.store,f.raw,fresh_since=self.cutover)
        f.poll();cursor=f.store.session('physical')['cursor']
        self.start('just-ended','2025-01-01T23:59:59Z')
        f.assistant('cannot publish after lifecycle closes','ended-final')
        process=f.capture._process
        calls=[]
        def end_during_process(*args):
            if not calls:
                self.ended()
            calls.append(True)
            return process(*args)
        with patch.object(f.capture,'_process',side_effect=end_during_process):
            self.assertEqual(f.poll()['status'],'inactive')
        self.assertEqual(f.store.session('physical')['cursor'],cursor)
        self.assertEqual(f.messages(),[])
        self.start('first-new','2025-01-02T00:00:01Z')
        f.assistant('new final','new-final')
        self.arm()
        self.assertEqual(f.poll()['queued'],2)
        self.assertEqual([m['arguments']['text'] for m in f.messages()],['first-new','new final'])
