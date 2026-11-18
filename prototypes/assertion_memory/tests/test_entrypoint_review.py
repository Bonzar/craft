"""CLI/Desktop share reviewed-overview policy without doing model work in hooks."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prototypes.assertion_memory import __main__ as cli, desktop
from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.runtime_config import model_identity
from prototypes.assertion_memory.overview_review import MAX_DRAFT
from prototypes.assertion_memory.storage import MemoryStore


class EntryPointReviewTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = {"db": str(self.root / "memory.sqlite3"), "scope": "fixture",
                       "cwd_roots": [str(self.root)],
                       "environment": {"ASSERTION_MEMORY_EMBEDDING_MODE": "lexical",
                                       "ASSERTION_MEMORY_MODEL_MODE": "codex"}}

    def source(self, store):
        store.append_assertion(assertion_id="format", text="The report format is JSONL.", actor="user",
                               source="test://format", episode="one", scope="fixture", recorded_at="2026-01-01")

    def test_cli_enables_review_for_every_configured_model_and_disables_it_without_one(self):
        options = [([], False), (["--generator", "codex"], True),
                   (["--generator", "local", "--model-name", "fixture"], True),
                   (["--generator", "command", "--model-command", '["fixture-model"]'], True)]
        for arguments, enabled in options:
            with self.subTest(arguments=arguments), contextlib.redirect_stdout(io.StringIO()), \
                    patch.dict("os.environ", {"ASSERTION_MEMORY_EMBEDDING_MODE": "lexical"}), \
                    patch.object(cli, "MemoryEngine", wraps=MemoryEngine) as factory:
                self.assertEqual(cli.main(["--db", self.config["db"], *arguments,
                                           "view", "fixture", "Report format?"]), 0)
                self.assertEqual(factory.call_args.kwargs.get("verify_overviews", False), enabled)

    def test_cli_prompt_export_matches_writer_policy_without_constructing_model_or_running_review(self):
        for generator in ("codex", "extractive"):
            output = io.StringIO()
            with self.subTest(generator=generator), contextlib.redirect_stdout(output), \
                    patch.dict("os.environ", {"ASSERTION_MEMORY_EMBEDDING_MODE": "lexical"}), \
                    patch.object(cli, "CodexModel", side_effect=AssertionError("export must not construct a model")), \
                    patch.object(cli, "MemoryEngine", wraps=MemoryEngine) as factory:
                self.assertEqual(cli.main(["--db", self.config["db"], "--generator", generator,
                                           "ask", "--question", "Report format?", "--prompt"]), 0)
                self.assertIsNone(factory.call_args.args[1])
                self.assertEqual(factory.call_args.kwargs["model_key"], "extractive")
                self.assertEqual(factory.call_args.kwargs["verify_overviews"], generator != "extractive")
                self.assertEqual(
                    ("max_encoded_characters=" + str(MAX_DRAFT + 2) +
                     " JSON-string characters including quotes and escaping") in output.getvalue(),
                    generator != "extractive")

    def test_worker_enables_review_for_real_model_and_disables_it_for_extractive(self):
        for mode in ("codex", "extractive"):
            self.config["environment"]["ASSERTION_MEMORY_MODEL_MODE"] = mode
            with self.subTest(mode=mode), patch.object(desktop, "MemoryEngine", wraps=MemoryEngine) as factory:
                self.assertEqual(desktop.run_worker(self.config)["status"], "finished")
            self.assertEqual(factory.call_args.kwargs.get("verify_overviews", False), mode != "extractive")

    def test_recall_keeps_reviewed_worker_cache_fresh_without_constructing_or_calling_model(self):
        requests = []

        def model(request):
            requests.append(request)
            if request.task == "Review grounded memory overview":
                return json.dumps({"status": "accepted", "coverage": "all_claims", "reason": "The selected source supports the claim.",
                                   "issues": [], "context_requests": [], "unknown_needs": []})
            return "The report format is JSONL."

        with MemoryStore(self.config["db"]) as store:
            self.source(store)
            engine = MemoryEngine(store)
            engine.define_view(ViewSpec("desktop-format", "Format", "Report format?", scopes=("fixture",)))
        with patch("prototypes.assertion_memory.runtime_config.build_model", return_value=model):
            outcome = desktop.run_worker(self.config)
        with MemoryStore(self.config["db"]) as store:
            engine = MemoryEngine(store)
            saved = engine.get_version(outcome["refreshed"][0])
            self.assertIsNotNone(saved.overview_review, "the background worker must save a reviewed version")
            self.assertEqual(saved.overview_review["status"], "accepted")
            self.assertTrue(any(request.task == "Review grounded memory overview" for request in requests))
            with patch("prototypes.assertion_memory.runtime_config.build_model",
                       side_effect=AssertionError("recall must not construct a model")), \
                    patch.object(desktop, "MemoryEngine", wraps=MemoryEngine) as factory:
                result = desktop.recall(store, "Report format?", self.config)
            self.assertTrue(factory.call_args.kwargs["verify_overviews"])
            self.assertEqual(result["summary_status"], "fresh")
            self.assertEqual(result["summary"], saved.summary)
            self.assertEqual(result["version_id"], saved.id)
            self.assertEqual(len(engine.view_history("desktop-format")), 1)

    def test_recall_does_not_report_legacy_unreviewed_cache_as_fresh(self):
        with MemoryStore(self.config["db"]) as store:
            self.source(store)
            engine = MemoryEngine(store, model=lambda request: "The report format is JSONL.",
                                  model_key=model_identity(environ=desktop._environment(self.config)))
            engine.define_view(ViewSpec("desktop-format", "Format", "Report format?", scopes=("fixture",)))
            saved = engine.build_view("desktop-format")
            with patch("prototypes.assertion_memory.runtime_config.build_model",
                       side_effect=AssertionError("recall must not construct a model")):
                result = desktop.recall(store, "Report format?", self.config)
            self.assertEqual(result["summary_status"], "stale")
            self.assertEqual(result["version_id"], saved.id)
            self.assertEqual(len(engine.view_history("desktop-format")), 1)

    def test_extractive_recall_matches_worker_pipeline_without_review(self):
        self.config["environment"]["ASSERTION_MEMORY_MODEL_MODE"] = "extractive"
        with MemoryStore(self.config["db"]) as store:
            self.source(store)
            engine = MemoryEngine(store, model_key=model_identity(environ=desktop._environment(self.config)))
            engine.define_view(ViewSpec("desktop-format", "Format", "Report format?", scopes=("fixture",)))
            saved = engine.build_view("desktop-format")
            with patch.object(desktop, "MemoryEngine", wraps=MemoryEngine) as factory:
                result = desktop.recall(store, "Report format?", self.config)
            self.assertFalse(factory.call_args.kwargs.get("verify_overviews", False))
            self.assertEqual(result["summary_status"], "fresh")
            self.assertEqual(result["version_id"], saved.id)
            self.assertIsNone(saved.overview_review)


if __name__ == "__main__":
    unittest.main()
