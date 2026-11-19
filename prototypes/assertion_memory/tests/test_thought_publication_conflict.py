"""Verified competing results survive without overwriting newer meaning."""
import json
import unittest
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_store import ThoughtStore, ThoughtConflict
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from .prepared_support_helpers import drafts


class ThoughtPublicationConflictTest(unittest.TestCase):
    def test_same_current_grounds_preserve_parallel_alternative_and_unresolved_relation(self):
        with MemoryStore(':memory:') as memory:
            thoughts = ThoughtStore(memory)
            base = thoughts.publish('Approval is required.',thought_id='base')
            target = thoughts.publish('An earlier answer.',thought_id='private-target-id',question='When is opening allowed?',
                bases=[{'thought_id':base.id,'version':1}])
            engine = ThoughtEngine(thoughts,lambda request:self.fail('unchanged grounds need no model rebuild'))
            first = engine._publish([({'text':'Opening requires approval.','question':target.question},
                [{'thought_id':base.id,'version':1}])],target=target)[0]
            alternate = engine._publish([({'text':'Approval remains a condition; opening has not been observed.','question':target.question},
                [{'thought_id':base.id,'version':1}])],target=target)[0]
            self.assertEqual(thoughts.get(target.id),first)
            self.assertNotEqual(alternate.id,target.id)
            self.assertEqual(alternate.metadata['publication']['state'],'parallel_alternative')
            self.assertEqual(alternate.bases[0]['version'],1)
            link = thoughts.links(alternate.id)[0]
            self.assertEqual((link['target_id'],link['target_version']),(first.id,first.version))
            content = json.dumps(engine._content(alternate))
            self.assertIn('unresolved',content)
            self.assertNotIn(target.id,content)

    def test_changed_grounds_rebuild_once_outside_writer_then_repeat_race_is_recoverable(self):
        for race_again in (False,True):
            with self.subTest(race_again=race_again), MemoryStore(':memory:') as memory:
                thoughts = ThoughtStore(memory)
                base = thoughts.publish('Old approval is required.',thought_id='base')
                target = thoughts.publish('Old answer.',thought_id='target',question='What is required?',
                    bases=[{'thought_id':base.id,'version':1}])
                current = thoughts.publish('Fresh approval is required.',thought_id=base.id,expected_version=1)
                calls = []
                def model(request):
                    self.assertFalse(memory._connection.in_transaction)
                    calls.append(request)
                    if request.task=='Construct thought':
                        self.assertNotIn(base.text,json.dumps(request.records))
                        return json.dumps(drafts(request,(current.text,[current.text]),question=target.question))
                    if race_again:
                        thoughts.publish('Approval changed again.',thought_id=base.id,expected_version=2)
                    return json.dumps({'verdict':'accept','text':'Fresh supplied grounds support the answer.'})
                engine = ThoughtEngine(thoughts,model)
                prepared = [({'text':'Stale prepared answer.','question':target.question},[{'thought_id':base.id,'version':1}])]
                if race_again:
                    with self.assertRaises(ThoughtConflict) as caught:
                        engine._publish(prepared,target=target)
                    self.assertTrue(caught.exception.recoverable)
                    self.assertEqual(thoughts.get(target.id),target)
                else:
                    result = engine._publish(prepared,target=target)[0]
                    self.assertEqual(result.text,current.text)
                    self.assertEqual(result.bases[0]['version'],2)
                self.assertEqual(len(calls),2)
                self.assertFalse(any(row.metadata.get('publication') for row in thoughts.list_thoughts()))


if __name__=='__main__':
    unittest.main()
