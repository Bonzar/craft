"""Versioned equal question formulations are searchable without duplicate thoughts."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from prototypes.assertion_memory.model import CandidateSearch
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.thought_selection import ThoughtSelection
from prototypes.assertion_memory.thought_engine import ThoughtEngine


class ThoughtQuestionsTest(unittest.TestCase):
    def setUp(self):
        self.memory=MemoryStore(':memory:'); self.addCleanup(self.memory.close)
        self.store=ThoughtStore(self.memory)

    def test_each_formulation_is_ranked_separately_but_one_thought_gets_one_slot(self):
        main=self.store.publish('Stored useful meaning.',question='Original formulation?',thought_id='main')
        seen=[]
        def embed(texts):
            seen.extend(texts)
            return [[1,0] if text=='Search query?' or text.startswith('Alternative ') else [.9,.1] for text in texts]
        engine=ThoughtEngine(self.store,None,candidates=CandidateSearch(embed))
        for index in range(100):engine._save_check(main,f'Alternative {index}?','frontier')
        others=[self.store.publish(f'Other meaning {index}.',question=f'Other question {index}?') for index in range(10)]
        result=engine.retrieve('Search query?')
        self.assertEqual(len(result),10)
        self.assertEqual(len({row.id for row in result}),10)
        self.assertEqual(sum(row.id==main.id for row in result),1)
        self.assertTrue(any(row.id in {item.id for item in others} for row in result))
        self.assertIn('Alternative 99?',seen)
        self.assertFalse(any('Alternative 99?' in text and text!='Alternative 99?' for text in seen))
        self.assertEqual(len(self.store.versions(main.id)),1)
        content=engine._content(main)
        self.assertNotIn('question',content)
        self.assertEqual(content['questions'],engine.question_texts(main))
        self.assertEqual(len(content['questions']),101)
        for frontier in ('new-frontier','new-frontier','later-frontier'):
            engine._save_check(main,'Alternative 99?',frontier)
        with self.memory.transaction() as c:
            import json
            row=c.execute("SELECT proofs FROM memory_thought_reuse_checks WHERE thought_id=? AND question='Alternative 99?'",(main.id,)).fetchone()
            self.assertEqual(len(json.loads(row[0])),1)
        self.assertEqual([row.id for row in self.store.search_text('Alternative 99')],[main.id])

    def test_new_version_does_not_inherit_applicability_of_old_questions(self):
        first=self.store.publish('Original meaning.',question='Old question?',thought_id='versioned')
        engine=ThoughtEngine(self.store,None)
        engine._save_check(first,'Previously verified alias?','old-frontier')
        latest=self.store.publish('Changed meaning.',question='New question?',thought_id=first.id,expected_version=1)
        self.assertEqual(engine.question_texts(latest),['New question?'])
        self.assertEqual(engine.question_texts(first),['Old question?','Previously verified alias?'])
        self.assertEqual(self.store.search_text('Previously verified alias'),[])
        self.assertFalse(self.store.question_checked(latest,'Previously verified alias?','old-frontier'))

    def test_scope_time_and_frozen_selection_apply_to_question_proofs(self):
        with patch('prototypes.assertion_memory.thought_store.datetime',wraps=datetime) as clock:
            clock.now.return_value=datetime(2026,1,1,tzinfo=timezone.utc)
            thought=self.store.publish('Home fact.',question='Original home question?',origin={'scope':'home','valid_at':'2026-01-01'})
        global_engine=ThoughtEngine(self.store,None)
        with patch('prototypes.assertion_memory.thought_questions._now',return_value='2026-01-03T00:00:00.000000+00:00'):
            global_engine._save_check(thought,'Later global formulation?','late')
        historical=ThoughtSelection(self.store,scope='home',valid_as_of='2026-01-02',recorded_as_of='2026-01-02')
        self.assertEqual(historical.questions(thought),['Original home question?'])
        frozen=ThoughtSelection(self.store,scope='home')
        scoped_engine=ThoughtEngine(ThoughtSelection(self.store,scope='home'),None)
        scoped_engine._save_check(thought,'Scoped verified formulation?','scoped')
        self.assertNotIn('Scoped verified formulation?',frozen.questions(thought))
        self.assertIn('Scoped verified formulation?',scoped_engine.question_texts(thought))
        self.assertNotIn('Scoped verified formulation?',global_engine.question_texts(thought))
        self.assertEqual(ThoughtEngine(ThoughtSelection(self.store,scope='work'),None).retrieve('Scoped verified formulation?'),[])


if __name__=='__main__':unittest.main()
