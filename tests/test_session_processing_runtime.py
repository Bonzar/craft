"""Startup session selection never wakes or recovers an unrelated queue."""
import fcntl
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from prototypes.assertion_memory.mcp_server import ServiceConfig, WorkerLauncher, _eligible_processing_work, _idle_handoff
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_processing import _recover_selected, _deadline
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.thought_worker_queue import ThoughtWorkQueue


class SessionProcessingRuntimeTest(unittest.TestCase):
    def test_launcher_is_model_free_selected_future_intake_wakes_and_pause_still_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'fixture.sqlite3';config=ServiceConfig(str(path),'codex',model='gpt-6.1-sol',
                embedding_mode='fastembed',embedding_cache=str(Path(directory)/'embedding-cache'),
                processing_session_ids=['selected','selected'])
            self.assertEqual(config.processing_session_ids,('selected',))
            with MemoryStore(path) as store:
                queue=CaptureQueue(store);thoughts=ThoughtStore(store)
                queue.capture_event('other','one','An unrelated source.',source='fixture')
                work=ThoughtWorkQueue(queue);work.enqueue([thoughts.publish('Independent maintenance.')])
                def spawn(args,**kwargs):
                    # Launcher releases the ordinary worker lock before spawning.
                    with open(str(path)+'.worker.lock','a') as lock:
                        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    child_config=json.loads(args[args.index('--worker-config-json')+1])
                    self.assertEqual(child_config['processing_session_ids'],['selected'])
                    return SimpleNamespace(pid=12345)
                with patch.object(ServiceConfig,'components',side_effect=AssertionError('probe initialized providers')), \
                        patch('prototypes.assertion_memory.mcp_server.subprocess.Popen',side_effect=spawn) as child:
                    launcher=WorkerLauncher(config)
                    self.assertFalse(launcher.start()['scheduled']);child.assert_not_called()
                    # Native identity is authoritative over the logical container.
                    queue.capture_event('logical-container','two','Selected future source.',source='fixture',
                        metadata={'native_session_id':'selected'})
                    self.assertTrue(launcher.start()['scheduled'])
                    disabled=ServiceConfig(str(path),'codex',model='gpt-6.1-sol',processing_session_ids=[])
                    self.assertFalse(WorkerLauncher(disabled).start()['scheduled'])
                    with open(str(path)+'.worker.lock','a') as pause:
                        fcntl.flock(pause,fcntl.LOCK_EX|fcntl.LOCK_NB)
                        child.reset_mock();self.assertFalse(launcher.start()['scheduled']);child.assert_not_called()
                for invalid in ('selected',[None],['']):
                    with self.assertRaises(ValueError):ServiceConfig(str(path),'none',processing_session_ids=invalid)

    def test_recovery_and_idle_handoff_keep_unselected_leases_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'fixture.sqlite3';config=ServiceConfig(str(path),'codex',model='gpt-6.1-sol',processing_session_ids=('selected',))
            with MemoryStore(path) as store:
                queue=CaptureQueue(store)
                selected=queue.capture_event('selected','one','Selected source.',source='fixture')
                other=queue.capture_event('other','two','Unselected source.',source='fixture')
                with store.transaction(write=True) as db:
                    db.execute("UPDATE memory_capture_jobs SET status='running',lease_token='original',lease_until=?",(queue._time(600),))
                with store.transaction() as db:before=dict(db.execute('SELECT * FROM memory_capture_jobs WHERE id=?',(other.id,)).fetchone())
                with open(str(path)+'.worker.lock','a') as owner:
                    fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    _recover_selected(queue,config.processing_session_ids)
                    with store.transaction() as db:
                        self.assertEqual(dict(db.execute('SELECT * FROM memory_capture_jobs WHERE id=?',(other.id,)).fetchone()),before)
                        row=db.execute('SELECT status,lease_token,lease_until FROM memory_capture_jobs WHERE id=?',(selected.id,)).fetchone()
                        self.assertEqual(tuple(row),('pending',None,None))
                    self.assertIsNotNone(_deadline(store,config))
                    self.assertFalse(_idle_handoff(owner,lambda:_deadline(store,config)))
                    with store.transaction(write=True) as db:db.execute("UPDATE memory_capture_jobs SET status='completed' WHERE id=?",(selected.id,))
                    self.assertIsNone(_deadline(store,config))
                    self.assertTrue(_idle_handoff(owner,lambda:_deadline(store,config)))
                self.assertFalse(_eligible_processing_work(config))
                with store.transaction() as db:self.assertEqual(dict(db.execute('SELECT * FROM memory_capture_jobs WHERE id=?',(other.id,)).fetchone()),before)


if __name__=='__main__':unittest.main()
