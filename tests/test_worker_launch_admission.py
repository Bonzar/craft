import fcntl
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from prototypes.assertion_memory.mcp_server import ServiceConfig,WorkerLauncher


class WorkerLaunchAdmissionTest(unittest.TestCase):
    def test_active_or_paused_queue_does_not_spawn_another_process(self):
        with tempfile.TemporaryDirectory() as folder:
            database=str(Path(folder)/'fixture.db')
            launcher=WorkerLauncher(ServiceConfig(database,'codex',model='gpt-6.1-sol'))
            with open(database+'.worker.lock','a') as owner:
                fcntl.flock(owner,fcntl.LOCK_EX)
                with patch('prototypes.assertion_memory.mcp_server.subprocess.Popen') as spawn:
                    self.assertFalse(launcher.start()['scheduled'])
                    spawn.assert_not_called()

    def test_released_lock_launches_with_no_lock_held_by_the_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            database=str(Path(folder)/'fixture.db')
            launcher=WorkerLauncher(ServiceConfig(database,'codex',model='gpt-6.1-sol'))
            def spawn(*args,**kwargs):
                with open(database+'.worker.lock','a') as child:
                    fcntl.flock(child,fcntl.LOCK_EX|fcntl.LOCK_NB)
                return SimpleNamespace(pid=1234)
            with patch('prototypes.assertion_memory.mcp_server.subprocess.Popen',side_effect=spawn):
                self.assertTrue(launcher.start()['scheduled'])
