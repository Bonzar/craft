"""Golden characterization of ranking, explicit seeds, and trace before extraction."""
import unittest
from unittest.mock import Mock, patch

from prototypes.assertion_memory.model import Candidate
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_service import ThoughtService


class ThoughtSearchBoundaryTest(unittest.TestCase):
    def test_ranked_thoughts_and_explicit_results_are_exact_without_generation_or_publication(self):
        model = Mock(side_effect=AssertionError('search must not call the generative model'))
        ranker = Mock()
        with MemoryStore(':memory:') as db:
            service = ThoughtService(db, model, ranker)
            for key in ('b', 'a', 'c', 'd'):
                service.thoughts.publish(f'material {key}', question=f'question {key}', thought_id=key)
            # A question match beats a text match within the thought. Equal
            # best scores across thoughts are sorted by identity, not ranker order.
            ranker.rank_relevance.return_value = [
                Candidate('["b",0]', .7, 'fixture'), Candidate('["a",1]', .7, 'fixture'),
                Candidate('["a",0]', .1, 'fixture'), Candidate('["b",1]', .1, 'fixture'),
            ]
            trace = []
            service.engine.trace = trace.append
            with patch.object(service.thoughts, 'publish', side_effect=AssertionError('publishes')), \
                 patch.object(service.work, 'enqueue', side_effect=AssertionError('schedules')):
                result = service.engine.retrieve('lookup', ('c', 'a', 'missing'), 2, exclude_ids=('d',))
            self.assertEqual([x.id for x in result], ['a', 'b', 'c'])
            ranker.rank_relevance.assert_called_once_with('lookup', {
                '["a",0]':'material a', '["a",1]':'question a',
                '["b",0]':'material b', '["b",1]':'question b',
                '["c",0]':'material c', '["c",1]':'question c',
            }, limit=6)
            self.assertEqual(trace, [{'stage':'retrieval','question':'lookup','candidates':[
                {'thought_id':'a','version':1,'score':.7,'method':'fixture','matched_input':{'kind':'question','text':'question a'}},
                {'thought_id':'b','version':1,'score':.7,'method':'fixture','matched_input':{'kind':'text','text':'material b'}},
            ],'selected':[{'thought_id':key,'version':1} for key in ('a','b','c')],
                'explicit_ids':['c','a','missing']}])
            model.assert_not_called()

    def test_injected_retrieval_is_shared_by_traced_scoped_query_and_uses_pinned_projection(self):
        from prototypes.assertion_memory.thought_retrieval import ThoughtRetrieval
        from prototypes.assertion_memory.thought_selection import ThoughtSelection
        ranker = Mock()
        ranker.rank_relevance.return_value = []
        retrieval = Mock(wraps=ThoughtRetrieval(ranker))
        with MemoryStore(':memory:') as db:
            service = ThoughtService(db, candidates=ranker, retrieval=retrieval)
            self.assertIs(service.engine.retrieval, retrieval)
            self.assertEqual(service.engine.retrieve('Unanswered?'), [])
            result = service.query('Scoped unanswered?', scope='fixture')
            self.assertFalse(result['answer_accepted'])
            self.assertEqual(retrieval.search.call_count, 2)
            first, second = (call.args for call in retrieval.search.call_args_list)
            self.assertIs(first[0], service.thoughts)
            self.assertIsInstance(second[0], ThoughtSelection)
            self.assertEqual(second[0].context['scopes'], ['fixture'])
            self.assertEqual(first[1], 'Unanswered?')
            self.assertEqual(second[1], 'Scoped unanswered?')

    def test_frozen_text_search_uses_one_injected_lexical_component(self):
        from prototypes.assertion_memory.model import CandidateSearch
        from prototypes.assertion_memory.thought_retrieval import ThoughtRetrieval
        from prototypes.assertion_memory.thought_selection import ThoughtSelection
        lexical = Mock(wraps=ThoughtRetrieval(CandidateSearch()))
        with MemoryStore(':memory:') as db:
            service = ThoughtService(db)
            a = service.thoughts.publish('Amber route', thought_id='a')
            service.thoughts.publish('Supplies', thought_id='b')
            selected = ThoughtSelection(service.thoughts, lexical_retrieval=lexical)
            self.assertEqual(selected.search_text('Amber', limit=1), [a])
            self.assertEqual(selected.search_text('Amber', limit=1), [a])
            self.assertEqual(lexical.rank_entries.call_count, 2)
