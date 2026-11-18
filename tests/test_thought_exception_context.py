import json,unittest
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.model import SemanticUnavailable

class ThoughtExceptionContextTest(unittest.TestCase):
    def test_unselected_exception_is_checked_without_becoming_a_factual_ground(self):
        with MemoryStore(':memory:') as store:
            thoughts=ThoughtStore(store);thoughts.publish('Rooms are reported ready.')
            thoughts.publish('Rooms 214 and 216 are exceptions: ventilation is not checked.')
            requests=[]
            def model(request):
                requests.append(request)
                if request.task=='Check ready thought':return json.dumps({'verdict':'build','text':'A combined answer is needed.'})
                if request.task=='Construct thought':return json.dumps({'thoughts':[{'text':'All rooms are ready.','question':None,'support':['Rooms are reported ready.']}]})
                exception=next(row for row in request.records if '214' in row.get('text',''))
                self.assertFalse(exception['selected_for_grounding'])
                return json.dumps({'verdict':'reject','text':'The draft lost the room exceptions.'})
            with self.assertRaises(SemanticUnavailable):ThoughtEngine(thoughts,model).answer('Are all rooms ready?')
            self.assertEqual(len(thoughts.list_thoughts()),2)
