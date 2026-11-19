"""Automatic Desktop capture retains dialogue only; no model/network calls."""
import json
import unittest

from . import test_assertion_memory_capture as capture_fixture
from .test_assertion_memory_frontier import FakeRemote


class DialogueOnlyTest(unittest.TestCase):
    def setUp(self):
        self.fixture=capture_fixture.CaptureTest(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_visible_dialogue_only_acknowledges_contiguous_frontier(self):
        f=self.fixture
        f.turn('Actual user words')
        f.assistant('Private analysis','private','analysis')
        for kind in ('function_call','custom_tool_call','function_call_output','custom_tool_call_output','reasoning'):
            f.append({'type':'response_item','payload':{'type':kind,'call_id':'exec','name':'exec',
                'arguments':'DO NOT CAPTURE CALL','output':'DO NOT CAPTURE RESULT','summary':'PRIVATE REASONING'}})
        f.assistant('Visible progress','progress','commentary')
        f.assistant('Visible answer','answer','final')
        f.capture.register(f.store,f.raw);result=f.poll()
        self.assertEqual([m['arguments']['role'] for m in f.messages()],['user','assistant'])
        self.assertEqual([m['arguments']['text'] for m in f.messages()],['Actual user words','Visible answer'])
        self.assertEqual(result['gaps'],[])
        self.assertEqual(f.store.session('physical')['cursor'],f.path.stat().st_size)
        from synthetic_memory_adapter.frontier import Coordinator
        remote=FakeRemote(); coordinator=Coordinator(f.store,remote)
        coordinator.observe('physical',result); coordinator.tick()
        self.assertEqual(coordinator.local('physical')['scan_state'],'current')
        self.assertEqual(coordinator.local('physical')['server_acked_through'],2)
        self.assertEqual([row[0] for row in f.store.db.execute('SELECT seq FROM delivery_sequences ORDER BY seq')],[1,2])
        self.assertEqual(f.poll()['queued'],0)

    def test_excluded_large_tool_media_never_creates_artifacts_or_gaps(self):
        f=self.fixture;f.turn()
        for kind,content in [('function_call_output',{'output':'x'*(f.capture.MAX_RECORD+1)}),
            ('custom_tool_call_output',{'output':[{'type':'image','image_url':'data:image/png;base64,INVALID'}]}),
            ('message',{'role':'assistant','channel':'analysis','content':[{'type':'output_text','text':'x'*(f.capture.MAX_RECORD+1)}]})]:
            f.append({'type':'response_item','payload':{'type':kind,**content}})
        f.assistant('Visible answer')
        f.capture.register(f.store,f.raw)
        state=f.store.session('physical');state['gaps']=['unsupported_tool_media:external_image_not_fetched']
        with f.store.db:f.store.save_session(state)
        result=f.poll()
        self.assertEqual(f.store.session('physical')['exclusions']['prior_non_dialogue_tool_gap'],1)
        self.assertEqual([m['arguments']['role'] for m in f.messages()],['user','assistant'])
        self.assertEqual(result['gaps'],[])
        self.assertEqual(f.store.db.execute('SELECT count(*) FROM artifacts').fetchone()[0],0)
        self.assertEqual(f.store.session('physical')['exclusions']['non_dialogue_function_call_output'],1)

    def test_legacy_desktop_cannot_promote_private_phase_with_completion(self):
        from prototypes.assertion_memory.desktop import _assistant_context
        rows=[]
        for i,phase in enumerate(('analysis','reasoning','final_answer')):
            rows.append({'_memory_offset':i+1,'type':'response_item','payload':{'type':'message','role':'assistant',
                'phase':phase,'content':[{'type':'output_text','text':'matching completion'}]}})
        rows.append({'_memory_offset':4,'type':'event_msg','payload':{'type':'task_complete',
            'turn_id':'turn','last_agent_message':'matching completion'}})
        messages=_assistant_context({'turn_id':'turn','transcript_path':'/synthetic/session.jsonl'},rows,{'position':0})
        self.assertEqual([message['position'] for message in messages],[3])
