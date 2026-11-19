"""Exercise per-call selection through the public processing tool."""
import asyncio
import fcntl
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from prototypes.assertion_memory.mcp_server import ServiceConfig, create_server
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore


class McpProcessingFilterTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.database = str(Path(directory) / 'memory.sqlite3')
        self.store = self.enterContext(MemoryStore(self.database))

    def tool(self, sessions=()):
        config = ServiceConfig(self.database, 'subprocess', command=('fake-model',),
                               processing_session_ids=sessions)
        server = create_server(config, background=False, store=self.store)
        return server._tool_manager.get_tool('memory_process').fn

    def capture(self, session_id):
        return CaptureQueue(self.store).capture_event(session_id, 'message', 'A fixture.', source='fixture')

    def test_only_explicit_session_is_admitted_when_background_selection_is_empty(self):
        tool = self.tool()
        self.capture('other')
        with patch('prototypes.assertion_memory.mcp_server.subprocess.Popen') as spawn:
            self.assertFalse(asyncio.run(tool(session_ids=['chosen']))['scheduled'])
            spawn.assert_not_called()
            self.capture('chosen')
            receipt = asyncio.run(tool(limit=3, session_ids=['chosen']))
            self.assertTrue(receipt['scheduled'])
            self.assertEqual(receipt['configured_processing_session_ids'], [])
            argv = spawn.call_args.args[0]
            worker_config = json.loads(argv[argv.index('--worker-config-json') + 1])
            self.assertEqual(worker_config['processing_session_ids'], ['chosen'])
            self.assertEqual(argv[argv.index('--worker-limit') + 1], '3')
            self.assertFalse(asyncio.run(tool())['scheduled'])
            spawn.assert_called_once()

    def test_filter_cannot_expand_the_startup_allowlist_or_override_a_held_lock(self):
        tool = self.tool(('chosen',))
        other = self.capture('other')
        self.capture('chosen')
        with patch('prototypes.assertion_memory.mcp_server.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'outside startup processing selection'):
                asyncio.run(tool(session_ids=['other']))
            with self.assertRaisesRegex(ValueError, 'outside requested processing selection'):
                asyncio.run(tool(session_ids=['chosen'], retry_job_id=other.id))
            with open(self.database + '.worker.lock', 'a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                self.assertFalse(asyncio.run(tool(session_ids=['chosen']))['scheduled'])
            spawn.assert_not_called()

    def test_empty_explicit_selection_is_rejected(self):
        tool = self.tool(None)
        with self.assertRaisesRegex(ValueError, 'session_ids must not be empty'):
            asyncio.run(tool(session_ids=[]))
