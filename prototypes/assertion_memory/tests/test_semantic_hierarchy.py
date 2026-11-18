"""Automatic navigation discovers useful slices independently of prompt size."""
import json
import unittest
from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.background_overviews import _identity
from prototypes.assertion_memory.topic_hierarchy import group_definitions
from prototypes.assertion_memory.model import ModelError

class SemanticHierarchyTest(unittest.TestCase):
    def setUp(self):
        self.store=MemoryStore(':memory:');self.addCleanup(self.store.close)
        self.calls=[];self.groups=[]
        def model(request):
            self.calls.append(request)
            if request.task=='Plan reusable memory theme hierarchy':return json.dumps({'groups':self.groups})
            if request.task=='Plan memory retrieval for a question':return json.dumps({'reuse_view_id':None,'related_view_ids':[],'subquestions':[],'reason':'Distinct useful slice.'})
            self.fail('Unexpected task '+request.task)
        self.engine=MemoryEngine(self.store,model=model)
        for key,question in [('a','What source clocks are recorded?'),('b','How are missing clocks represented?'),('c','What is the garden watering schedule?')]:
            self.engine.define_view(ViewSpec(key,question,question,scopes=('s',)))
        self.spec,self.request,_=_identity(self.engine,'s',self.engine.capture_snapshot())
    def test_small_catalog_creates_real_semantic_group_once(self):
        self.groups=[{'question':'What governs source message time?', 'depends_on':['a','b']}]
        children,audit=group_definitions(self.engine,self.spec,self.request,['a','b','c','a'])
        self.assertEqual(len(audit),1)
        self.assertEqual(audit[0]['phase'],'semantic')
        self.assertEqual(audit[0]['ungrouped_view_ids'],['c'])
        group=self.engine._spec(next(key for key in children if key!='c'))
        self.assertEqual(group.depends_on,('a','b'))
        self.assertEqual(group.root_ids,())
        self.assertEqual(len([r for r in self.calls if r.task=='Plan reusable memory theme hierarchy']),1)
        self.assertEqual(len([r for r in self.calls[0].records if r.get('id')=='a']),1)
    def test_no_useful_group_preserves_forest_and_audit(self):
        children,audit=group_definitions(self.engine,self.spec,self.request,['a','c'])
        self.assertEqual(children,['a','c']);self.assertEqual(audit[0]['groups'],[])
        self.assertEqual(audit[0]['ungrouped_view_ids'],['a','c'])
    def test_overlap_singleton_and_ungrouped_are_legal(self):
        self.groups=[{'question':'What clock metadata is recorded?', 'depends_on':['a','b']},
                     {'question':'What clock is explicitly recorded?', 'depends_on':['a']}]
        children,audit=group_definitions(self.engine,self.spec,self.request,['a','b','c'])
        self.assertEqual(len(children),3)
        self.assertIn('c',children)
        self.assertEqual([self.engine._spec(k).depends_on for k in children if k!='c'],[('a','b'),('a',)])
    def test_unseen_child_rejected_without_publishing(self):
        self.groups=[{'question':'Injected slice','depends_on':['outside']}]
        with self.assertRaises(ModelError):group_definitions(self.engine,self.spec,self.request,['a','b'])
        self.assertEqual({v.id for v in self.engine.list_views()},{'a','b','c'})
    def test_one_automatic_child_does_not_need_wrapper(self):
        children,audit=group_definitions(self.engine,self.spec,self.request,['a','a'])
        self.assertEqual(children,['a']);self.assertEqual(audit,[]);self.assertEqual(self.calls,[])
    def test_protocol_versions_the_pipeline(self):
        from unittest.mock import patch
        before=self.engine.pipeline_key
        with patch('prototypes.assertion_memory.topic_hierarchy.HIERARCHY_PROTOCOL','next-contract'):
            changed=MemoryEngine(self.store,model=self.engine.model)
        self.assertNotEqual(before,changed.pipeline_key)
    def test_budget_reduction_follows_only_one_semantic_pass(self):
        keys=[]
        for index in range(20):
            key='large-'+str(index);keys.append(key)
            self.engine.define_view(ViewSpec(key,key,'Independent question '+str(index)+' detail'*220,scopes=('s',)))
        hierarchy_calls=[]
        def model(request):
            if request.task=='Plan reusable memory theme hierarchy':
                ids=[r['id'] for r in request.records if r.get('role')=='view']
                hierarchy_calls.append(ids)
                # Two batches in the semantic pass decline to invent a group.
                return json.dumps({'groups':[] if len(hierarchy_calls)<=2 else
                    [{'question':'What interface governs this bounded source family?', 'depends_on':ids}]})
            return json.dumps({'reuse_view_id':None,'related_view_ids':[],'subquestions':[],'reason':'Distinct slice.'})
        self.engine.model=model
        children,audit=group_definitions(self.engine,self.spec,self.request,keys)
        self.assertEqual([level['phase'] for level in audit],['semantic','budget'])
        self.assertEqual(audit[0]['ungrouped_view_ids'],keys)
        self.assertLess(len(children),len(keys))
        self.assertEqual({k for child in children for k in self.engine._spec(child).depends_on},set(keys))
    def test_manual_small_catalog_does_not_gain_semantic_pass(self):
        spec=ViewSpec('manual','User question','User question',scopes=('s',))
        children,audit=group_definitions(self.engine,spec,{'question':spec.query,'scopes':['s']},['a','b','a'])
        self.assertEqual(children,['a','b']);self.assertEqual(audit,[]);self.assertEqual(self.calls,[])

if __name__=='__main__':unittest.main()
