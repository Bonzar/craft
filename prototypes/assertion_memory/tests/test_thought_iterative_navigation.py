"""Hermetic semantic routing, incoming edges, ambiguity and selection boundaries."""
import json
import unittest
from unittest.mock import patch

from prototypes.assertion_memory.model import Candidate, ModelContextExceeded
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_navigation import navigate, neighborhood
from prototypes.assertion_memory.thought_selection import ThoughtSelection
from prototypes.assertion_memory.thought_store import ThoughtStore


def decision(action,quote=None,question=None):
    return {'action':action,'quote':quote,'question':question,'text':'The visible content identifies the next useful location.'}


class Model:
    def __init__(self,*steps):self.steps=list(steps);self.requests=[]
    def __call__(self,request):
        self.requests.append(request)
        step=self.steps.pop(0)
        if isinstance(step,Exception):raise step
        return json.dumps(step)


class OnlyManagement:
    def __init__(self,key):self.key=key;self.calls=[]
    def rank_relevance(self,question,corpus,limit=10):
        self.calls.append(question)
        key = next(key for key in corpus if json.loads(key)[0] == self.key)
        return [Candidate(key,1.0,'embedding')]


class ThoughtIterativeNavigationTest(unittest.TestCase):
    def setUp(self):
        self.memory=MemoryStore(':memory:');self.addCleanup(self.memory.close)
        self.store=ThoughtStore(self.memory)

    def test_management_seed_reaches_campus_incoming_and_readiness_basis(self):
        management=self.store.publish('Management records agreements and assigns responsibilities.',question='How is management organized?',thought_id='technical-management')
        readiness=self.store.publish('Campus readiness requires the signed inspection report.',question='When is the campus ready?',thought_id='technical-readiness')
        campus=self.store.publish('The campus connects management and readiness work.',question='What is the campus?',thought_id='technical-campus',bases=[{'thought_id':management.id},{'thought_id':readiness.id}])
        unrelated=self.store.publish('An unrelated management attachment.',thought_id='technical-unrelated',bases=[{'thought_id':management.id}])
        model=Model(decision('inspect',quote=management.question),decision('inspect',quote=campus.question),decision('inspect',quote=readiness.question),decision('focus',quote=readiness.text))
        ranked=OnlyManagement(management.id);trace=[];engine=ThoughtEngine(self.store,model,ranked,trace.append)
        result=navigate(engine,'What must happen before opening the campus?')
        self.assertEqual(result.thought.id,readiness.id)
        self.assertEqual([r.id for r in result.inspected],[management.id,campus.id,readiness.id])
        self.assertEqual(len(ranked.calls),1)
        # Search results are not expanded until explicitly inspected.
        self.assertNotIn(campus.text,json.dumps(model.requests[0].records))
        self.assertIn(campus.text,json.dumps(model.requests[1].records))
        self.assertNotIn(unrelated.text,json.dumps(model.requests[3].records))
        for request in model.requests:
            self.assertEqual(request.task,'Navigate thought graph')
            self.assertIn('quote',request.response_schema['properties'])
            for thought in (management,readiness,campus):self.assertNotIn(thought.id,json.dumps(request.records))
        choices=[row for row in trace if row['stage']=='navigation_choice']
        self.assertEqual(choices[1]['thought'],{'thought_id':campus.id,'version':1})
        self.assertTrue(choices[1]['spans'])

    def test_ambiguous_quote_and_unchanged_search_do_not_guess_or_loop(self):
        a=self.store.publish('The shared phrase is not a unique address.',thought_id='a')
        b=self.store.publish('The shared phrase also occurs here.',thought_id='b')
        engine=ThoughtEngine(self.store,Model(decision('inspect',quote='shared phrase'),
            {'action':'unknown','quote':None,'question':None,'text':'The visible quotation does not uniquely identify a thought.'}))
        with patch.object(engine,'retrieve',return_value=[a,b]):result=navigate(engine,'Which material answers this?')
        self.assertIsNone(result.thought);self.assertIn('does not uniquely identify',result.reason)
        model=Model(decision('search',question='Another formulation'),decision('search',question='Another formulation'))
        engine=ThoughtEngine(self.store,model)
        with patch.object(engine,'retrieve',return_value=[a]):result=navigate(engine,'Original question')
        self.assertIsNone(result.thought);self.assertEqual(len(model.requests),2)
        self.assertEqual(result.reason,'repeated_navigation_search')

    def test_neighbors_obey_selection_exact_versions_and_real_provider_capacity(self):
        a=self.store.publish('Alpha source.',thought_id='a',origin={'scope':'alpha'})
        b=self.store.publish('Beta source.',thought_id='b',origin={'scope':'beta'})
        mixed=self.store.publish('Cross-scope composite.',thought_id='mixed',bases=[{'thought_id':'a'},{'thought_id':'b'}])
        self.store.connect(a.id,b.id,'A cross-scope semantic connection.')
        selection=ThoughtSelection(self.store,scope='alpha');engine=ThoughtEngine(selection,Model())
        catalog=neighborhood(engine,a,'An alpha question')
        self.assertEqual(catalog['neighbors'],())
        self.assertNotIn(mixed.id,[r['thought_id'] for r in catalog['bindings']])
        self.store.publish('Updated alpha source.',thought_id=a.id,expected_version=1,origin=a.origin)
        engine=ThoughtEngine(self.store,Model(decision('inspect',quote=mixed.text),ModelContextExceeded('reported actual capacity')))
        with patch.object(engine,'retrieve',return_value=[mixed]):result=navigate(engine,'Which old evidence was used?')
        self.assertEqual(result.inspected,(mixed,))
        self.assertIsNone(result.thought);self.assertIn('provider_context_capacity_exceeded',result.reason)
        catalog=neighborhood(engine,mixed,'Which old evidence was used?')
        self.assertIn({'thought_id':'a','version':1},catalog['bindings'])
        self.assertIn({'thought_id':'a','version':2},catalog['bindings'])
        self.assertTrue(any(row.get('pinned_target_ref')=={'thought_id':'a','version':1}
                            for row in catalog['relations']))


if __name__=='__main__':unittest.main()
