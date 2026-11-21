from __future__ import annotations

import io
import json
import stat
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stderr
from unittest.mock import patch

from apps.graph_viewer.server.providers.json_snapshot import validate_document
from apps.memory_sites.export_approved import export_approved, main
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_graph import version_node_id
from prototypes.assertion_memory.thought_store import ThoughtStore


class ApprovedExportTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db_path = self.root / "source.sqlite3"
        self.memory = MemoryStore(self.db_path)
        self.addCleanup(self.memory.close)
        self.store = ThoughtStore(self.memory)
        self.queue = CaptureQueue(self.memory)

    def publish(self, text, scope="house", **kwargs):
        return self.store.publish(text, origin={"scope": scope, "actor": "Hidden Actor",
                                               "source": "Private absolute source"}, **kwargs)

    def export(self, scopes=("house",), name="approved.json"):
        path = self.root / name
        summary = export_approved(self.db_path, path, scopes)
        return path, summary, json.loads(path.read_text())

    def test_wal_snapshot_filters_transitive_bases_and_raw_provenance(self):
        self.assertEqual(self.memory._connection.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        good_raw = self.queue.capture_event("hidden session", "turn", "RAW EXACT SECRET",
                                            source="private raw source", scope="house")
        bad_raw = self.queue.capture_event("hidden session", "turn-two", "OTHER RAW SECRET",
                                           source="private raw source", scope="private")
        good = self.publish("Approved canonical text", bases=[{"event_id": good_raw.id,
                                                               "span": {"start": 0, "end": 3}}])
        old = self.publish("Approved thought v1")
        summary = self.publish("Approved overview", bases=[{"thought_id": old.id, "version": 1}],
                               metadata={"title": "Hidden metadata title"}, question="Hidden query")
        latest = self.publish("Approved thought v2", thought_id=old.id, expected_version=1)
        other = self.publish("Unapproved source text", scope="private")
        blocked = self.publish("Not approved via thought base", bases=[{"thought_id": other.id}])
        self.publish("Not approved transitively", bases=[{"thought_id": blocked.id}])
        self.publish("Unapproved raw dependency", bases=[{"event_id": bad_raw.id}])
        self.store.connect(good.id, latest.id, "Hidden semantic relationship interpretation")
        self.store.connect(good.id, other.id, "Cross-scope relation")
        before = self.memory._connection.total_changes

        path, counts, doc = self.export()
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(self.memory._connection.total_changes, before)
        self.assertIs(validate_document(doc), doc)
        ids = {n["id"] for n in doc["nodes"]}
        self.assertEqual(ids, {version_node_id(n.id, n.version) for n in (good, old, latest, summary)})
        self.assertEqual(counts, {"nodes": 4, "edges": 2, "overviews": 1, "topic_links": 0})
        self.assertEqual(doc["episodes"], [])
        self.assertEqual(doc["overviews"][0]["versions"][0]["member_ids"],
                         [version_node_id(summary.id, summary.version), version_node_id(old.id, old.version)])
        persisted = path.read_text()
        for datum in [good_raw.id, bad_raw.id, other.id, "RAW", "Hidden",
                      "private raw source", "Private absolute source", "Cross-scope", "Unapproved",
                      "source_quote", "evidence_refs", "source_span", "target_span"]:
            self.assertNotIn(datum, persisted)

    def test_refuses_empty_and_unknown_scope_and_never_overwrites(self):
        self.publish("approved")
        for scopes in ((), ("*",), ("none",), ("house", "none"), "house"):
            with self.assertRaises(ValueError):
                self.export(scopes)
            self.assertFalse((self.root / "approved.json").exists())
        good, _, _ = self.export()
        original = good.read_bytes()
        with self.assertRaises(FileExistsError):
            self.export()
        self.assertEqual(good.read_bytes(), original)
        (self.root / "link.json").symlink_to(self.root / "missing")
        with self.assertRaises(FileExistsError):
            self.export(name="link.json")

    def test_import_caps_fail_without_file_or_sensitive_diagnostic(self):
        self.publish("approved synthetic")
        destination = self.root / "too-large.json"
        args = ["--source", str(self.db_path), "--scope", "house", "--output", str(destination)]
        for name, value, message in [
            ("MAX_BYTES", 5, "Sites import size limit"),
            ("COUNT_LIMITS", {"nodes": 0}, "Sites record limit"),
        ]:
            stream = io.StringIO()
            with patch("apps.memory_sites.export_approved." + name, value), redirect_stderr(stream):
                self.assertEqual(main(args), 1)
            self.assertIn(message, stream.getvalue())
            self.assertNotIn(str(self.root), stream.getvalue())
            self.assertNotIn("approved synthetic", stream.getvalue())
            self.assertFalse(destination.exists())

    def test_revision_does_not_track_disallowed_data(self):
        self.publish("approved")
        _, _, first = self.export(name="first.json")
        self.publish("New unapproved thought", scope="private")
        _, _, second = self.export(name="second.json")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
