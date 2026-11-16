"""Pinned parser checkpoints restore no old source history, graph or receipts."""
import hashlib
import json
import sqlite3
import unittest
from unittest.mock import patch
from . import test_assertion_memory_capture as fixture
from .test_assertion_memory_artifacts import ArtifactRemote,PNG
import base64


class BoundedReplayTest(unittest.TestCase):
    setUp=fixture.CaptureTest.setUp
    append=fixture.CaptureTest.append
    turn=fixture.CaptureTest.turn
    pair=fixture.CaptureTest.pair
    assistant=fixture.CaptureTest.assistant
    poll=fixture.CaptureTest.poll
    messages=fixture.CaptureTest.messages

    def snapshot(self):
        path=self.root/'pinned.sqlite3';target=sqlite3.connect(path);self.store.db.backup(target);target.close()
        return path,hashlib.sha256(path.read_bytes()).hexdigest(),self.store.session('physical')['cursor']
    def restore(self,snapshot,**extra):
        from synthetic_memory_adapter.repair import replay_from_snapshot,BOUNDED_REASON
        path,digest,cursor=snapshot
        return replay_from_snapshot(self.store,'physical',snapshot_path=path,expected_snapshot_sha256=digest,
            expected_snapshot_cursor=cursor,expected_header_sha256=self.store.session('physical')['header_hash'],reason=BOUNDED_REASON,**extra)

    def test_resume_bounded_replay_keeps_precheckpoint_gap_and_old_receipts_recovers_image(self):
        self.turn('OLD HISTORY NEVER IMPORT');self.assistant('OLD ANSWER NEVER IMPORT')
        self.capture.register(self.store,{**self.raw,'source':'resume'});self.poll()
        state=self.store.session('physical');state['gaps']=['oversized_record']
        with self.store.db:self.store.save_session(state)
        saved=self.snapshot();self.turn('new current input','new','new-user')
        self.append({'type':'response_item','payload':{'type':'custom_tool_call','name':'fixture','call_id':'photo'}})
        self.append({'type':'response_item','payload':{'type':'custom_tool_call_output','call_id':'photo','output':[
            {'type':'input_text','text':'All current text stays.'},{'type':'input_image','image_url':'data:image/png;base64,'+base64.b64encode(PNG).decode()}]}})
        import synthetic_memory_adapter.tool_projection as projection
        with patch.object(projection,'project_result',side_effect=ValueError('old parser shape')):old_result=self.poll()
        from synthetic_memory_adapter.frontier import Coordinator
        remote=ArtifactRemote();coordinator=Coordinator(self.store,remote);coordinator.observe('physical',old_result);coordinator.tick()
        before={m['message_id']:(m['arguments'],m['receipt']) for m in self.messages()}
        self.assertEqual(len(before),1)
        repair=self.restore(saved);self.assertEqual(repair['replay_start_offset'],saved[2]);result=self.poll()
        coordinator.observe('physical',result);coordinator.tick()
        after={m['message_id']:(m['arguments'],m['receipt']) for m in self.messages()}
        self.assertTrue(all(after[k]==v for k,v in before.items()));self.assertEqual(len(after),2)
        self.assertNotIn('OLD HISTORY',json.dumps(after));self.assertNotIn('OLD ANSWER',json.dumps(after))
        self.assertEqual(result['gaps'],['oversized_record']);self.assertEqual(len(remote.blobs),1)
        self.assertEqual(self.store.session('physical')['cursor'],self.path.stat().st_size)

    def test_wrong_snapshot_hash_or_active_worker_never_rewinds_current_state(self):
        self.turn('old');self.capture.register(self.store,{**self.raw,'source':'resume'});self.poll();saved=self.snapshot()
        self.turn('current','new','new-user');self.poll();cursor=self.store.session('physical')['cursor']
        from synthetic_memory_adapter.repair import replay_from_snapshot,BOUNDED_REASON
        with self.assertRaises(ValueError):
            replay_from_snapshot(self.store,'physical',snapshot_path=saved[0],expected_snapshot_sha256='wrong',
                expected_snapshot_cursor=saved[2],expected_header_sha256=self.store.session('physical')['header_hash'],reason=BOUNDED_REASON)
        import fcntl
        with (self.root/'worker.lock').open('a+') as lock:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError,'Stop the existing'):self.restore(saved)
        self.assertEqual(self.store.session('physical')['cursor'],cursor)

    def test_fork_checkpoint_replay_never_imports_parent_history_and_preserves_acked_subset(self):
        self.turn('PARENT HISTORY MUST STAY OUT');self.assistant('PARENT ANSWER MUST STAY OUT')
        rows=[json.loads(line) for line in self.path.read_text().splitlines()]
        rows[0]['payload']['forked_from_id']='parent'
        for row in rows:
            if row.get('payload',{}).get('type')=='item_completed':row['payload']['thread_id']='parent'
        self.path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        self.capture.register(self.store,{**self.raw,'source':'fork'});self.poll();saved=self.snapshot()
        self.turn('current fork input','child','child-user')
        for i in range(3):self.append({'type':'response_item','payload':{'type':'message','role':'assistant','id':'commentary-'+str(i),
            'phase':'commentary','content':[{'type':'output_text','text':'Current progress '+str(i)}]}})
        self.append({'type':'response_item','payload':{'type':'message','role':'assistant','id':'final',
            'phase':'final_answer','content':[{'type':'output_text','text':'Current visible final'}]}})
        self.append({'type':'event_msg','payload':{'type':'task_complete','turn_id':'child','last_agent_message':'Current visible final'}})
        mode=self.capture._assistant_mode
        with patch.object(self.capture,'_assistant_mode',side_effect=lambda p:'pending' if p.get('phase')=='commentary' else mode(p)):
            result=self.poll()
        from synthetic_memory_adapter.frontier import Coordinator
        remote=ArtifactRemote();coordinator=Coordinator(self.store,remote);coordinator.observe('physical',result);coordinator.tick()
        before={m['message_id']:(m['arguments'],m['receipt']) for m in self.messages()};self.assertEqual(len(before),2)
        self.restore(saved);result=self.poll();coordinator.observe('physical',result);coordinator.tick()
        after={m['message_id']:(m['arguments'],m['receipt']) for m in self.messages()}
        self.assertEqual(len(after),5);self.assertTrue(all(after[key]==value for key,value in before.items()))
        self.assertNotIn('PARENT HISTORY',json.dumps(after));self.assertNotIn('PARENT ANSWER',json.dumps(after))
        self.assertEqual(coordinator.local('physical')['scan_state'],'current')
        self.assertEqual(coordinator.local('physical')['server_acked_through'],5)
