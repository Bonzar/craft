"""Acceptance tests for restoring committed evidence and pending work."""

import tempfile
import unittest
from pathlib import Path

from prototypes.assertion_memory.backup import snapshot_database
from prototypes.assertion_memory.capture import CaptureInput
from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore


class BackupRecoveryTest(unittest.TestCase):
    def test_restore_retains_pinned_evidence_and_unprocessed_capture_from_live_wal(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            live = path / "live.sqlite3"
            backup = path / "backup.sqlite3"
            restored = path / "restored.sqlite3"

            with MemoryStore(live) as store:
                queue = CaptureQueue(store)
                engine = MemoryEngine(store)
                original = queue.capture(CaptureInput(
                    assertion_id="old-rule", text="Choose the first draft.", actor="Fixture",
                    source="chat://first", episode="original", scope="form",
                    metadata={"excerpt": "Choose the first draft.", "kind": "decision"},
                ))
                engine.define_view(ViewSpec("rule", "Default rule", "first draft", root_ids=(original.assertion_id,)))
                version = engine.build_view("rule")
                pending = queue.capture_event(
                    "another-session", "turn-2", "If the draft is missing, choose the published version.",
                    source="chat://second", scope="form",
                )
                self.assertTrue(live.with_name("live.sqlite3-wal").exists())
                snapshot_database(live, backup)
                after_backup = queue.capture(CaptureInput(
                    assertion_id="after-backup", text="A later event.", actor="Fixture",
                    source="chat://later", episode="later",
                ))
                self.assertIsNotNone(store.get_assertion(after_backup.assertion_id))

            snapshot_database(backup, restored)
            with MemoryStore(restored) as reopened:
                recovered_queue = CaptureQueue(reopened)
                recovered_engine = MemoryEngine(reopened)
                self.assertEqual(reopened.get_assertion(original.assertion_id), original)
                self.assertEqual(recovered_engine.get_version(version.id), version)
                self.assertEqual(recovered_queue.get_event(pending.id), pending)
                self.assertEqual(recovered_queue.get(pending.id).status, "waiting_model")
                self.assertEqual(len(reopened.search("first draft")), 1)
                self.assertIsNone(reopened.get_assertion(after_backup.assertion_id))
            with self.assertRaises(FileExistsError):
                snapshot_database(backup, restored)


if __name__ == "__main__":
    unittest.main()
