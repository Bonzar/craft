"""Known prerequisites are required evidence, not optional search neighbours."""
from dataclasses import asdict
import unittest

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import Candidate, format_prompt
from prototypes.assertion_memory.storage import MemoryStore


class SelectedNeighbours:
    def __init__(self, identifiers=()):
        self.identifiers = identifiers

    def rank(self, query, candidates, *, limit):
        return [Candidate(key, 1.0, "fixture") for key in self.identifiers if key in candidates][:limit]


class PrerequisiteEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(":memory:")
        self.addCleanup(self.store.close)

    def add(self, key, text=None, scope="campus"):
        self.store.append_assertion(assertion_id=key, text=text or key, actor="fixture",
            source=f"fixture://{key}", episode="fixture", scope=scope,
            recorded_at="2026-01-01", metadata={"kind": "decision"})

    def edge(self, source, target, kind="depends_on", **metadata):
        self.store.append_edge(edge_id=f"{source}/{kind}/{target}", from_id=source,
            to_id=target, kind=kind, recorded_at="2026-01-02", metadata=metadata)

    def select(self, anchor, neighbours=()):
        engine = MemoryEngine(self.store, candidate_search=SelectedNeighbours(neighbours))
        records = {a.assertion_id: {**asdict(a), "id": a.assertion_id}
                   for a in self.store.list_assertions()}
        edges = [{**asdict(edge), "id": edge.edge_id} for edge in self.store.list_edges()]
        return engine.select_evidence(ViewSpec("opening", "Opening", "Open east building?", max_seeds=1),
                                      {anchor}, records, edges)

    def test_known_prerequisite_survives_ranker_omission(self):
        self.add("launch", "Commission the east building.")
        self.add("permit", "Commissioning requires a signed fire permit.", scope="authority")
        self.edge("launch", "permit", condition="only with signed fire permit")
        evidence, edges = self.select("launch")
        self.assertEqual({row["id"] for row in evidence}, {"launch", "permit"})
        self.assertEqual(edges[0]["metadata"]["condition"], "only with signed fire permit")

    def test_transitive_prerequisite_keeps_its_own_exception(self):
        for key in ("launch", "permit", "inspection", "restriction"):
            self.add(key)
        self.edge("launch", "permit")
        self.edge("permit", "inspection")
        self.edge("restriction", "inspection", "exception_to", condition="after an independent repeat")
        evidence, edges = self.select("launch")
        self.assertEqual({row["id"] for row in evidence}, {"launch", "permit", "inspection", "restriction"})
        self.assertEqual(len(edges), 3)

    def test_reading_shared_permit_does_not_pull_all_dependents(self):
        self.add("permit")
        for index in range(1000):
            key = f"building-{index}"
            self.add(key)
            self.edge(key, "permit")
        evidence, edges = self.select("permit")
        self.assertEqual([row["id"] for row in evidence], ["permit"])
        self.assertEqual(edges, [])

    def test_selected_reverse_neighbour_brings_all_of_its_prerequisites(self):
        for key in ("permit", "launch", "structural-check"):
            self.add(key)
        self.edge("launch", "permit")
        self.edge("launch", "structural-check")
        evidence, edges = self.select("permit", neighbours=("launch",))
        self.assertEqual({row["id"] for row in evidence}, {"permit", "launch", "structural-check"})
        self.assertEqual(len(edges), 2)

    def test_dependency_cycle_terminates_and_keeps_both_records(self):
        self.add("launch")
        self.add("permit")
        self.edge("launch", "permit")
        self.edge("permit", "launch")
        evidence, edges = self.select("launch")
        self.assertEqual({row["id"] for row in evidence}, {"launch", "permit"})
        self.assertEqual(len(edges), 2)

    def test_changed_prerequisite_impacts_transitive_dependent_roots(self):
        for key in ("launch", "permit", "inspection"):
            self.add(key)
        self.edge("launch", "permit")
        self.edge("permit", "inspection")
        engine = MemoryEngine(self.store, candidate_search=SelectedNeighbours())
        engine.define_view(ViewSpec("opening", "Opening", "Open east building?", root_ids=("launch",)))
        self.assertEqual(engine.impact("inspection"), ["opening"])

    def test_changed_dependent_does_not_invalidate_siblings_using_same_permit(self):
        for key in ("east", "west", "permit"):
            self.add(key)
        self.edge("east", "permit")
        self.edge("west", "permit")
        engine = MemoryEngine(self.store, candidate_search=SelectedNeighbours())
        for key in ("east", "west"):
            engine.define_view(ViewSpec(key, key, f"Open {key}?", root_ids=(key,)))
        self.assertEqual(engine.impact("east"), ["east"])

    def test_canonical_prerequisite_condition_is_published(self):
        self.add("launch", "Opening approved.")
        self.add("permit", "The permit must be signed before opening.")
        self.edge("launch", "permit", condition="only with signed fire permit")
        engine = MemoryEngine(self.store, model=lambda request: "Opening approved [launch].",
                              candidate_search=SelectedNeighbours())
        engine.define_view(ViewSpec("opening", "Opening", "Open east building?", root_ids=("launch",)))
        self.assertIn("only with signed fire permit", engine.build_view("opening").summary)

    def test_batched_shared_prerequisite_does_not_join_all_dependents_into_one_component(self):
        self.add("permit", "Signed fire permit is required. " + "Permit context. " * 50)
        roots = []
        for index in range(90):
            key = f"launch-{index:03}"
            roots.append(key)
            self.add(key, f"Opening {index} is approved. " + "Local explanatory context. " * 40)
            self.edge(key, "permit", condition="only with signed fire permit")
        requests = []
        def model(request):
            requests.append(request)
            self.assertLessEqual(len(format_prompt(request)), 60_000)
            return "Grounded fixture " + " ".join(f"[{row['id']}]" for row in request.records)
        engine = MemoryEngine(self.store, model=model, candidate_search=SelectedNeighbours())
        engine.define_view(ViewSpec("opening", "Opening", "Opening conditions?", root_ids=tuple(roots)))
        version = engine.build_view("opening")
        self.assertGreater(len(requests), 1)
        for request in requests:
            ids = [row["id"] for row in request.records]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertIn("permit", ids)
        self.assertEqual({row["id"] for row in version.evidence}, {*roots, "permit"})
        self.assertEqual(len(version.edges), 90)
        self.assertEqual({row["id"] for request in requests for row in request.records}, {*roots, "permit"})


if __name__ == "__main__":
    unittest.main()
