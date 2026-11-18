"""Rank lightweight text first; admit and decode only selected current thoughts."""
import unittest
from unittest.mock import patch
from prototypes.assertion_memory.model import CandidateSearch
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_store import Thought, ThoughtStore
import prototypes.assertion_memory.thought_store as store_module


class ThoughtRetrievalIndexTest(unittest.TestCase):
    def test_unselected_large_payload_is_never_loaded_and_selected_source_policy_still_applies(self):
        with MemoryStore(':memory:') as memory:
            store = ThoughtStore(memory)
            good = store.publish('Kitchen readiness: walls painted and stove installed.', thought_id='good', question='Kitchen readiness?')
            noise = store.publish('Unrelated garden supplies.', thought_id='large-noise', metadata={'legacy': {'unused': 'x' * 200000}})
            known = store.publish('An explicit exception.', thought_id='known')
            hidden = store.publish('Kitchen readiness.', thought_id='historical', metadata={'selection_context': {'scope': 'old'}})
            bad = Thought('bad-tool',1,'Kitchen readiness kitchen readiness approved.','Kitchen readiness?',
                {'source':'codex://session/old/tool/exec','actor':'tool:exec_command'},[],{},'2026-01-01')
            with memory.transaction(write=True) as c:
                store._insert(c,bad)
            text = {row.id:'\n'.join(filter(None,(row.question,row.text))) for row in (good,noise,known,bad)}
            ranking = CandidateSearch().rank_relevance('Kitchen readiness?',text,limit=2)
            expected = [row.id for row in ranking if row.id != bad.id] + [known.id]
            decoded, trace = [], []
            original = store_module.decode_payload
            def decode(raw):
                payload = original(raw)
                decoded.append(payload['id'])
                return payload
            engine = ThoughtEngine(store,None,trace=trace.append)
            with patch.object(store,'list_thoughts',side_effect=AssertionError('eager corpus payload loading')), patch.object(store_module,'decode_payload',side_effect=decode):
                result = engine.retrieve('Kitchen readiness?',explicit_ids=[known.id,hidden.id],limit=2)
            self.assertEqual([row.id for row in result],list(dict.fromkeys(expected)))
            self.assertNotIn(noise.id,decoded)
            self.assertNotIn(hidden.id,decoded)
            self.assertIn(bad.id,decoded)
            self.assertNotIn(bad.id,[row.id for row in result])
            event = next(row for row in trace if row['stage']=='retrieval')
            self.assertEqual(event['candidates'][0]['version'],1)
            self.assertEqual(event['selected'],[{'thought_id':row.id,'version':row.version} for row in result])


if __name__ == '__main__':
    unittest.main()
