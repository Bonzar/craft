import json
import unittest
from prototypes.assertion_memory.thought_engine_inputs import quote_span

class ThoughtQuoteSpansTest(unittest.TestCase):
    def test_json_annotation_logical_newlines_bind_original_escaped_fragment(self):
        quote='Первый вопрос?\n\nНадо решить скорее как сохранять.'
        text='# Response annotations:\n'+json.dumps([{'annotation':quote,'text':'Earlier words'}],ensure_ascii=False)
        start,end=quote_span(text,quote)
        self.assertEqual(json.loads('"'+text[start:end]+'"'),quote)
        self.assertNotIn(quote,text)

    def test_unrelated_paraphrase_is_not_fuzzily_bound(self):
        self.assertIsNone(quote_span('Решение пока не принято.','Решение принято.'))
