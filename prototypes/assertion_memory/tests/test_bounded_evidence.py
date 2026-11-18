"""Regressions from the campus load and wrong-object freshness audit."""
import json
import unittest

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import Candidate, ModelError, format_prompt
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.queue import CaptureInput, CaptureQueue
from prototypes.assertion_memory.worker import LinkWorker


class OnlyRoots:
    def __init__(self, roots):
        self.roots = set(roots)

    def rank(self, query, candidates, *, limit):
        # Deterministic stand-in for semantic ranking. Anchors win the corpus
        # search; a separate neighbour search can rank the available context.
        selected = sorted(self.roots.intersection(candidates)) or sorted(candidates)
        return [Candidate(key, 1.0, "fixture") for key in selected[:limit]]


class BoundedEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(":memory:")
        self.addCleanup(self.store.close)
        self.requests = []

    def add(self, key, text, *, scope="procurement", kind="decision", valid_at=None):
        self.store.append_assertion(
            assertion_id=key, text=text, actor="audit", source=f"test://audit/{key}",
            episode=key, scope=scope, recorded_at="2026-01-10", valid_at=valid_at,
            metadata={"kind": kind},
        )

    def edge(self, key, source, kind, target, **metadata):
        self.store.append_edge(edge_id=key, from_id=source, to_id=target, kind=kind,
                               recorded_at="2026-01-11", metadata=metadata)

    def model(self, request):
        self.requests.append(request)
        self.assertLessEqual(len(format_prompt(request)), 60_000)
        return "Ответ по записям " + ", ".join(f"[{row['id']}]" for row in request.records)

    def engine(self, roots, query="Какие условия заказа?", max_seeds=1):
        engine = MemoryEngine(self.store, model=self.model, candidate_search=OnlyRoots(roots))
        engine.define_view(ViewSpec("audit", "Audit", query, root_ids=tuple(roots), max_seeds=max_seeds))
        return engine

    def test_procurement_star_and_chain_are_bounded_but_fire_exception_survives(self):
        # The audit's one-seed query traversed 132 records/131 links and failed
        # locally with 143561 characters before the generator was called.
        self.add("order", "Заказ панели согласован.")
        self.add("fire", "Панель нельзя монтировать до независимой пожарной проверки.", scope="authority")
        self.edge("fire/order", "fire", "exception_to", "order", condition="до независимой пожарной проверки")
        for index in range(130):
            key = f"approval-{index:03}"
            self.add(key, "Справочное согласование поставки. " + "Пояснение комплекта. " * 40)
            self.edge(f"support/{index}", key, "supports", "order" if index < 65 else f"approval-{index-1:03}")
        engine = self.engine(["order"])
        version = engine.build_view("audit")
        ids = {row["id"] for row in version.evidence}
        self.assertIn("fire", ids)
        self.assertLess(len(ids), 15)
        self.assertIn("нельзя монтировать", version.summary)
        self.assertEqual(next(row for row in version.evidence if row["id"] == "fire")["source"], "test://audit/fire")
        self.assertEqual(engine.build_view("audit").id, version.id)
        self.assertEqual(len(self.requests), 1)

    def test_optional_neighbor_keeps_its_own_exception_but_does_not_expand_navigation(self):
        for key in ("rule", "observation", "restriction", "remote"):
            self.add(key, "Панель заказа " + key)
        self.edge("observed/rule", "observation", "supports", "rule")
        self.edge("restricted/observed", "restriction", "exception_to", "observation")
        self.edge("remote/restricted", "remote", "realizes", "restriction")
        version = self.engine(["rule"]).build_view("audit")
        self.assertEqual({row["id"] for row in version.evidence}, {"rule", "observation", "restriction"})

    def test_large_independent_required_roots_use_complete_bounded_requests(self):
        roots = []
        for index in range(90):
            key = f"rule-{index}"
            roots.append(key)
            self.add(key, "Требование " + "сохранить первоначальное основание " * 25)
        engine = self.engine(roots)
        version = engine.build_view("audit")
        seen = [row["id"] for request in self.requests for row in request.records]
        self.assertEqual(set(seen), set(roots))
        self.assertEqual(len(seen), len(roots))
        self.assertGreater(len(self.requests), 1)
        self.assertTrue(all("остальных пакетах" in request.instructions for request in self.requests))
        self.assertEqual(engine.get_version(version.id), version)
        self.assertEqual(engine.model_requests(version), self.requests)
        for key in roots:
            self.assertIn(f"[{key}]", version.summary)

    def test_batched_rules_keep_their_cross_scope_qualifiers_and_all_known_restrictions(self):
        roots = []
        for index in range(60):
            rule, restriction = f"rule-{index:02}", f"restriction-{index:02}"
            roots.append(rule)
            self.add(rule, "Заказ согласован. " + "Комплектация оборудования. " * 20)
            self.add(restriction, f"Монтаж {index} запрещён до проверки. " + "Обязательная проверка. " * 20,
                     scope="authority")
            self.edge(f"limit/{index}", restriction, "exception_to", rule, condition="до проверки")
        engine = self.engine(roots)
        version = engine.build_view("audit")
        self.assertGreater(len(self.requests), 1)
        for request in self.requests:
            ids = {row["id"] for row in request.records}
            for index in range(60):
                self.assertEqual(f"rule-{index:02}" in ids, f"restriction-{index:02}" in ids)
        for index in range(60):
            self.assertIn(f"Монтаж {index} запрещён до проверки", version.summary)
        self.assertEqual(engine.model_requests(version), self.requests)

    def test_single_oversize_mandatory_component_is_explicit_and_not_sent_partially(self):
        self.add("rule", "Поставка согласована.")
        engine = self.engine(["rule"])
        previous = engine.build_view("audit")
        self.requests.clear()
        self.add("restriction", "Обязательная оговорка " * 4000, scope="authority")
        self.edge("restriction/rule", "restriction", "exception_to", "rule")
        with self.assertRaisesRegex(ModelError, "mandatory.*restriction") as failed:
            engine.build_view("audit")
        self.assertEqual(self.requests, [])
        self.assertEqual({row["id"] for row in failed.exception.failure_version.evidence}, {"rule", "restriction"})
        self.assertEqual([row["id"] for row in failed.exception.failure_version.edges], ["restriction/rule"])
        self.assertEqual(engine.inspect_view("audit")["current_id"], previous.id)
        self.assertEqual(engine.get_version(previous.id), previous)

    def test_reviewed_branch_record_retains_new_qualifier_and_invalidates_cached_view(self):
        text = "Согласовали разгрузку у восточных ворот."
        question = "Где согласована разгрузка?"
        queue = CaptureQueue(self.store)
        source = queue.capture(CaptureInput(
            text, "audit", "test://other", "other", "reviewed", "outside",
            recorded_at="2026-01-01", metadata={"kind": "decision"},
        ))
        engine = MemoryEngine(self.store, model=self.model)
        engine.define_view(ViewSpec("audit", "Audit", question, scopes=("procurement",)))

        def propose(_):
            return json.dumps({"links": [], "branches": [{
                "view_id": "audit", "source_quote": text, "question_quote": question,
                "reason": "Реплика определяет место разгрузки.",
            }]})

        LinkWorker(queue, engine, propose).run_once()
        engine.review_branch(queue, "audit", source.assertion_id,
                             decision="accepted", reviewer="audit", reason="Относится к этой ветке.")
        previous = engine.build_view("audit")
        self.add("reviewed-limit", "Восточные ворота закрыты до проверки.", scope="authority")
        self.edge("limit/reviewed", "reviewed-limit", "exception_to", source.assertion_id)
        self.assertEqual(engine.inspect_view("audit")["status"], "stale")
        current = engine.build_view("audit")
        self.assertNotEqual(current.id, previous.id)
        self.assertEqual({row["id"] for row in current.evidence}, {"reviewed", "reviewed-limit"})
        self.assertIn("Восточные ворота закрыты до проверки", current.summary)
        self.assertIn("branch_review", next(row for row in current.evidence if row["id"] == "reviewed"))
        self.assertEqual(engine.get_version(previous.id), previous)

    def test_lamp_observation_is_not_presented_as_latest_lift_observation(self):
        self.add("lift", "Подъёмник проверили в мае.", kind="observation", valid_at="2026-05-02")
        self.add("lamp", "Лампа заменена в августе.", kind="observation", valid_at="2026-08-04")
        version = self.engine(["lift", "lamp"], query="Можно направлять людей к подъёмнику сейчас?").build_view("audit")
        self.assertNotIn("Последнее датированное наблюдение [lamp]", version.summary)
        self.assertIn("Состояние прямо сейчас по этим данным не подтверждено", version.summary)
        self.assertIn("объект", self.requests[0].instructions)
        self.assertIn("дату", self.requests[0].instructions)
        self.assertIn("[lift]", version.summary)
        self.assertIn("[lamp]", version.summary)


if __name__ == "__main__":
    unittest.main()
