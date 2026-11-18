"""Hybrid evidence ranking preserves sparse details and semantic neighbours."""

import unittest

from prototypes.assertion_memory.model import CandidateSearch, _retrieval_features


class HybridRankingTest(unittest.TestCase):
    def test_specific_evidence_survives_a_long_metadata_query(self):
        query = ("Which documents establish loading platform and freight gate elevations? "
                 "Identify document versions, validity, replacement, dates and applicability.")
        specific = "Drawing ZX-47 places the loading platform at 2.40 and freight gate at -0.60."
        distractor = "The registry records document versions, validity, replacement and applicability dates."
        repeated = distractor + " The registry also tracks later document revisions."
        vectors = {query: [1, 0], specific: [.25, .968], distractor: [.95, .312],
                   repeated: [.94, .34]}
        search = CandidateSearch(lambda texts: [vectors[text] for text in texts])
        corpus = {"specific": specific, "generic": distractor, "repeat": repeated}
        self.assertIn("specific", [hit.id for hit in search.rank(query, corpus, limit=2)])
        self.assertNotIn("specific", [hit.id for hit in search.rank_relevance(query, corpus, limit=2)])

    def test_numeric_or_document_anchor_does_not_replace_entity_matching(self):
        for anchor in ("-0.60", "ZX-47", "47"):
            query = f"freight gate elevation {anchor}"
            right = "The freight gate elevation is given in the approved site drawing."
            wrong = f"Pump pressure calibration {anchor} is recorded in a laboratory register."
            vectors = {query: [1, 0], right: [.9, .435], wrong: [.3, .954]}
            search = CandidateSearch(lambda texts: [vectors[text] for text in texts])
            with self.subTest(anchor=anchor):
                self.assertEqual(search.rank(query, {"right": right, "wrong": wrong}, limit=1)[0].id,
                                 "right")

    def test_measurement_sign_decimal_and_drawing_identifier_stay_intact(self):
        features = _retrieval_features("ZX–47: -0,60; +2.40")
        self.assertIn("anchor:zx-47", features)
        self.assertIn("anchor:-0.60", features)
        self.assertNotIn("anchor:0.60", features)
        self.assertIn("anchor:+2.40", features)
        self.assertNotIn("word:60", features)

    def test_sparse_match_cannot_erase_cross_language_neighbour(self):
        query = "What happens when creating the first revision fails?"
        paraphrase = "При ошибке создания первой версии новая автоматизация откатывается."
        distractor = "What happens when a browser window opens?"
        repeated = distractor + " The window opens before the first click."
        vectors = {query: [1, 0], paraphrase: [.9, -.435], distractor: [.1, .995],
                   repeated: [.09, .996]}
        search = CandidateSearch(lambda texts: [vectors[text] for text in texts])
        result = search.rank(query, {"translation": paraphrase, "words": distractor,
                                     "repeat": repeated}, limit=2)
        self.assertIn("translation", [row.id for row in result])
        self.assertAlmostEqual(search.rank_relevance(query, {"translation": paraphrase})[0].score,
                               .9 / (.9 ** 2 + .435 ** 2) ** .5)

    def test_numeric_overlap_alone_does_not_create_a_lexical_candidate(self):
        for number in ("-0.60", "47"):
            query = f"freight elevation {number}"
            unrelated = f"насос давление {number}"
            search = CandidateSearch(lambda texts: [[1, 0], [0, 1]])
            with self.subTest(number=number):
                self.assertEqual(search.rank(query, {"unrelated": unrelated}), [])


if __name__ == "__main__":
    unittest.main()
