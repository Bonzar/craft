import unittest
from plan import OWNER_STREAM_FLAG, PINNED_VALUES, PREFIX, TEST_UNIT, plan, prove

class Planner(unittest.TestCase):
    def setUp(self):
        self.args=[*PREFIX,*[x for kv in PINNED_VALUES.items() for x in kv],
            '--synthetic-fixture','--browser-owner-api','--queue-status-config',
            '/run/assertion-memory-dashboard/test/scheduler.json','--queue-status-stage','test']
        self.params=dict(lane='test',unit=TEST_UNIT,listener='127.0.0.1:8891',argv=self.args,
            expected_base='assertion-memory-v0.2.0',expected_visual='assertion-memory-visual-v0.2.0',
            effective_base='assertion-memory-v0.2.0',effective_visual='assertion-memory-visual-v0.2.0',
            expected_base_sha='a'*64,expected_visual_sha='b'*64,effective_base_sha='a'*64,effective_visual_sha='b'*64)
    def test_dark_default_and_exact_one_flag_only(self):
        self.assertEqual(plan(**self.params),self.args)
        self.assertEqual(plan(True,**self.params),self.args+[OWNER_STREAM_FLAG])
        self.assertNotIn(OWNER_STREAM_FLAG,self.args)
    def test_reject_other_lane_process_unsafe_or_unmatched_generation(self):
        for override in ({'lane':'prod'},{'unit':'memory-site-test-graph.service'},
                {'listener':'0.0.0.0:8891'},{'effective_base_sha':'c'*64},
                {'effective_visual':'assertion-memory-visual-v0.1.16'},
                {'argv':self.args+[OWNER_STREAM_FLAG]},
                {'argv':self.args+[ '--host','0.0.0.0']},
                {'argv':self.args[:-1]+['prod']},{'argv':self.args+[ '--browser-owner-clear']}):
            with self.subTest(override=override),self.assertRaises(ValueError):
                plan(True,**(self.params|override))
        with self.assertRaises(ValueError):
            plan('true',**self.params)

class Witness(unittest.TestCase):
    def test_auth_cache_unbuffered_separate_frames_and_later_change(self):
        r={k:True for k in ('anonymous_denied','same_origin','no_store','unbuffered','bootstrap',
                'queue_hints','epoch_matched','single_connection','disconnect_downgraded')}
        r.update(base='assertion-memory-v0.2.0',visual='assertion-memory-visual-v0.2.0',
                connecting_ms=12,request_ms=20,hello_reset_ms=95,live_ms=96,graph_dispatch_ms=96,
                queue_dispatch_ms=98,heartbeat_ms=1000,later_change_ms=2200)
        kw=dict(expected_base=r['base'],expected_visual=r['visual'])
        self.assertTrue(prove(response=r,**kw))
        for x in (dict(anonymous_denied=False),dict(no_store=False),dict(unbuffered=False),
                  dict(heartbeat_ms=95),dict(graph_dispatch_ms=94),dict(queue_dispatch_ms=90),
                  dict(later_change_ms=1000),dict(single_connection=False),
                  dict(epoch_matched=False),dict(base='assertion-memory-v0.1.36')):
            with self.subTest(x=x),self.assertRaises(ValueError):
                prove(response=r|x,**kw)

if __name__=='__main__':
    unittest.main()
