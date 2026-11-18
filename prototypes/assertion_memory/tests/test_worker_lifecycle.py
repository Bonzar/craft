"""The idle worker releases its flock before deciding its final queue state."""
import fcntl
import tempfile
import unittest

from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.worker import LinkWorker
from prototypes.assertion_memory import mcp_server


class WorkerLifecycleTest(unittest.TestCase):
    def test_capture_after_last_idle_check_is_owned_by_reacquired_worker(self):
        with tempfile.TemporaryDirectory() as directory, MemoryStore(directory+'/memory.sqlite3') as store:
            queue = CaptureQueue(store)
            model = lambda request:('{"source_coverage":{"status":"unresolved","reason":"This ownership fixture leaves source meaning unresolved.","omissions":[]}}'
            if request.task=='Review source material coverage' else '{"assertions":[]}')
            worker = LinkWorker(queue,MemoryEngine(store,model=model,verify_overviews=True),source_first=True)
            with open(directory+'/worker.lock','a') as owner, open(directory+'/worker.lock','a') as contender:
                fcntl.flock(owner,fcntl.LOCK_EX | fcntl.LOCK_NB)
                def after_unlock():
                    # A new launcher can acquire the lock after the old owner
                    # releases it. Simulate capture whose earlier launcher lost.
                    fcntl.flock(contender,fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(contender,fcntl.LOCK_UN)
                    queue.capture_event('fiction','late','Fictional context',source='fixture://late')
                    return worker.seconds_until_work(with_model=True)
                self.assertFalse(mcp_server._idle_handoff(owner,after_unlock))
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(contender,fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(worker.run_once().status,'completed')

    def test_empty_idle_queue_releases_worker_ownership(self):
        with tempfile.TemporaryFile() as owner:
            fcntl.flock(owner,fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertTrue(mcp_server._idle_handoff(owner,lambda:None))


if __name__ == '__main__':
    unittest.main()
