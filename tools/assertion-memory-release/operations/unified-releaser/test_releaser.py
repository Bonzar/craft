from contextlib import contextmanager
from dataclasses import replace
from unittest import TestCase
from releaser import LANES, TEST_BASELINE, Receipt, operate

def proof(key='test',**kw):
    lane=LANES[key]
    return replace(Receipt(key,lane.environment,lane.operator,'apply','assertion-releaser-v1.0.0',
        'a'*40,'b'*64,TEST_BASELINE if key=='test' else 'e'*64,'f'*64,
        TEST_BASELINE if key=='test' else 'e'*64,'c'*64,'d'*64),**kw)


class Adapter:
    def __init__(self,p):
        self.value=(p.before,None)
        self.calls=[]
        self.nonces={}
        self.done={}
        self.held=False
    @contextmanager
    def lock(self,lane):
        assert not self.held
        self.held=True
        try: yield
        finally: self.held=False
    def assert_current(self,lane,r):
        assert self.held and r.environment==lane.environment
    def observe(self,lane):
        assert self.held
        return self.value
    def reserve(self,lane,r):
        if r.nonce in self.nonces:
            raise ValueError('replay')
        self.nonces[r.nonce]=r
    def settled_forward(self,lane,nonce):
        return self.done.get(nonce)
    def stage_adapter(self,lane,digest):
        self.calls.append('stage_adapter')
        self.value=(self.value[0],digest)
    def swap(self,lane,before,after):
        assert self.value[0]==before
        self.calls.append('swap')
        self.value=(after,self.value[1])
    def remove_exact_adapter(self,lane,digest):
        assert self.value[1]==digest
        self.calls.append('remove_adapter')
        self.value=(self.value[0],None)
    def finish(self,lane,r,target):
        assert self.value==target
        self.done[r.nonce]=r


def run(key,r,a,configured=None,verify=lambda *_:True):
    return operate(key,r.action,r,configured=configured or key,verify=verify,
                   installed_artifact='b'*64,io=a)


class Releaser(TestCase):
    def test_same_package_two_isolated_lanes_forward_exact_inverse_replay(self):
        for key in LANES:
            with self.subTest(lane=key):
                p=proof(key); a=Adapter(p)
                self.assertEqual(run(key,p,a),(p.after,p.adapter))
                inv=replace(p,action='inverse',nonce='9'*64,forward_receipt=p.nonce)
                self.assertEqual(run(key,inv,a),(p.before,None))
                with self.assertRaisesRegex(ValueError,'replay'):
                    run(key,p,a)
                self.assertEqual(a.calls,['stage_adapter','swap','swap','remove_adapter'])

    def test_wrong_lane_environment_signature_and_package_rejected_before_effect(self):
        p=proof(); a=Adapter(p)
        for key,r,k,valid in (('prod',p,'test',True),('test',proof('prod'),'test',True),
                               ('test',replace(p,environment=LANES['prod'].environment),'test',True),
                               ('test',replace(p,artifact='0'*64),'test',True),
                               ('test',p,'test',False)):
            with self.assertRaises(ValueError):
                run(key,r,a,configured=k,verify=lambda *_:valid)
        self.assertEqual(a.calls,[])

    def test_inverse_requires_settled_exact_prior_and_pinned_baseline(self):
        p=proof(); a=Adapter(p); run('test',p,a)
        inv=replace(p,action='inverse',nonce='9'*64,forward_receipt=p.nonce)
        for r in (replace(inv,forward_receipt='0'*64),
                  replace(inv,tag='assertion-releaser-v2.0.0'),
                  replace(inv,inverse='1'*64)):
            with self.assertRaises(ValueError):
                run('test',r,a)
        self.assertEqual(a.value,(p.after,p.adapter))

    def test_interrupted_apply_does_not_retry_or_infer_success(self):
        p=proof(); a=Adapter(p)
        def fail(*_): raise RuntimeError('ambiguous')
        a.swap=fail
        with self.assertRaises(RuntimeError):
            run('test',p,a)
        self.assertNotIn(p.nonce,a.done)
        with self.assertRaisesRegex(ValueError,'drift'):
            run('test',p,a)
