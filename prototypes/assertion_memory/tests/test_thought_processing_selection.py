"""A selected source session owns only its causally tagged processing cohort."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.thought_worker import ThoughtWorker
from prototypes.assertion_memory.thought_worker_queue import ThoughtWorkQueue
from prototypes.assertion_memory.thought_processing_selection import processing_cohort

X='00000000-0000-4000-8000-000000000002'
Y='other-native-session'


class ThoughtProcessingSelectionTest(unittest.TestCase):
    def engine(self,memory,thoughts):
        return SimpleNamespace(store=memory,thoughts=thoughts,model=lambda request:self.fail('no model'),gateway=None,
            question_texts=thoughts.questions,retrieve=lambda *args,**kwargs:[],consolidate=lambda changed:[])

    def test_raw_selection_uses_native_or_stored_session_not_url_and_leaves_other_leases_alone(self):
        with MemoryStore(':memory:') as memory:
            thoughts,queue=ThoughtStore(memory),CaptureQueue(memory)
            wrong=queue.capture_event(X,'wrong','Other session.',source='codex://'+X,metadata={'native_session_id':Y})
            chosen=queue.capture_event('stored-alias','right','Selected session.',source='fixture://'+Y,metadata={'native_session_id':X})
            fallback=queue.capture_event(X,'fallback','Stored fallback.',source='fixture')
            with memory.transaction(write=True) as c:
                c.execute("UPDATE memory_capture_jobs SET status='running',lease_token='other',lease_until='2000-01-01',attempts=99 WHERE id=?",(wrong.id,))
            with memory.transaction(write=True) as c:queue._scheduler_schema(c)
            before=queue.get(wrong.id)
            engine=self.engine(memory,thoughts)
            engine.ingest=lambda event,context,consolidate=False:[thoughts.publish(event.prompt)]
            self.assertIsNone(ThoughtWorker(queue,engine,processing_session_ids=()).run_once())
            worker=ThoughtWorker(queue,engine,lane='source',processing_session_ids=(X,))
            self.assertEqual({worker.run_once().event_id,worker.run_once().event_id},{chosen.id,fallback.id})
            self.assertIsNone(worker.run_once())
            self.assertIsNone(worker.seconds_until_work(with_model=True))
            self.assertEqual(queue.get(wrong.id),before)
            task=ThoughtWorkQueue(queue,processing_session_ids=(X,)).claim()
            self.assertEqual(task['processing_session_ids'],(X,))

    def test_shared_whole_refresh_and_all_derived_work_inherit_only_active_allowed_cohort(self):
        with MemoryStore(':memory:') as memory:
            thoughts,queue=ThoughtStore(memory),CaptureQueue(memory)
            a,b=thoughts.publish('Y knowledge.'),thoughts.publish('X fact.')
            whole=thoughts.publish('Shared whole.',question='Shared question?',bases=[{'thought_id':a.id,'version':1}])
            other=thoughts.publish('Independent Y work.',question='Y question?')
            legacy=thoughts.publish('Untyped legacy work.',question='Legacy question?')
            work=ThoughtWorkQueue(queue)
            with processing_cohort((Y,)):
                work.enqueue_refresh(whole,(a.id,1));work.enqueue([other])
            work.enqueue([legacy])
            with processing_cohort((X,)):work.enqueue_refresh(whole,(b.id,1))
            engine=self.engine(memory,thoughts)
            worker=ThoughtWorker(queue,engine,lane='maintenance',processing_session_ids=(X,))
            task=worker.work.claim()
            self.assertEqual(task['thought_id'],whole.id)
            self.assertEqual(task['processing_session_ids'],(X,))
            def refresh(engine,current,additional=()):
                self.assertEqual({row.id for row in additional},{a.id,b.id})
                return thoughts.publish('Updated shared whole.',thought_id=current.id,question=current.question,
                    bases=[{'thought_id':a.id,'version':1},{'thought_id':b.id,'version':1}],expected_version=1)
            with patch('prototypes.assertion_memory.thought_worker.refresh_thought',side_effect=refresh):worker._consolidate(task)
            with memory.transaction() as c:
                tags={row[0] for row in c.execute('SELECT session_id FROM memory_thought_cause_sessions WHERE thought_id=? AND cause=?',
                    (whole.id,json.dumps(['published',whole.id,2],sort_keys=True)))}
            self.assertEqual(tags,{X})
            descendant=[]
            def consolidate(changed):
                if changed[0].id==whole.id:
                    descendant.append(thoughts.publish('Further generalization.',bases=[{'thought_id':whole.id,'version':2}]))
                    return descendant
                return []
            engine.consolidate=consolidate
            worker.run_once();worker.run_once()
            self.assertEqual(worker.work.get(descendant[0].id)['status'],'completed')
            self.assertEqual(work.get(other.id)['status'],'pending')
            self.assertEqual(work.get(legacy.id)['status'],'pending')
            self.assertIsNone(worker.run_once())
            self.assertIsNone(worker.seconds_until_work(with_model=True))

    def test_newer_unallowed_generation_and_untagged_legacy_are_not_claimed_or_deadlined(self):
        with MemoryStore(':memory:') as memory:
            queue=CaptureQueue(memory);thoughts=ThoughtStore(memory)
            whole=thoughts.publish('Shared.',question='Question?')
            work=ThoughtWorkQueue(queue)
            with processing_cohort((X,)):work.enqueue_refresh(whole,('x',1))
            scoped=ThoughtWorkQueue(queue,processing_session_ids=(X,))
            task=scoped.claim()
            with processing_cohort((Y,)):work.enqueue_refresh(whole,('y',1))
            scoped.finish(task,1)
            self.assertIsNone(scoped.claim());self.assertIsNone(scoped.deadline())
            self.assertEqual(work.get(whole.id)['status'],'pending')
            self.assertEqual(work.claim()['processing_session_ids'],(Y,))


if __name__=='__main__':unittest.main()
