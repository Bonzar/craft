import unittest
from copy import deepcopy
from gateway_test_apply import compute,fingerprint,require_exact
from test_render_test import W

class GatewayOperator(unittest.TestCase):
    def test_rollback_only_accepts_the_reviewed_test_route(self):
        wanted=compute(W,False)
        self.assertEqual(compute(wanted,True),W)
        drift=deepcopy(wanted)
        drift['paths']['/api/v1/events']['get']['x-yc-apigateway-integration']['timeouts']['read']=100
        with self.assertRaisesRegex(ValueError,'differs'):
            compute(drift,True)

    def test_preflight_digest_and_no_stale_overwrite(self):
        wanted=compute(W,False); calls=[]
        with self.assertRaisesRegex(ValueError,'drifted'):
            require_exact(W,wanted,fingerprint(W),fingerprint(wanted),lambda:{},calls.append)
        self.assertEqual(calls,[])

    def test_readback_failure_never_do_a_second_write(self):
        wanted=compute(W,False); calls=[]
        snapshots=iter([W,{}])
        with self.assertRaisesRegex(ValueError,'reconcile'):
            require_exact(W,wanted,fingerprint(W),fingerprint(wanted),lambda:next(snapshots),calls.append)
        self.assertEqual(calls,[wanted])
