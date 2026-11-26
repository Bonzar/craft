from unittest import TestCase
from neutral_edge_plan import derive,digest
from neutral_edge_apply import TARGET,exact_change,expected_loaded
from test_neutral_edge_plan import SAMPLE

class TestEdgeTransaction(TestCase):
    def setUp(self):
        self.old=SAMPLE
        self.new=derive(self.old,expected_sha256=digest(self.old),registered_clients=0,
            gateway_paths_absent=True,alternate_forwards_absent=True)['candidate']
        self.dyn=self.old
        self.other=b'unchanged unrelated PROD topology'
        self.log=[]
        self.original=self.assembled()
        self.next=expected_loaded(self.old,self.new,self.original)
    def assembled(self):
        return b'# configuration file '+str(TARGET).encode()+b':\n'+self.dyn+self.other
    def change(self,body):
        self.dyn=body;self.log.append('write')
    def invoke(self,validate=lambda:None):
        def reload():
            self.log.append('reload')
        return exact_change(self.old,self.new,self.original,self.next,lambda:self.dyn,
                            self.change,self.assembled,validate,reload)
    def test_live_full_bytes_change_only_one_exact_test_location(self):
        self.invoke()
        self.assertEqual(self.dyn,self.new)
        self.assertEqual(self.assembled(),self.next)
        self.assertEqual(self.log,['write','reload'])
    def test_unrelated_drift_refuses_before_touching_target(self):
        self.other+=b'xx'
        with self.assertRaises(ValueError):self.invoke()
        self.assertEqual(self.log,[])
    def test_candidate_failure_restores_exact_baseline(self):
        calls=[]
        def validate():
            calls.append(1)
            if len(calls)==1:raise ValueError('synthetic nginx -t failed')
        with self.assertRaises(ValueError):self.invoke(validate)
        self.assertEqual(self.dyn,self.old)
        self.assertEqual(self.log,['write','write','reload'])
    def test_rollback_does_not_reload_unrelated_changed_ingress(self):
        def validate():
            self.other+=b'concurrent drift'
            raise ValueError('synthetic nginx -t failed')
        with self.assertRaises(RuntimeError):self.invoke(validate)
        self.assertEqual(self.dyn,self.old)
        self.assertEqual(self.log,['write','write'])
    def test_loader_requires_unique_exact_target_bytes(self):
        with self.assertRaises(ValueError):
            expected_loaded(self.old,self.new,self.original+self.original)


    def test_configuration_changing_during_nginx_t_never_reloads(self):
        def drift():
            self.other+=b'during normal nginx -t'
        with self.assertRaises(RuntimeError):
            self.invoke(drift)
        self.assertEqual(self.dyn,self.old)
        self.assertNotIn('reload',self.log)
