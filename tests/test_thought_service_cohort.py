"""Query attribution tags downstream work without narrowing readable knowledge."""
import unittest
from unittest.mock import patch
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_service import ThoughtService
from prototypes.assertion_memory.thought_processing_selection import processing_cohort,current_cohort


class ThoughtServiceCohortTest(unittest.TestCase):
    def test_request_session_tags_query_work_but_anonymous_query_does_not_inherit_outer_cohort(self):
        with MemoryStore(':memory:') as memory:
            service=ThoughtService(memory,processing_session_ids=('selected',))
            other=service.thoughts.publish('Other session knowledge is still readable.',origin={'scope':'other'})
            seen=[]
            def answer(question):
                seen.append(current_cohort())
                self.assertEqual(service.engine.thoughts.get(other.id),other)
                return service.thoughts.publish('A useful answer.',question=question,metadata={'verification':'accepted'})
            with patch('prototypes.assertion_memory.thought_service.traced_engine',return_value=service.engine),patch.object(service.engine,'answer',side_effect=answer):
                with processing_cohort(('outer',)):
                    first=service.query('Selected question?',request_session_id='selected')
                    second=service.query('Anonymous question?')
                    self.assertEqual(current_cohort(),('outer',))
            self.assertEqual(seen,[('selected',),()])
            task=service.work.claim()
            self.assertEqual(task['thought_id'],first['thought']['id'])
            service.work.finish(task,1)
            self.assertIsNone(service.work.claim())
            self.assertEqual(service.work.get(second['thought']['id'])['status'],'pending')
            self.assertEqual(service.status()['configured_processing_session_ids'],['selected'])


if __name__=='__main__':unittest.main()
