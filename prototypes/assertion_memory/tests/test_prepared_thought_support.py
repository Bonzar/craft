"""Models select prepared support; source addresses and exact bytes stay in code."""
import json
import unittest
from datetime import datetime,timezone
from unittest.mock import patch
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_selection import ThoughtSelection
from prototypes.assertion_memory.thought_support import prepare_support

ACCEPT={'verdict':'accept','text':'Selected support retains the meaning and conditions.'}

def options(request):
    return [(fragment,record) for record in request.records for fragment in record.get('fragments',[])]

def result(text,selections):
    return json.dumps({'thoughts':[{'text':text,'question':None,'support_fragments':selections}],'needs_detail':None})


class PreparedThoughtSupportTest(unittest.TestCase):
    def setup_memory(self):
        memory=MemoryStore(':memory:');self.addCleanup(memory.close)
        return memory,ThoughtStore(memory),CaptureQueue(memory)

    def test_raw_json_unicode_whitespace_round_trip_and_python_character_addresses(self):
        memory,store,queue=self.setup_memory()
        text=' \n{"answer":"Да\\nтолько при одобрении 👩‍💻", "note":"quote: \\\"ok\\\""}\nСледующее условие.  '
        raw=queue.capture_event('s','json',text,source='fixture')
        prepared=prepare_support([{'role':'source','text':text}],[{'event_id':raw.id}])
        self.assertEqual(''.join(item.text for item in prepared.fragments),text)
        self.assertEqual(''.join(row['text'] for row in prepared.wire[0]['fragments']),text)
        selected=next(item for item in prepared.fragments if 'одобрении' in item.text)
        def model(request):
            if request.task=='Construct thought':
                self.assertNotIn('text',request.records[0])
                return result('Только при одобрении.',[selected.option])
            return json.dumps(ACCEPT)
        thought=ThoughtEngine(store,model).ingest(raw,consolidate=False)[0]
        basis=thought.bases[0]
        self.assertEqual(text[basis['span']['start']:basis['span']['end']],selected.text)
        self.assertEqual(basis['meaning'],selected.text)
        self.assertTrue(basis['content_hash'])
        wrapped=queue.capture_event('s','window','PREFIX'+text+'TAIL',source='fixture')
        window=prepare_support([{'role':'source','text':text}],
            [{'event_id':wrapped.id,'span':{'start':6,'end':6+len(text)}}])
        _,pins=window.resolve([selected.option])
        self.assertEqual(wrapped.prompt[pins[0]['span']['start']:pins[0]['span']['end']],selected.text)

    def test_identical_text_selects_its_record_and_historical_version_without_disambiguation_model(self):
        memory,store,queue=self.setup_memory()
        with patch('prototypes.assertion_memory.thought_store.datetime',wraps=datetime) as clock:
            clock.now.return_value=datetime(2026,1,1,tzinfo=timezone.utc)
            first=store.publish('Approval is required.',origin={'actor':'user','scope':'home'})
            selected=store.publish(first.text,origin={'actor':'assistant','scope':'home'})
            clock.now.return_value=datetime(2026,1,3,tzinfo=timezone.utc)
            store.publish('A later changed interpretation.',thought_id=selected.id,expected_version=1,origin=selected.origin)
        selection=ThoughtSelection(store,scope='home',recorded_as_of='2026-01-02')
        calls=[]
        def model(request):
            calls.append(request.task)
            if request.task=='Construct thought':
                choice=next(f['option'] for f,row in options(request) if row.get('origin',{}).get('actor')=='assistant')
                return result('The assistant reported that approval is required.',[choice])
            return json.dumps(ACCEPT)
        engine=ThoughtEngine(selection,model)
        thought=engine._construct([{'role':'material',**engine._content(row)} for row in (first,selected)],
            [engine._basis(row) for row in (first,selected)])[0]
        self.assertEqual([(b['thought_id'],b['version']) for b in thought.bases],[(selected.id,1)])
        self.assertEqual(calls,['Construct thought','Verify thought'])

    def test_short_confirmation_selects_primary_and_antecedent_without_unrelated_context(self):
        memory,store,queue=self.setup_memory()
        context=queue.capture_event('s','proposal','Choose lunch on weekends.\nUnrelated portraits decision.',source='fixture',actor='assistant')
        raw=queue.capture_event('s','yes','Да',source='fixture')
        def model(request):
            if request.task=='Construct thought':
                picked=[f['option'] for f,row in options(request) if f['text'].strip()=='Да' or 'Choose lunch' in f['text']]
                return result('The user agreed to lunch on weekends.',picked)
            source=next(row for row in request.records if row.get('role')=='source')
            self.assertTrue(all(f['selected_for_grounding'] for f in source['fragments']))
            return json.dumps(ACCEPT)
        thought=ThoughtEngine(store,model).ingest(raw,[context],consolidate=False)[0]
        self.assertEqual(len(thought.bases),2)
        self.assertEqual({b['event_id'] for b in thought.bases},{raw.id,context.id})
        context_basis=next(b for b in thought.bases if b['event_id']==context.id)
        self.assertNotIn('portraits',context.prompt[context_basis['span']['start']:context_basis['span']['end']])

    def test_two_selected_spans_exclude_middle_and_insufficient_support_still_requires_semantic_repair(self):
        memory,store,queue=self.setup_memory()
        base=store.publish('Approval is required.\nUNSELECTED MIDDLE.\nEvacuation is the exception.')
        calls=[]
        def model(request):
            calls.append(request.task)
            if request.task=='Construct thought':
                entries=options(request)
                picked=[f['option'] for f,_ in entries if 'Approval' in f['text'] or
                    (calls.count('Construct thought')>1 and 'Evacuation' in f['text'])]
                return result('Approval is required except during evacuation.',picked)
            fragments=[f for row in request.records for f in row.get('fragments',[])]
            selected=[f['text'] for f in fragments if f['selected_for_grounding']]
            return json.dumps(ACCEPT if any('Evacuation' in text for text in selected) else
                {'verdict':'reject','text':'Select the evacuation exception as support too.'})
        engine=ThoughtEngine(store,model)
        thought=engine._construct([{'role':'material',**engine._content(base)}],[engine._basis(base)])[0]
        self.assertEqual(len(thought.bases),2)
        self.assertLess(thought.bases[0]['span']['end'],thought.bases[1]['span']['start'])
        self.assertFalse(any('MIDDLE' in b['meaning'] for b in thought.bases))
        self.assertEqual(calls,['Construct thought','Verify thought']*2)

    def test_unprovided_positions_are_rejected_and_fragment_count_has_no_fixed_ceiling(self):
        memory,store,queue=self.setup_memory()
        base=store.publish(''.join(f'Condition {index}.\n' for index in range(175)))
        calls=[]
        def model(request):
            calls.append(request.task)
            if request.task=='Construct thought':
                choices=options(request)
                self.assertEqual(len(choices),175)
                selection_schema=request.response_schema['properties']['thoughts']['items']['properties']['support_fragments']
                self.assertNotIn('uniqueItems',selection_schema)
                spec=selection_schema['items']
                self.assertEqual(spec,{'type':'integer','minimum':0,'maximum':174})
                return result('Condition 174.',[999] if calls.count('Construct thought')==1 else [174,174])
            return json.dumps(ACCEPT)
        engine=ThoughtEngine(store,model)
        thought=engine._construct([{'role':'material',**engine._content(base)}],[engine._basis(base)],question='Last condition?')[0]
        self.assertEqual(len(thought.bases),1)
        self.assertEqual(thought.bases[0]['meaning'],'Condition 174.\n')
        self.assertEqual(calls,['Construct thought','Construct thought','Verify thought'])


if __name__=='__main__':unittest.main()
