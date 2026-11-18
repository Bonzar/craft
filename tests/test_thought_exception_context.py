import json,unittest
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_engine_contract import SemanticReviewRejected

class ThoughtExceptionContextTest(unittest.TestCase):
    def test_unselected_exception_is_checked_without_becoming_a_factual_ground(self):
        with MemoryStore(':memory:') as store:
            thoughts=ThoughtStore(store);ready=thoughts.publish('Rooms are reported ready.')
            exception=thoughts.publish('Rooms 214 and 216 are exceptions: ventilation is not checked.')
            thoughts.connect(ready.id, exception.id, 'The readiness report has these room exceptions.')
            requests=[]
            def model(request):
                requests.append(request)
                if request.task=='Navigate thought graph':
                    return json.dumps({'action':'focus','question':None,'quote':ready.text,
                                       'text':'Check the readiness report and its known exceptions.'})
                if request.task=='Check ready thought':return json.dumps({'verdict':'build','text':'A combined answer is needed.'})
                if request.task=='Construct thought':
                    support=next(fragment['option'] for row in request.records for fragment in row.get('fragments', [])
                                 if fragment['text']==ready.text)
                    return json.dumps({'thought':{'text':'All rooms are ready.','question':None,
                                                 'support_fragments':[support]},'needs_detail':None})
                self.assertEqual(request.task,'Verify thought')
                exception_fragment=next(fragment for row in request.records for fragment in row.get('fragments', [])
                                        if '214' in fragment['text'])
                self.assertFalse(exception_fragment['selected_for_grounding'])
                self.assertEqual(exception_fragment['selected_by'],[])
                draft=next(row for row in request.records if row['role']=='draft')
                self.assertNotIn(exception_fragment['option'],draft['support_fragments'])
                return json.dumps({'verdict':'reject','text':'The draft lost the room exceptions.'})
            with self.assertRaisesRegex(SemanticReviewRejected,'lost the room exceptions'):ThoughtEngine(thoughts,model).answer('Are all rooms ready?')
            self.assertEqual(len(thoughts.list_thoughts()),2)
            self.assertEqual([request.task for request in requests],
                ['Navigate thought graph','Check ready thought','Construct thought','Verify thought',
                 'Construct thought','Verify thought'])
