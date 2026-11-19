"""Goal continuations inherit only a paired human root in the same native file."""
import unittest
from . import test_assertion_memory_capture as fixtures


class GoalCaptureTest(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.CaptureTest(methodName='runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)

    def goal(self,turn='goal1',parent='one',root='one',agent=None):
        self.f.append({'type':'event_msg','payload':{'type':'task_started','turn_id':turn,'root_turn_id':root,
            'turn_attribution':{'turn_trigger':'goal','parent_turn_id':parent,'initiating_agent_path':agent,'root_turn_id':root}}})
        self.f.append({'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'HARNESS GOAL CONTEXT'}],
            'internal_chat_message_metadata_passthrough':{'turn_id':turn,'content_item_kinds':['goal.internal_context'],
                'content_item_metadata':[{'provenance':{'type':'harness','hook':False}}]}}})

    def test_proven_goal_ancestry_survives_compaction_and_keeps_visible_dialogue(self):
        f=self.f;f.turn('Actual root request',trigger='remote_ios');self.goal();f.assistant('Visible goal progress','a1','commentary')
        self.goal('goal2','goal1');f.append({'type':'compacted','payload':{'summary':'x'*(f.capture.MAX_RECORD+1)}})
        f.pair('Actual steering words','goal2','steering');f.assistant('Visible goal answer','a2','final')
        f.capture.register(f.store,f.raw);result=f.poll()
        self.assertEqual([m['arguments']['text'] for m in f.messages()],['Actual root request','Actual steering words','Visible goal answer'])
        self.assertEqual(result['gaps'],[])
        self.assertEqual(f.store.session('physical')['dialogue_turn_roots'],{'one':'one','goal1':'one','goal2':'one'})
        self.assertEqual(f.store.session('physical')['exclusions']['harness_compaction'],1)
        self.assertEqual(f.store.session('physical')['exclusions']['harness_service_context'],2)

    def test_unproven_or_delegated_goal_never_inherits_human_permission(self):
        f=self.f;self.goal();f.assistant('Not proven','a0')
        f.turn('Actual root',trigger='user');self.goal('delegated','one',agent='/root/child');f.assistant('Delegated response','a1')
        self.goal('wrong-root','one',root='other');f.assistant('Wrong ancestry','a2')
        f.capture.register(f.store,f.raw);result=f.poll()
        self.assertEqual([m['arguments']['text'] for m in f.messages()],['Actual root'])

    def test_acknowledged_human_boundary_replay_recovers_goal_without_duplicate_receipts(self):
        from synthetic_memory_adapter.frontier import Coordinator
        from tests.test_assertion_memory_frontier import FakeRemote
        from synthetic_memory_adapter.repair import replay_verified_turn
        from unittest.mock import patch
        f=self.f;f.turn('Actual root request',trigger='remote_ios')
        f.capture.register(f.store,f.raw);first=f.poll()
        coordinator=Coordinator(f.store,FakeRemote());coordinator.observe('physical',first);coordinator.tick()
        before=f.messages();self.goal();f.assistant('Visible recovered answer')
        allowed=f.capture._allowed_start
        with patch.object(f.capture,'_allowed_start',side_effect=lambda payload,meta,roots=None:allowed(payload,meta)):
            f.poll()
        self.assertEqual(f.messages(),before)
        state=f.store.session('physical')
        replay_verified_turn(f.store,'physical',turn_id='one',expected_header_sha256=state['header_hash'])
        result=f.poll();coordinator.observe('physical',result);coordinator.tick()
        self.assertEqual([m['arguments']['text'] for m in f.messages()],['Actual root request','Visible recovered answer'])
        self.assertEqual(f.messages()[0]['receipt'],before[0]['receipt'])
        self.assertEqual(coordinator.local('physical')['server_acked_through'],2)
        self.assertEqual(result['gaps'],[])


if __name__=='__main__':unittest.main()
