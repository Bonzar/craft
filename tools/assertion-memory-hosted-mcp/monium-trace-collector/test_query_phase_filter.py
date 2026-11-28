"""Read the actual collector shape expression without starting YC or a collector."""
from pathlib import Path
import re
import unittest


class CollectorQueryPhaseFilter(unittest.TestCase):
    def test_accepts_only_named_query_phases(self):
        config = Path(__file__).with_name('common.yaml').read_text()
        rules = re.findall(r'not IsMatch\(span\.name, "([^"]+)"\)', config)
        self.assertEqual(len(rules),1)
        shape = re.compile(rules[0])
        for name in ('openai.chat','memory_query.search','memory_query.sufficiency',
                     'memory_query.relation_neighborhood','memory_query.relation_frontier',
                     'memory_query.draft_generation','memory_query.answer_verification'):
            with self.subTest(name=name):
                self.assertIsNotNone(shape.fullmatch(name))
        for name in ('memory_query.raw_payload','memory_query.sufficiency.secret',
                     'memory_query.relation_any','outside.search'):
            with self.subTest(name=name):
                self.assertIsNone(shape.fullmatch(name))
