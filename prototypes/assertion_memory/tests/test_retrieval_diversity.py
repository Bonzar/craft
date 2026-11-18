"""Evidence selection keeps separate branches despite repeated wording."""

import tempfile
import unittest
from pathlib import Path

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import CandidateSearch
from prototypes.assertion_memory.storage import MemoryStore


class RetrievalDiversityTest(unittest.TestCase):
    def test_repetition_does_not_displace_an_independent_branch(self):
        vectors = {"question": [1, 0], "first": [.9, .43589],
                   "repeat": [.899, .437], "other": [.8, -.6]}
        search = CandidateSearch(lambda texts: [vectors[text] for text in texts])
        corpus = {"a": "first", "b": "repeat", "c": "other"}
        self.assertEqual([hit.id for hit in search.rank("question", corpus, limit=2)], ["a", "c"])
        # Confidence requires the actual runner-up, not the diverse alternative.
        self.assertEqual(
            [hit.id for hit in search.rank_relevance("question", corpus, limit=2)], ["a", "b"],
        )
        self.assertEqual(set(corpus), {"a", "b", "c"})

    def test_explicit_scopes_keep_separate_budgets_and_linked_evidence(self):
        vectors = {"question": [1, 0], "first": [1, 0], "second": [.9, .1],
                   "other": [.1, .9], "exception": [0, 1]}
        search = CandidateSearch(lambda texts: [vectors[text] for text in texts])
        with tempfile.TemporaryDirectory() as directory, MemoryStore(Path(directory) / "store.sqlite") as store:
            for id_, scope in (("first", "one"), ("second", "one"), ("other", "two"), ("exception", "outside")):
                store.append_assertion(
                    assertion_id=id_, text=id_, actor="user", source="fixture://test", episode="test",
                    recorded_at="2026-01-01", scope=scope,
                )
            store.append_edge(
                edge_id="exception/first", from_id="exception", to_id="first", kind="exception_to",
                recorded_at="2026-01-01",
            )
            engine = MemoryEngine(store, candidate_search=search)
            engine.define_view(ViewSpec("scoped", "question", "question", scopes=("one", "two"), max_seeds=1))
            scoped = engine.build_view("scoped")
            self.assertEqual({row["id"] for row in scoped.evidence}, {"first", "other", "exception"})
            engine.define_view(ViewSpec("global", "question", "question", max_seeds=1))
            unscoped = engine.build_view("global")
            self.assertEqual({row["id"] for row in unscoped.evidence}, {"first", "exception"})


if __name__ == "__main__":
    unittest.main()
