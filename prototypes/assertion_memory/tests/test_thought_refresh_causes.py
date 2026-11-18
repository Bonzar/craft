"""Generation-bound change causes become actual whole-refresh material."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.thought_worker import ThoughtWorker
from prototypes.assertion_memory.thought_worker_queue import ThoughtWorkQueue


class ThoughtRefreshCausesTest(unittest.TestCase):
    def test_related_new_material_is_passed_only_to_its_claimed_generation(self):
        with MemoryStore(':memory:') as memory:
            thoughts,queue=ThoughtStore(memory),CaptureQueue(memory)
            a=thoughts.publish('Existing basis A.')
            b=thoughts.publish('New related fact B.')
            newer=thoughts.publish('Newer related fact D.')
            whole=thoughts.publish('Whole content C.',question='What is known?',bases=[{'thought_id':a.id,'version':1}])
            engine=SimpleNamespace(store=memory,thoughts=thoughts,model=lambda request:self.fail('no model expected'),
                gateway=None,question_texts=thoughts.questions,retrieve=lambda *args,**kwargs:[whole])
            worker=ThoughtWorker(queue,engine,lane='maintenance')
            self.assertNotIn(b.id,[basis.get('thought_id') for basis in whole.bases])
            worker.enqueue_dependents([b])
            worker.work.enqueue_refresh(whole,(whole.id,whole.version))
            worker.work.enqueue_refresh(whole,(newer.id,newer.version,'retired'))
            first=worker.work.claim()
            worker.enqueue_dependents([newer])
            received=[]
            def refresh(engine,current,*,additional=()):
                received.append([row.id for row in additional])
                self.assertEqual(current.bases[0]['thought_id'],a.id)
                return current
            with patch('prototypes.assertion_memory.thought_worker.refresh_thought',side_effect=refresh):
                result=worker._consolidate(first)
                self.assertEqual(result['status'],'pending')
                self.assertEqual(received,[[b.id]])
                second=worker.work.claim()
                self.assertEqual(worker._consolidate(second)['status'],'completed')
            self.assertEqual(received,[[b.id],[newer.id]])
            self.assertEqual(thoughts.get(whole.id),whole)

    def test_legacy_causes_backfill_one_unknown_order_group_and_completed_causes_are_not_reread(self):
        with MemoryStore(':memory:') as memory:
            thoughts,queue=ThoughtStore(memory),CaptureQueue(memory)
            whole=thoughts.publish('A whole thought.',question='Question?')
            work=ThoughtWorkQueue(queue)
            work.enqueue_refresh(whole,('old-child',1))
            with memory.transaction(write=True) as c:
                c.execute('DROP TABLE memory_thought_work_causes')
                c.execute('CREATE TABLE memory_thought_work_causes(thought_id TEXT NOT NULL,cause TEXT NOT NULL,PRIMARY KEY(thought_id,cause))')
                c.executemany('INSERT INTO memory_thought_work_causes VALUES(?,?)',
                    [(whole.id,json.dumps(['basis',f'old-{index}',1])) for index in range(20)])
                c.execute('UPDATE memory_thought_work SET desired_generation=7,completed_generation=5 WHERE thought_id=?',(whole.id,))
            work=ThoughtWorkQueue(queue)
            first=work.claim()
            self.assertEqual(len(work.causes(first)),20)
            with memory.transaction() as c:
                self.assertEqual({row[0] for row in c.execute('SELECT generation FROM memory_thought_work_causes')},{7})
            work.finish(first,whole.version)
            work.enqueue_refresh(whole,('new-child',1))
            second=work.claim()
            self.assertEqual(work.causes(second),[['basis','new-child',1]])
            with memory.transaction() as c:
                plan=c.execute('EXPLAIN QUERY PLAN SELECT cause FROM memory_thought_work_causes WHERE thought_id=? AND generation>? AND generation<=?',
                    (whole.id,7,8)).fetchall()
                self.assertTrue(any('SEARCH' in row[3] and 'generation' in row[3] for row in plan))


if __name__=='__main__':unittest.main()
