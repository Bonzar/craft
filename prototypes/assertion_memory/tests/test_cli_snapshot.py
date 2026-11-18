"""CLI exposes the exact invocation boundary and separates intake from derived work."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prototypes.assertion_memory import __main__ as cli
from prototypes.assertion_memory.capture import CaptureInput
from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import CandidateSearch
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.worker import LinkWorker


class CliSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "memory.sqlite3"
        self.view_id = "format"
        self.appended = False
        self.append_during_writer = False
        with MemoryStore(self.path) as store:
            self.source(store, "old", "Report format is JSONL.")
            MemoryEngine(store).define_view(ViewSpec("format", "Format", "Report format?", scopes=("fixture",)))

    def source(self, store, key, text, *, scope="fixture", recorded_at="2026-01-01"):
        return store.append_assertion(assertion_id=key, text=text, actor="user", source="test://" + key,
                                      episode="fixture", scope=scope, recorded_at=recorded_at)

    def model(self, request):
        if request.task == "Review grounded memory overview":
            return json.dumps({"status": "accepted", "coverage": "all_claims", "reason": "The fixture sources support the draft.",
                               "issues": [], "context_requests": [], "unknown_needs": []})
        if self.append_during_writer and not self.appended:
            self.appended = True
            with MemoryStore(self.path) as other:
                self.source(other, "new", "Report compression is gzip.")
        return " ".join(row["text"] for row in request.records if "text" in row)

    def command(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), \
                patch.object(cli, "_configuration", return_value=(self.model, CandidateSearch(), "fixture-model")), \
                patch.object(cli, "build_candidate_search", return_value=CandidateSearch()):
            self.assertEqual(cli.main(["--db", str(self.path), *arguments]), 0)
        return output.getvalue()

    def ask(self, *arguments):
        return json.loads(self.command("ask", "--view", self.view_id, *arguments))

    def test_concurrent_append_returns_pinned_evidence_and_explicit_refresh_then_followup_covers_it(self):
        self.append_during_writer = True
        first = self.ask()
        self.assertEqual({row["id"] for row in first["evidence"]}, {"old"})
        self.assertTrue(first["refresh_required"])
        self.assertTrue(first["freshness_check"]["freshness"]["newer_eligible_evidence_unchecked"])
        self.assertEqual(first["freshness_check"]["version_id"], first["id"])
        self.assertEqual(first["query_snapshot"]["source_version"], first["source_version"])
        followup = self.ask()
        self.assertEqual({row["id"] for row in followup["evidence"]}, {"old", "new"})
        self.assertFalse(followup["refresh_required"])
        self.assertGreater(followup["freshness_check"]["id"], first["freshness_check"]["id"])

    def test_raw_intake_requires_refresh_but_unrelated_scope_does_not(self):
        with MemoryStore(self.path) as store:
            queue = CaptureQueue(store)
            queue.capture_event("session", "other", "Other work", source="test://other", scope="other")
        self.assertFalse(self.ask()["refresh_required"])
        with MemoryStore(self.path) as store:
            CaptureQueue(store).capture_event("session", "same", "Report change", source="test://same", scope="fixture")
        result = self.ask()
        self.assertTrue(result["refresh_required"])
        self.assertTrue(result["source_status"]["processing_gaps_apply_to_query"])
        self.assertEqual(result["source_status"]["latest_scope_health"][0]["pending_count"], 1)

    def test_pending_derived_refresh_is_health_information_without_source_gap(self):
        with MemoryStore(self.path) as store:
            queue = CaptureQueue(store)
            queue.capture(CaptureInput("Report colour is blue.", "user", "test://colour", "fixture",
                                       assertion_id="colour", scope="fixture", recorded_at="2026-01-01"))
            worker = LinkWorker(queue, MemoryEngine(store), lambda request: '{"links":[]}', source_first=True)
            job = worker.run_once()
            self.assertEqual((job.status, job.phase), ("pending", "refresh"))
        result = self.ask()
        self.assertFalse(result["refresh_required"])
        self.assertFalse(result["source_status"]["processing_gaps_apply_to_query"])
        self.assertEqual(result["source_status"]["latest_scope_health"][0]["pending_count"], 1)

    def test_future_raw_intake_does_not_stale_explicit_knowledge_cutoff(self):
        with MemoryStore(self.path) as store:
            CaptureQueue(store).capture_event("session", "future", "Report change", source="test://future", scope="fixture",
                                             recorded_at="2026-02-01")
        result = self.ask("--recorded-as-of", "2026-01-15")
        self.assertFalse(result["refresh_required"])
        self.assertFalse(result["source_status"]["processing_gaps_apply_to_query"])
        self.assertEqual(result["source_status"]["latest_scope_health"][0]["pending_count"], 1)

    def test_multiple_view_scopes_include_only_their_queue_health(self):
        self.view_id = "multiple"
        with MemoryStore(self.path) as store:
            MemoryEngine(store).define_view(ViewSpec(self.view_id, "Format", "Report format?", scopes=("fixture", "second")))
            queue = CaptureQueue(store)
            queue.capture_event("session", "second", "Report change", source="test://second", scope="second")
            queue.capture_event("session", "other", "Other change", source="test://other", scope="other")
        result = self.ask()
        self.assertTrue(result["refresh_required"])
        health = {row["scope"]: row["pending_count"] for row in result["source_status"]["latest_scope_health"]}
        self.assertEqual(health, {"fixture": 0, "second": 1})

    def test_global_historical_view_keeps_future_queue_health_separate(self):
        self.view_id = "global"
        with MemoryStore(self.path) as store:
            MemoryEngine(store).define_view(ViewSpec(self.view_id, "Format", "Report format?"))
            CaptureQueue(store).capture_event("session", "future", "Report change", source="test://future", scope="other",
                                             recorded_at="2026-02-01")
        result = self.ask("--recorded-as-of", "2026-01-15")
        self.assertFalse(result["refresh_required"])
        self.assertEqual(result["source_status"]["latest_scope_health"][0]["pending_count"], 1)
        self.assertIsNone(result["source_status"]["latest_scope_health"][0]["scope"])

    def test_live_artifact_exposes_exact_checked_boundary_and_unchecked_new_evidence(self):
        self.append_during_writer = True
        document = self.command("artifact", "--view", "format")
        self.assertIn("Проверено по срезу", document)
        self.assertIn("Новые сведения ещё не проверены", document)
        with MemoryStore(self.path) as store:
            snapshot_id = MemoryEngine(store).view_history("format")[0].id
        pinned = self.command("artifact", "--snapshot", str(snapshot_id))
        self.assertIn(f"Снимок {snapshot_id}", pinned)
        self.assertNotIn("Проверено по срезу", pinned)


if __name__ == "__main__":
    unittest.main()
