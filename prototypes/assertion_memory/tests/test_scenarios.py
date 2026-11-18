"""Black-box scenarios for the memory prototype, using synthetic chat excerpts."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prototypes.assertion_memory.artifact import render_artifact
from prototypes.assertion_memory.capture import CaptureInput
from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import Candidate, ModelError
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.worker import LinkWorker


FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "clone_scenarios.json").read_text(encoding="utf-8")
)
MESSAGES = {message["assertion_id"]: message for message in FIXTURE["assertions"]}
VIEWS = {view["id"]: view for view in FIXTURE["views"]}
BASE = ("clone-ui-version", "clone-collection", "clone-bff-source", "clone-bff-rollback")


class OneSeedSearch:
    """Make the exception impossible to reach through ranking alone."""

    def __init__(self, seed):
        self.seed = seed
        self.selections = []

    def rank(self, query, candidates, *, limit):
        selection = []
        if limit and self.seed in candidates:
            selection.append(Candidate(self.seed, 1.0, "fixture"))
        self.selections.append((limit, tuple(hit.id for hit in selection)))
        return selection


class FixtureModel:
    def __init__(self):
        self.fail = False
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if self.fail:
            raise ModelError("fixture model unavailable")
        return "Сводка по предоставленным свидетельствам."


def evidence(version):
    return {record["id"]: record for record in version.evidence}


class CloneScenarioTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.path = self.directory / "scenario.sqlite3"
        self.store = MemoryStore(self.path)
        self.addCleanup(self.store.close)
        self.engine = MemoryEngine(self.store)

    def load(self, *ids, store=None):
        store = store or self.store
        with store.transaction(write=True):
            for assertion_id in ids:
                store.append_assertion(**MESSAGES[assertion_id])
            for edge in FIXTURE["edges"]:
                if (
                    store.get_assertion(edge["from_id"]) is not None
                    and store.get_assertion(edge["to_id"]) is not None
                ):
                    store.append_edge(**edge)

    def define_ui(self, engine=None):
        (engine or self.engine).define_view(ViewSpec(**VIEWS["ui"]))

    def define_bff(self, engine=None):
        (engine or self.engine).define_view(ViewSpec(**VIEWS["bff"]))

    def test_exception_is_retrieved_when_ranking_returns_only_the_general_rule(self):
        self.load(*BASE)
        search = OneSeedSearch("clone-collection")
        engine = MemoryEngine(self.store, candidate_search=search)
        engine.define_view(ViewSpec(**VIEWS["collection"]))
        initial = engine.build_view("collection")
        self.assertNotIn("widget-collection-exception", evidence(initial))
        self.load("widget-collection-exception")
        self.assertEqual(engine.inspect_view("collection")["status"], "stale")
        self.assertIn("collection", engine.impact("widget-collection-exception"))

        version = engine.build_view("collection")
        self.assertNotEqual(initial.id, version.id)
        self.assertIn((1, ("clone-collection",)), search.selections)
        records = evidence(version)
        self.assertIn("clone-collection", records)
        self.assertIn("widget-collection-exception", records)
        self.assertEqual(records["widget-collection-exception"]["disposition"], "exception")
        self.assertNotIn("clone-bff-source", records)
        self.assertNotEqual(
            records["clone-collection"]["episode"], records["widget-collection-exception"]["episode"]
        )

    def test_partial_revision_and_proposal_leave_other_decisions_intact(self):
        self.load(*BASE, "clone-ui-version-revised", "clone-ui-version-proposal")
        self.define_ui()
        self.define_bff()

        ui = evidence(self.engine.build_view("ui"))
        bff = evidence(self.engine.build_view("bff"))
        self.assertEqual(ui["clone-ui-version"]["disposition"], "superseded")
        self.assertEqual(ui["clone-ui-version-revised"]["disposition"], "decision")
        self.assertEqual(ui["clone-ui-version-proposal"]["disposition"], "proposal")
        self.assertEqual(ui["clone-collection"]["disposition"], "decision")
        self.assertEqual(bff["clone-bff-source"]["disposition"], "decision")
        self.assertEqual(bff["clone-bff-rollback"]["disposition"], "decision")

    def test_conditional_replacement_keeps_original_rule_in_the_model_request(self):
        self.load("clone-ui-version")
        condition = "при открытии формы из виджета"
        self.store.append_assertion(
            assertion_id="widget-default", text=f"Черновик выбран {condition}.",
            actor="Участник", source="chat://other-session", episode="widget-session",
            scope="clone/ui", valid_at="2026-09-16", recorded_at="2026-09-16",
            metadata={"kind": "decision"},
        )
        self.store.append_edge(
            edge_id="conditional-default", from_id="widget-default", to_id="clone-ui-version",
            kind="supersedes", recorded_at="2026-09-16", metadata={"condition": condition},
        )
        model = FixtureModel()
        engine = MemoryEngine(self.store, model=model)
        self.define_ui(engine)

        result = engine.build_view("ui")
        requested = {record["id"]: record for record in model.requests[0].records}
        self.assertEqual(requested["clone-ui-version"]["disposition"], "decision")
        self.assertEqual(requested["widget-default"]["disposition"], "decision")
        self.assertIn(condition, model.requests[0].instructions)
        self.assertEqual(result.edges[0]["metadata"]["condition"], condition)

    def test_current_extractive_overview_surfaces_edge_only_condition_and_pins_prior_artifact(self):
        self.load("clone-ui-version")
        self.define_ui()
        previous = self.engine.build_view("ui")
        previous_artifact = render_artifact("Форма клонирования", previous)
        condition = "только при открытии из виджета"
        revision = "Форма предвыбирает черновик."
        self.store.append_assertion(
            assertion_id="widget-default", text=revision, actor="Участник",
            source="chat://other-session", episode="widget-session", scope="clone/ui",
            recorded_at="2026-09-16", metadata={"kind": "decision"},
        )
        self.store.append_edge(
            edge_id="conditional-default", from_id="widget-default", to_id="clone-ui-version",
            kind="supersedes", recorded_at="2026-09-16", metadata={"condition": condition},
        )
        current = self.engine.build_view("ui")
        self.assertEqual(evidence(current)["clone-ui-version"]["disposition"], "decision")
        self.assertIn(f"Условие для [widget-default] относительно [clone-ui-version]: {condition}", current.summary)
        self.assertEqual(current.summary.count(revision), 1)
        self.assertIn(condition, render_artifact("Форма клонирования", current))
        self.assertNotIn(condition, previous_artifact)
        self.assertEqual(
            render_artifact("Форма клонирования", self.engine.get_version(previous.id)), previous_artifact,
        )

    def test_background_replacement_distinguishes_explicit_revision_from_overlap(self):
        old = MESSAGES["clone-ui-version"]
        old_id = old["assertion_id"]
        for revised, expected_status, old_disposition in (
            ("Решили: вместо опубликованной версии форма предвыбирает черновик.", "applied", "superseded"),
            ("Форма предвыбирает черновик.", "pending", "decision"),
        ):
            with self.subTest(revised=revised), MemoryStore(self.directory / f"{expected_status}.sqlite3") as store:
                store.append_assertion(**old)
                queue = CaptureQueue(store)
                queue.capture(CaptureInput(
                    assertion_id="revision", text=revised, actor=old["actor"], source="chat://later",
                    episode="later", scope=old["scope"], valid_at="2026-09-18",
                    recorded_at="2026-09-18", metadata={"kind": "decision"},
                ))
                engine = MemoryEngine(store, candidate_search=OneSeedSearch(old_id))

                def propose(_):
                    return json.dumps({"links": [{
                        "target_id": old_id, "kind": "supersedes",
                        "source_quote": revised, "target_quote": old["text"], "condition": None,
                    }]})

                job = LinkWorker(queue, engine, propose).run_once()
                self.assertEqual(job.proposals[0]["status"], expected_status)
                engine.define_view(ViewSpec("default", "Предвыбор", "Версия", root_ids=(old_id, "revision")))
                version = engine.build_view("default")
                self.assertEqual(evidence(version)[old_id]["disposition"], old_disposition)

    def test_branch_becomes_stale_when_a_known_decision_takes_effect(self):
        self.load(*BASE, "clone-ui-version-revised")
        self.define_ui()
        store_version = self.store.version
        with patch(
            "prototypes.assertion_memory.engine._now", return_value="2026-09-16T09:59:59.000000+00:00"
        ):
            previous = self.engine.build_view("ui")
            self.assertEqual(self.engine.inspect_view("ui")["status"], "fresh")
            self.assertEqual(evidence(previous)["clone-ui-version"]["disposition"], "decision")
            self.assertNotIn("clone-ui-version-revised", evidence(previous))

        with patch(
            "prototypes.assertion_memory.engine._now", return_value="2026-09-16T10:00:00.000000+00:00"
        ):
            self.assertEqual(self.engine.inspect_view("ui")["status"], "stale")
            active = self.engine.build_view("ui")
            self.assertEqual(self.engine.inspect_view("ui")["status"], "fresh")
        self.assertEqual(self.store.version, store_version)
        self.assertNotEqual(active.id, previous.id)
        self.assertEqual(evidence(active)["clone-ui-version"]["disposition"], "superseded")
        self.assertEqual(evidence(active)["clone-ui-version-revised"]["disposition"], "decision")

    def test_observation_cannot_replace_a_decision_by_cross_kind_link(self):
        self.load(*BASE, "clone-ui-version-revised", "clone-ui-old-observation")
        self.store.append_edge(
            edge_id="observation-versus-decision",
            from_id="clone-ui-old-observation", to_id="clone-ui-version-revised", kind="supersedes",
            recorded_at="2026-09-21T10:01:00Z",
        )
        self.define_ui()

        records = evidence(self.engine.build_view("ui"))
        self.assertEqual(records["clone-ui-version"]["disposition"], "superseded")
        self.assertEqual(records["clone-ui-old-observation"]["disposition"], "conflict")
        self.assertEqual(records["clone-ui-version-revised"]["disposition"], "conflict")

    def test_cyclic_replacements_preserve_both_as_conflicting(self):
        self.load(*BASE, "clone-ui-version-revised")
        self.store.append_edge(
            edge_id="reverse-ui-version-replacement",
            from_id="clone-ui-version", to_id="clone-ui-version-revised", kind="supersedes",
            recorded_at="2026-09-21T10:01:00Z",
        )
        self.define_ui()

        version = self.engine.build_view("ui")
        records = evidence(version)
        self.assertEqual(records["clone-ui-version"]["disposition"], "conflict")
        self.assertEqual(records["clone-ui-version-revised"]["disposition"], "conflict")
        self.assertIn("[clone-ui-version]", version.summary)
        self.assertIn("[clone-ui-version-revised]", version.summary)

    def test_historical_observation_depends_on_both_time_axes(self):
        self.load(*BASE, "clone-ui-version-revised", "clone-ui-old-observation")
        self.define_ui()
        event_cutoff = "2026-09-13T00:00:00Z"
        older_record_cutoff = "2026-09-20T00:00:00Z"
        later_record_cutoff = "2026-09-24T00:00:00Z"

        active_version = self.engine.build_view("ui")
        before_import = evidence(
            self.engine.build_view(
                "ui", valid_as_of=event_cutoff, recorded_as_of=older_record_cutoff
            )
        )
        after_import = evidence(
            self.engine.build_view(
                "ui", valid_as_of=event_cutoff, recorded_as_of=later_record_cutoff
            )
        )
        current = evidence(active_version)

        self.assertEqual(self.engine.inspect_view("ui")["current_id"], active_version.id)
        self.assertNotIn("clone-ui-old-observation", before_import)
        self.assertIn("clone-ui-old-observation", after_import)
        self.assertEqual(after_import["clone-ui-version"]["disposition"], "decision")
        self.assertNotIn("clone-ui-version-revised", after_import)
        self.assertEqual(current["clone-ui-version"]["disposition"], "superseded")
        self.assertEqual(current["clone-ui-version-revised"]["disposition"], "decision")
        self.assertNotEqual(
            current["clone-ui-old-observation"]["valid_at"],
            current["clone-ui-old-observation"]["recorded_at"],
        )

    def test_disagreement_remains_unresolved_across_chats(self):
        self.load(*BASE)
        self.define_bff()
        original = self.engine.build_view("bff")
        self.load("clone-bff-dispute")
        disputed = self.engine.build_view("bff")
        records = evidence(disputed)
        left = records["clone-bff-source"]
        right = records["clone-bff-dispute"]
        self.assertEqual(left["disposition"], "conflict")
        self.assertEqual(right["disposition"], "conflict")
        self.assertNotEqual(left["source"], right["source"])
        self.assertIn("[clone-bff-source]", disputed.summary)
        self.assertIn("[clone-bff-dispute]", disputed.summary)
        self.assertRegex(disputed.summary.casefold(), r"спор|противореч|конфликт")
        self.assertEqual(records["clone-bff-rollback"]["disposition"], "decision")
        self.assertEqual(
            evidence(self.engine.view_history("bff")[0]), evidence(original)
        )

    def test_branch_freshness_propagates_to_dependents_without_global_rebuild(self):
        self.load(*BASE)
        self.define_ui()
        self.define_bff()
        self.engine.define_view(ViewSpec(**VIEWS["integration"]))
        ui_v1 = self.engine.build_view("ui")
        bff_v1 = self.engine.build_view("bff")
        integration_v1 = self.engine.build_view("integration")
        self.assertEqual(integration_v1.upstream, {"ui": ui_v1.id, "bff": bff_v1.id})

        self.load("unrelated-receipt")
        self.assertEqual(self.engine.impact("unrelated-receipt"), [])
        for view_id, version in (("ui", ui_v1), ("bff", bff_v1), ("integration", integration_v1)):
            with self.subTest(view=view_id):
                self.assertEqual(self.engine.inspect_view(view_id)["status"], "fresh")
                self.assertEqual(self.engine.build_view(view_id).id, version.id)

        self.load("clone-ui-version-revised")
        self.assertEqual(set(self.engine.impact("clone-ui-version-revised")), {"ui", "integration"})
        self.assertEqual(self.engine.inspect_view("ui")["status"], "stale")
        self.assertEqual(self.engine.inspect_view("integration")["status"], "stale")
        self.assertEqual(self.engine.inspect_view("bff")["status"], "fresh")
        ui_v2 = self.engine.build_view("ui")
        self.assertNotEqual(ui_v1.id, ui_v2.id)
        self.assertEqual(self.engine.inspect_view("integration")["status"], "stale")
        integration_v2 = self.engine.build_view("integration")
        self.assertEqual(integration_v2.upstream, {"ui": ui_v2.id, "bff": bff_v1.id})
        self.assertEqual(self.engine.inspect_view("integration")["status"], "fresh")
        self.assertEqual(len(self.engine.view_history("bff")), 1)
        old_version, new_version = self.engine.view_history("integration")
        self.assertEqual(old_version.upstream, integration_v1.upstream)
        self.assertEqual(new_version.upstream, integration_v2.upstream)

    def test_historical_evidence_is_pinned_after_restart_and_source_loss(self):
        source = self.directory / "chat-export.txt"
        excerpt = "Форма открывается с опубликованной версией."
        source.write_text(excerpt, encoding="utf-8")
        self.store.append_assertion(
            assertion_id="clone-ui-version",
            text="Опубликованная версия выбрана по умолчанию.",
            actor="Участник",
            source=source.as_uri(),
            episode="chat-export",
            valid_at="2026-09-10T10:00:00Z",
            recorded_at="2026-09-10T10:01:00Z",
            scope="clone/ui",
            metadata={"excerpt": excerpt},
        )
        self.load("clone-collection")
        self.define_ui()
        original = self.engine.build_view("ui")
        source.unlink()
        self.load("clone-ui-version-revised")

        with MemoryStore(self.path) as reopened:
            next_engine = MemoryEngine(reopened)
            revised = next_engine.build_view("ui")
            saved = next_engine.view_history("ui")[0]

        self.assertEqual(saved.id, original.id)
        self.assertEqual(saved.evidence, original.evidence)
        self.assertEqual(saved.edges, original.edges)
        self.assertEqual(saved.source_version, original.source_version)
        self.assertEqual(evidence(saved)["clone-ui-version"]["metadata"]["excerpt"], excerpt)
        self.assertEqual(evidence(saved)["clone-ui-version"]["source"], source.as_uri())
        self.assertEqual(evidence(saved)["clone-ui-version"]["disposition"], "assertion")
        self.assertEqual(evidence(revised)["clone-ui-version"]["disposition"], "superseded")
        self.assertEqual(evidence(revised)["clone-ui-version-revised"]["disposition"], "decision")

    def test_missing_explicit_root_is_reported_and_later_discovered(self):
        self.engine.define_view(
            ViewSpec(
                "handoff", "Передача контракта", "Клонирование сценария",
                scopes=("clone/ui",), root_ids=("clone-bff-source",),
            )
        )
        missing = self.engine.build_view("handoff")
        self.assertEqual(missing.missing_roots, ["clone-bff-source"])
        self.assertEqual(missing.evidence, [])
        self.engine.define_view(
            ViewSpec(
                "review", "Проверка передачи", "Клонирование сценария",
                scopes=("clone/ui",), depends_on=("handoff",),
            )
        )
        original_review = self.engine.build_view("review")

        self.load("unrelated-receipt")
        self.assertEqual(self.engine.inspect_view("handoff")["status"], "fresh")
        self.assertEqual(self.engine.inspect_view("review")["status"], "fresh")
        self.load("clone-bff-source")
        self.assertEqual(self.engine.inspect_view("handoff")["status"], "stale")
        self.assertEqual(self.engine.inspect_view("review")["status"], "stale")

        refreshed = {version.view_id: version for version in self.engine.refresh_from("clone-bff-source")}
        available = refreshed["handoff"]
        self.assertEqual(available.missing_roots, [])
        self.assertIn("clone-bff-source", evidence(available))
        self.assertEqual(self.engine.get_version(missing.id).missing_roots, ["clone-bff-source"])
        self.assertEqual(original_review.upstream, {"handoff": missing.id})
        self.assertEqual(refreshed["review"].upstream, {"handoff": available.id})

    def test_failed_rebuild_keeps_published_version_and_can_be_retried(self):
        self.load(*BASE)
        model = FixtureModel()
        engine = MemoryEngine(self.store, model=model)
        self.define_ui(engine)
        original = engine.build_view("ui")
        previous_check=engine.inspect_view("ui")["current_check_id"]
        self.load("clone-ui-version-revised")
        model.fail = True

        with self.assertRaises(ModelError) as caught:
            engine.build_view("ui")
        self.assertEqual(engine.inspect_view("ui")["status"], "stale")
        history=engine.view_history("ui")
        self.assertEqual(len(history),2)
        self.assertEqual(history[0],original)
        failure=history[1]
        self.assertEqual(failure,caught.exception.failure_version)
        self.assertEqual(failure.summary,'')
        self.assertEqual(failure.overview_review['status'],'unresolved')
        self.assertIs(failure.overview_review['failure_proof'],True)
        self.assertEqual(failure.overview_review['generation_error'],'fixture model unavailable')
        self.assertTrue(failure.overview_review['failed_phase'])
        self.assertEqual(set(evidence(failure)),set(evidence(original))|{'clone-ui-version-revised'})
        for row in failure.evidence:
            self.assertEqual(row['text'],self.store.get_assertion(row['id']).text)
        self.assertEqual(engine.inspect_view("ui")["current_id"],original.id)
        self.assertEqual(engine.inspect_view("ui")["current_check_id"],previous_check)
        self.assertEqual(engine.view_history("ui")[0].evidence, original.evidence)

        model.fail = False
        revised = engine.build_view("ui")
        self.assertEqual(engine.inspect_view("ui")["status"], "fresh")
        self.assertEqual(
            [version.id for version in engine.view_history("ui")], [original.id, failure.id, revised.id]
        )
        self.assertEqual(engine.get_version(failure.id),failure)
        self.assertEqual(engine.get_version(original.id),original)
        self.assertEqual(engine.inspect_view("ui")["current_id"],revised.id)
        self.assertNotEqual(engine.inspect_view("ui")["current_check_id"],previous_check)
        self.assertEqual(evidence(revised)["clone-ui-version-revised"]["disposition"], "decision")
        self.assertEqual(len(model.requests), 3)

    def test_write_during_generation_publishes_frozen_snapshot_then_refreshes(self):
        self.load(*BASE)
        requests = []

        def model(request):
            requests.append({record["id"] for record in request.records})
            if len(requests) == 1:
                with MemoryStore(self.path) as other:
                    self.load("clone-ui-version-revised", store=other)
            return f"Сводка {len(requests)}."

        engine = MemoryEngine(self.store, model=model)
        self.define_ui(engine)
        published = engine.build_view("ui")

        self.assertEqual(len(requests), 1)
        self.assertNotIn("clone-ui-version-revised", requests[0])
        self.assertEqual(published.summary, "Сводка 1.")
        self.assertLess(published.source_version, self.store.version)
        self.assertTrue(engine.latest_check(published)['freshness']['refresh_required'])
        self.assertEqual(engine.inspect_view('ui')['status'],'stale')
        refreshed = engine.build_view('ui')
        self.assertEqual(len(requests), 2)
        self.assertIn("clone-ui-version-revised", requests[1])
        self.assertEqual(refreshed.source_version,self.store.version)
        self.assertEqual(engine.inspect_view('ui')['status'],'fresh')
        self.assertEqual([version.id for version in engine.view_history("ui")], [published.id,refreshed.id])


class CliScenarioTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)

    def cli(self, *args, db="memory.sqlite3"):
        result = subprocess.run(
            [sys.executable, "-m", "prototypes.assertion_memory", "--db",
             str(self.directory / db), *args],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.rstrip("\n")

    def test_export_import_keeps_branch_dependencies(self):
        self.cli("import", str(Path(__file__).parent / "fixtures" / "clone_scenarios.json"))
        export = self.directory / "export.json"
        export.write_text(self.cli("export"), encoding="utf-8")

        self.cli("import", str(export), db="imported.sqlite3")
        view = json.loads(self.cli("ask", "--view", "integration", db="imported.sqlite3"))
        records = {record["id"]: record for record in view["evidence"]}
        self.assertEqual(set(view["upstream"]), {"bff", "ui"})
        self.assertEqual(records["clone-ui-version-revised"]["disposition"], "decision")
        self.assertEqual(records["clone-bff-source"]["disposition"], "conflict")
        self.assertEqual(records["widget-collection-exception"]["disposition"], "exception")

    def test_manual_prompt_matches_automatic_input_without_calling_the_model(self):
        self.cli(
            "add", "ui-default", "Выбирается черновик.", "--actor", "Участник",
            "--source", "chat://ui", "--episode", "ui",
        )
        self.cli("view", "ui", "Предвыбор версии", "--root", "ui-default")
        prompt_file = self.directory / "prompt.txt"
        calls_file = self.directory / "calls.jsonl"
        capture = [
            sys.executable, "-c",
            "from pathlib import Path\nimport json, sys\n"
            "prompt = sys.stdin.read()\n"
            "request = json.loads(prompt.split('\\n\\n', 1)[1])\n"
            "with Path(sys.argv[2]).open('a') as stream:\n"
            "    stream.write(json.dumps(request['task']) + '\\n')\n"
            "if request['task'] == 'Review grounded memory overview':\n"
            "    print(json.dumps({'status': 'accepted', 'coverage': 'all_claims', "
            "'reason': 'The fixture claim follows its source.', 'issues': [], 'context_requests': [], 'unknown_needs': []}))\n"
            "else:\n"
            "    Path(sys.argv[1]).write_text(prompt, encoding='utf-8')\n"
            "    print('draft')\n",
            str(prompt_file), str(calls_file),
        ]
        generated = self.cli(
            "--generator", "command", "--model-command", json.dumps(capture), "ask", "--view", "ui",
        )
        self.assertEqual(json.loads(generated)["summary"], "draft")
        self.assertEqual(json.loads(generated)["overview_review"]["status"], "accepted")
        calls_before_export = calls_file.read_text(encoding="utf-8")
        self.assertIn("Review grounded memory overview", calls_before_export)

        failure = [sys.executable, "-c", "raise SystemExit(17)"]
        manual = self.cli(
            "--generator", "command", "--model-command", json.dumps(failure),
            "ask", "--view", "ui", "--prompt",
        )
        self.assertEqual(manual, prompt_file.read_text(encoding="utf-8").rstrip("\n"))
        self.assertEqual(calls_file.read_text(encoding="utf-8"), calls_before_export)

        # Export builds only an extractive snapshot. It must not become the next
        # configured model's unchecked cache, even when its writer policy matches.
        generated_after_export = json.loads(self.cli(
            "--generator", "command", "--model-command", json.dumps(capture), "ask", "--view", "ui",
        ))
        self.assertEqual(generated_after_export["overview_review"]["status"], "accepted")
        self.assertEqual(generated_after_export["id"], json.loads(generated)["id"])


if __name__ == "__main__":
    unittest.main()
