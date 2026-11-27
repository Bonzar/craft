from dataclasses import replace
import json
import unittest

import convergence as c
import public_read_contract as w

OLD = ('assertion-memory-v0.1.37', 'a'*40, 'b'*64)
NEW = ('assertion-memory-v0.1.38', 'c'*40, 'd'*64)


def read(when, release):
    return c.Observed(when, (w.URL, 200, 'application/json',
                     json.dumps(dict(stage='test',release_version=release[0],
                                     release_generation=w.identity(*release))).encode()))


def receipt(run,start,end,release):
    return c.Protected(run,1,c.WORKFLOW,c.BASE_ENV,start,end,
                       dict(status='ok',environment='test',revision=release[1],sha256=release[2]))


class ExternalProof(unittest.TestCase):
    def setUp(self):
        self.data = dict(predecessor=OLD,candidate=NEW,before=read(100,OLD),
                         forward=receipt('1001',110,120,NEW),forward_read=read(121,NEW),
                         inverse=receipt('1002',130,150,OLD),inverse_read=read(151,OLD))

    def prove(self, **changed):
        return c.prove(**(self.data|changed))

    def test_independent_reads_and_protected_redeploy_prove_only_external_convergence(self):
        result = self.prove()
        self.assertEqual(result['inverse'],'explicit_predecessor_redeploy')
        self.assertIs(result['automatic_receiver_rollback_proven'],False)
        self.assertIs(result['kernel_pid_proven'],False)

    def test_candidate_receipt_cannot_pass_with_old_cached_public_generation(self):
        with self.assertRaisesRegex(ValueError,'stale_or_foreign'):
            self.prove(forward_read=read(121,OLD))

    def test_redeploy_receipt_cannot_pass_while_gateway_still_serves_candidate(self):
        with self.assertRaisesRegex(ValueError,'stale_or_foreign'):
            self.prove(inverse_read=read(151,NEW))

    def test_wrong_restore_receipt_fails_even_when_public_looks_old(self):
        for value, error in ((None,'wrong_protected'), (receipt('1002',130,150,NEW),'wrong_exact'),
                             (replace(self.data['inverse'],environment='configured-release-base-prod'),'wrong_protected')):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError,error):
                self.prove(inverse=value)

    def test_duplicate_run_or_stale_or_misordered_samples_rejected(self):
        for changed in (dict(inverse=replace(self.data['inverse'],run='1001')),
                        dict(before=read(112,OLD)), dict(inverse_read=read(340,OLD)),
                        dict(forward_read=read(140,NEW))):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError,'unordered_or_stale'):
                self.prove(**changed)
