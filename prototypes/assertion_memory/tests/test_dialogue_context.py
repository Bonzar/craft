"""Dialogue contracts at the provider boundary; never hand-create evidence links.

These tests distinguish transport/persistence guarantees from model intelligence.
The optional live runner exercises the same dialogue through the real provider.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from prototypes.assertion_memory import desktop
from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.worker import LinkWorker


ROOMS = "Помещения 214 и 216 — серверные с непрерывным питанием. Там нельзя отключать электричество."
PROPOSAL = "Предлагаю отключать питание северного корпуса ночью, чтобы снизить расход энергии. Подтверждаешь?"
ACCEPTANCE = "Да, кроме помещений 214 и 216."


class DialogueContextTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.db = self.root / "dialogue.sqlite3"
        self.store = MemoryStore(self.db)
        self.addCleanup(self.store.close)
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.queue = CaptureQueue(self.store, clock=lambda: self.now)

    def capture(self, text, *, actor="user", session="facilities", scope="campus"):
        self.now += timedelta(seconds=1)
        return self.queue.capture_event(
            session, f"turn-{self.now.second}", text,
            source=f"dialogue://{session}/{self.now.second}/{actor}",
            actor=actor, scope=scope, metadata={"role": "assistant" if actor == "assistant" else "user"},
        )

    def extraction_request(self, target):
        requests = []

        def provider(request):
            if request.task.startswith("Extract"):
                requests.append(request)
            return json.dumps({"assertions": [], "links": [], "branches": []})

        LinkWorker(self.queue, MemoryEngine(self.store), provider).drain()
        matches = [request for request in requests
                   if any(record.get("id") == target.id for record in request.records)]
        self.assertTrue(matches, "worker never asked provider to extract the target event")
        # The target itself is the first record for backward-compatible providers.
        exact = [request for request in matches if request.records[0].get("id") == target.id]
        self.assertEqual(len(exact), 1)
        return exact[0]

    def test_short_exception_receives_prior_room_definitions_and_assistant_question(self):
        rooms = self.capture(ROOMS)
        proposal = self.capture(PROPOSAL, actor="assistant")
        accepted = self.capture(ACCEPTANCE)
        request = self.extraction_request(accepted)
        records = {record.get("id"): record for record in request.records}
        self.assertIn(rooms.id, records, "room definitions must survive into short-reply extraction")
        self.assertIn(proposal.id, records, "the accepted assistant question is required evidence")
        self.assertEqual(records[proposal.id].get("actor"), "assistant")
        self.assertEqual(records[rooms.id].get("actor"), "user")
        self.assertIn("непрерывным питанием", records[rooms.id].get("text", ""))
        self.assertIn("снизить расход энергии", records[proposal.id].get("text", ""))

    def test_extraction_cannot_use_future_or_unrelated_session_context(self):
        rooms = self.capture(ROOMS)
        accepted = self.capture(ACCEPTANCE)
        future = self.capture("Это согласие означало полное отключение всех помещений.", actor="assistant")
        other = self.capture("Все помещения отключаем без исключения.", session="unrelated-project")
        request = self.extraction_request(accepted)
        serialized = json.dumps(list(request.records), ensure_ascii=False)
        self.assertIn(rooms.id, serialized)
        self.assertNotIn(future.id, serialized, "future assistant text cannot explain earlier consent")
        self.assertNotIn(other.id, serialized, "another conversation cannot supply the missing referent")

    def test_raw_context_and_actual_actor_labels_survive_reopen_without_promotion(self):
        self.capture(ROOMS, actor="simulated-facilities-manager")
        self.capture(PROPOSAL, actor="assistant")
        self.capture(ACCEPTANCE, actor="simulated-facilities-manager")
        with MemoryStore(self.db) as reopened:
            rows = CaptureQueue(reopened).list_events("facilities")
            self.assertEqual([row.prompt for row in rows], [ROOMS, PROPOSAL, ACCEPTANCE])
            self.assertEqual([row.actor for row in rows],
                             ["simulated-facilities-manager", "assistant", "simulated-facilities-manager"])
            self.assertEqual(reopened.list_assertions(), [], "raw intake is not an adopted assertion")

    def interpreted(self, target, text, evidence, *, status="accepted", reason="The exact reply adopts the qualified proposal.", kind="decision", propose_links=None):
        calls = []

        def provider(request):
            calls.append(request)
            if request.task == "Review source material coverage":
                return json.dumps({"source_coverage":{"status":"unresolved",
                    "reason":"This attribution fixture leaves aggregate source meaning unresolved.","omissions":[]}})
            if request.task.startswith("Repair rejected"):
                return json.dumps({"assertions":[]})
            if request.task.startswith("Extract"):
                objects = [{"text": text, "quote": target.prompt, "kind": kind, "evidence": evidence}] \
                    if request.records[0]["id"] == target.id else []
                return json.dumps({"assertions": objects}, ensure_ascii=False)
            if request.task == "Verify contextual assertion interpretations":
                return json.dumps({"verdicts": [{"index": 0, "status": status, "reason": reason}]})
            if request.task == "Verify semantic evidence links":
                return json.dumps({"verdicts": [{"index": row["index"], "status": "accepted",
                    "reason": "Independent relation fixture."} for row in request.records
                    if row.get("role") == "proposed_link"]})
            if request.task.startswith("Propose"):
                return json.dumps({"links": propose_links(request) if propose_links else [], "branches": []})
            return "A review from the supplied assertions."

        LinkWorker(self.queue, MemoryEngine(self.store), provider).drain()
        return self.queue.get(target.id), calls

    def test_contextual_decision_keeps_definition_reason_and_separate_actor_provenance(self):
        rooms = self.capture(ROOMS)
        proposal = self.capture(PROPOSAL, actor="assistant")
        target = self.capture(ACCEPTANCE, actor="simulated-facilities-manager")
        normalized = "Согласовали ночное отключение питания северного корпуса, кроме серверных 214 и 216."
        evidence = [
            {"event_id": rooms.id, "quote": ROOMS, "relation": "definition"},
            {"event_id": proposal.id, "quote": PROPOSAL, "relation": "antecedent"},
            {"event_id": proposal.id, "quote": "чтобы снизить расход энергии", "relation": "reason"},
        ]
        job, calls = self.interpreted(target, normalized, evidence)
        self.assertEqual(job.status, "completed", job.error)
        self.assertEqual(len(job.extracted_ids), 1)
        assertion = self.store.get_assertion(job.extracted_ids[0])
        self.assertEqual(assertion.text, normalized)
        self.assertEqual(assertion.actor, "simulated-facilities-manager")
        self.assertEqual(assertion.metadata["excerpt"], ACCEPTANCE)
        self.assertEqual(assertion.metadata["kind"], "decision")
        refs = assertion.metadata["evidence_refs"]
        self.assertTrue(any(ref["event_id"] == rooms.id and ref["relation"] == "definition" for ref in refs))
        reason_ref = next(ref for ref in refs if ref["relation"] == "reason")
        self.assertEqual(reason_ref["role"], "assistant")
        self.assertEqual(self.queue.resolve_reference(reason_ref)["text"], "чтобы снизить расход энергии")
        self.assertEqual(assertion.metadata["interpretation"]["status"], "model_verified")
        for ref in refs:
            event = self.queue.get_event(ref["event_id"])
            self.assertEqual(event.prompt[ref["start"]:ref["end"]], self.queue.resolve_reference(ref)["text"])
            self.assertNotIn("quote", ref, "shared source bodies are resolved, not copied into each assertion")
        self.assertEqual(sum(call.task == "Verify contextual assertion interpretations" for call in calls), 1)
        self.assertNotIn("assistant", [row.actor for row in self.store.list_assertions()])

    def test_ambiguous_yes_does_not_force_an_alternative_into_a_decision(self):
        alternatives = self.capture("Можно отключать северный корпус ночью или снизить яркость освещения. Что выбираем?", actor="assistant")
        target = self.capture("Да.")
        job, calls = self.interpreted(
            target, "Выбрали ночное отключение северного корпуса.",
            [{"event_id": alternatives.id, "quote": alternatives.prompt, "relation": "antecedent"}],
            status="unresolved", reason="The yes reply does not distinguish the two alternatives.",
        )
        self.assertEqual(job.status, "completed", job.error)
        self.assertEqual(job.extracted_ids, [])
        self.assertEqual(self.store.list_assertions(), [])
        self.assertTrue(job.proposals, "unresolved interpretation must remain a reviewable durable record")
        self.assertIn("two alternatives", json.dumps(job.proposals))

    def test_exact_quotes_do_not_bypass_independent_semantic_verification(self):
        proposal = self.capture(PROPOSAL, actor="assistant")
        target = self.capture(ACCEPTANCE)
        job, calls = self.interpreted(
            target, "Согласовали отключение абсолютно всех помещений без исключения.",
            [{"event_id": proposal.id, "quote": PROPOSAL, "relation": "antecedent"}],
            status="unsupported", reason="The interpretation erased the explicit room exceptions.",
        )
        self.assertEqual(job.status, "completed", job.error)
        self.assertEqual(job.extracted_ids, [])
        self.assertTrue(job.proposals)
        self.assertIn("room exceptions", json.dumps(job.proposals))

    def test_invented_definition_is_rejected_before_publication(self):
        rooms = self.capture(ROOMS)
        target = self.capture(ACCEPTANCE)
        job, _ = self.interpreted(
            target, "Серверные помещения 214 и 216 остаются с питанием.",
            [{"event_id": rooms.id, "quote": "Помещение 218 — склад.", "relation": "definition"}],
        )
        self.assertEqual(job.extracted_ids, [])
        self.assertEqual(self.store.list_assertions(), [])
        self.assertIsNotNone(job.error, "fabricated context span must fail closed")
        self.assertRegex(job.error, r"(?i)quote|evidence|context", "failure must identify invalid evidence, not unsupported schema")

    def test_explicit_uncertainty_survives_as_uncertainty(self):
        target = self.capture("Пока не знаю, нужно ли оставлять питание в помещении 218.")
        job, _ = self.interpreted(
            target, "Не определено, нужно ли оставлять питание в помещении 218.", [], kind="assertion",
        )
        self.assertEqual(job.status, "completed", job.error)
        assertion, = self.store.list_assertions()
        self.assertIn("Не определено", assertion.text)
        self.assertNotEqual(assertion.metadata["kind"], "decision")
        self.assertEqual(self.store.list_edges(), [])

    def test_natural_late_change_automatically_supersedes_only_the_changed_room(self):
        old_event = self.capture("Ночью серверные 214 и 216 оставляем с питанием.")
        old_text = "Согласовали сохранять питание серверных 214 и 216 ночью."
        old_job, _ = self.interpreted(old_event, old_text, [])
        self.assertEqual(old_job.status, "completed", old_job.error)
        update = self.capture("Решили заменить ночное исключение для помещения 216: его теперь отключаем, потому что серверы перенесли. Помещение 214 по-прежнему остаётся с питанием.")
        normalized = "Пользователь сообщает о решении отменить исключение из ночного отключения электропитания северного корпуса для помещения 216 и теперь отключать его ночью, поскольку серверы перенесли; помещение 214 по-прежнему сохраняет питание."

        def model_links(request):
            source = next(record for record in request.records if record.get("role") == "source")
            self.assertEqual(source["metadata"]["interpretation"]["status"], "model_verified")
            self.assertTrue(source["metadata"]["evidence_refs"], "linker must see anchored interpretation provenance")
            prior = next(record for record in request.records if record.get("text") == old_text)
            return [{"target_id": prior["id"], "kind": "supersedes", "source_quote": normalized,
                     "target_quote": old_text, "condition": "для помещения 216"}]

        new_job, _ = self.interpreted(update, normalized, [], propose_links=model_links)
        self.assertEqual(new_job.status, "completed", new_job.error)
        edges = self.store.list_edges()
        self.assertEqual(len(edges), 1, "an unambiguous natural revision must not stay pending because of verb inflection")
        self.assertEqual(edges[0].kind, "supersedes")
        self.assertEqual(edges[0].metadata["condition"], "для помещения 216")
        self.assertEqual(edges[0].to_id, old_job.extracted_ids[0])
        self.assertEqual(edges[0].from_id, new_job.extracted_ids[0])

    def test_unverified_staged_antecedent_cannot_supply_agreement_context(self):
        staged = self.queue.capture_event("facilities", "staged", PROPOSAL,
            actor="assistant", source="dialogue://staged/assistant",
            metadata={"role": "assistant"}, await_origin=True)
        target = self.capture("Да.")
        request = self.extraction_request(target)
        self.assertNotIn(staged.id, {record.get("id") for record in request.records})
        self.assertEqual(self.queue.get(staged.id).status, "waiting_origin")

    def test_future_context_reference_is_rejected_even_when_quote_is_exact(self):
        target = self.capture("Да.")
        future = self.capture("Отключаем все комнаты без исключения.", actor="assistant")
        job, _ = self.interpreted(target, "Согласовали отключение всех комнат.",
            [{"event_id": future.id, "quote": future.prompt, "relation": "antecedent"}])
        self.assertEqual(job.extracted_ids, [])
        self.assertEqual(self.store.list_assertions(), [])
        self.assertIsNotNone(job.error)
        self.assertIn("context", job.error.lower())

    def test_equivalent_consent_phrasings_preserve_provider_meaning_and_distinct_sources(self):
        rooms = self.capture(ROOMS)
        proposal = self.capture(PROPOSAL, actor="assistant")
        evidence = [{"event_id": rooms.id, "quote": ROOMS, "relation": "definition"},
                    {"event_id": proposal.id, "quote": PROPOSAL, "relation": "antecedent"}]
        normalized = "Ночью отключаем северный корпус, кроме серверных 214 и 216."
        ids = []
        for phrase in ("Да, кроме помещений 214 и 216.", "Согласен, но 214 и 216 оставляем с питанием.",
                       "Подтверждаю с исключением для комнат 214 и 216."):
            target = self.capture(phrase)
            job, _ = self.interpreted(target, normalized, evidence)
            self.assertEqual(job.status, "completed", job.error)
            self.assertEqual(len(job.extracted_ids), 1)
            row = self.store.get_assertion(job.extracted_ids[0])
            self.assertEqual(row.text, normalized)
            self.assertEqual(row.metadata["excerpt"], phrase)
            self.assertEqual(row.metadata["event_id"], target.id)
            ids.append(row.assertion_id)
        self.assertEqual(len(set(ids)), 3, "equivalent meanings still retain independent source occurrences")

    def test_assistant_role_with_actual_model_actor_is_attributed_source(self):
        event = self.queue.capture_event("model-dialogue", "one", PROPOSAL,
            actor="actual-model-assistant", source="simulation://assistant/one",
            metadata={"role": "assistant", "actual_actor": "actual-model-assistant"})
        job, calls = self.interpreted(event,
            "Агент предложил отключать питание северного корпуса ночью для снижения расхода энергии.",
            [], kind="proposal")
        self.assertTrue(any(call.task.startswith("Extract") for call in calls))
        self.assertEqual(job.status, "completed", job.error)
        self.assertEqual(len(job.extracted_ids), 1)
        assertion = self.store.get_assertion(job.extracted_ids[0])
        self.assertEqual(assertion.actor, "actual-model-assistant")
        self.assertEqual(assertion.metadata["role"], "assistant")
        self.assertEqual(assertion.metadata["kind"], "proposal")
        self.assertEqual(self.queue.get_event(event.id).actor, "actual-model-assistant")

    def test_stop_preserves_assistant_dialogue_as_attributed_raw_context(self):
        transcript = self.root / "rollout.jsonl"
        rows = [
            {"type": "session_meta", "payload": {"id": "native", "cwd": str(self.root)}},
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-1",
                "turn_attribution": {"turn_trigger": "user"}}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": ROOMS}],
                "internal_chat_message_metadata_passthrough": {"turn_id": "turn-1",
                    "content_item_kinds": ["user.text"],
                    "content_item_metadata": [{"provenance": {"type": "user"}}]}}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "id": "assistant-1",
                "content": [{"type": "output_text", "text": PROPOSAL}]}},
            {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "turn-1",
                "last_agent_message": PROPOSAL}},
        ]
        transcript.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
        config = {"db": str(self.db), "scope": "campus", "auto_process": False,
                  "cwd_roots": [str(self.root)]}
        raw = {"hook_event_name": "Stop", "session_id": "native", "turn_id": "turn-1",
               "cwd": str(self.root), "transcript_path": str(transcript)}
        desktop.handle_event(raw, config)
        desktop.handle_event(raw, config)  # transcript replay must be idempotent
        events = self.queue.list_events("native")
        self.assertEqual([(event.actor, event.prompt) for event in events],
                         [("user", ROOMS), ("assistant", PROPOSAL)])
        self.assertEqual(self.store.list_assertions(), [])


if __name__ == "__main__":
    unittest.main()
