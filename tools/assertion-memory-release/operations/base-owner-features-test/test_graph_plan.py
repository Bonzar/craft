"""Fictional receipts only; no filesystem capability or host calls."""
import unittest
from dataclasses import replace

import graph_plan as p


class Tests(unittest.TestCase):
    def fixture(self):
        uid = 991
        rev, archive, manifest, chain = 'a' * 40, 'b' * 64, 'c' * 64, 'd' * 64
        current = f'{p.ROOT}/releases/{rev}-{archive}'
        argv = [*p.ARGV, *sum(([k, v] for k, v in p.VALUES.items()), []),
                '--test-direct-events-origin', 'https://8.8.8.8', *sorted(p.SWITCHES)]
        data = dict(stage='test', account=p.ACCOUNT, unit=p.UNIT, uid=uid, effective_uid=uid, pid=10,
                    listener='127.0.0.1:8891', private_base_listener='127.0.0.1:8893',
                    cgroup=f'/user.slice/user-{uid}.slice/user@{uid}.service/app.slice/{p.UNIT}',
                    credential_directory=f'/run/user/{uid}/credentials/{p.UNIT}',
                    credential_names=list(p.CREDENTIALS), credential_scope_ok=True, base_owner_policy_matches=True,
                    current=current, cwd=current, install_chain_sha256=chain, visual_manifest_sha256=manifest,
                    verified_lane_artifacts=True, generic_owner_carrier=True, preflight_smoke_ok=True,
                    baseline_dropins={'zzzzzz-base-browser-owner-api-v1.conf': 'e' * 64},
                    dropin_absent=True, argv=argv)
        return data, p.Pins(rev, archive, manifest, p.digest(p.canonical(data)), chain, uid)

    def reseal(self, data, pins):
        return replace(pins, evidence_sha256=p.digest(p.canonical(data)))

    def test_only_one_flag_and_exact_inverse(self):
        data, pins = self.fixture()
        c = p.plan(data, pins)
        self.assertEqual(c.destination, p.DESTINATION)
        self.assertTrue(c.rendered.endswith(('ExecStart=' + ' '.join([*data['argv'], p.FLAGS]) + '\n').encode()))
        for token in ('LoadCredential', 'Environment', 'clear', 'prod', 'device'):
            self.assertNotIn(token, c.rendered.decode())
        inverse = self.invert(c, data)
        self.assertEqual((inverse['unit'], inverse['path'], inverse['expected_argv']),
                         (p.UNIT, p.DESTINATION, tuple(data['argv'])))

    def invert(self, c, data, **updates):
        kw = dict(actual_sha256=c.candidate_sha256, current=data['current'],
                  baseline_dropins=data['baseline_dropins'], install_chain_sha256=data['install_chain_sha256'])
        return p.inverse(c, **(kw | updates))

    def test_drift_in_inverse_is_never_overwritten(self):
        data, pins = self.fixture()
        c = p.plan(data, pins)
        for change in ({'actual_sha256': '0' * 64}, {'current': '/wrong'},
                       {'baseline_dropins': {}}, {'install_chain_sha256': '0' * 64}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.invert(c, data, **change)

    def test_changed_receipt_needs_separate_pin(self):
        data, pins = self.fixture()
        with self.assertRaisesRegex(ValueError, 'trusted_evidence_drift'):
            p.plan(data | {'pid': 22}, pins)

    def test_wrong_authority_fails_even_when_resealed(self):
        data, pins = self.fixture()
        cases = [{'stage': 'prod'}, {'effective_uid': 0}, {'cgroup': '/system.slice/' + p.UNIT},
                 {'private_base_listener': '127.0.0.1:8903'}, {'credential_directory': '/run/credentials/' + p.UNIT},
                 {'credential_names': []}, {'base_owner_policy_matches': False}, {'credential_scope_ok': 1},
                 {'dropin_absent': False}, {'generic_owner_carrier': False}, {'preflight_smoke_ok': False},
                 {'verified_lane_artifacts': 1}, {'visual_manifest_sha256': '0' * 64},
                 {'baseline_dropins': {'zzzzzzz-other.conf': '6' * 64}},
                 {'argv': [*data['argv'], '--browser-owner-clear']}, {'argv': [*data['argv'], p.FLAGS]},
                 {'argv': [*data['argv'], '--embedding-cache', '/etc/secret']},
                 {'argv': [*data['argv'], '--provider-id', 'bad\nX=Y']}]
        for change in cases:
            row = data | change
            with self.subTest(field=next(iter(change))), self.assertRaises(ValueError):
                p.plan(row, self.reseal(row, pins))


if __name__ == '__main__':
    unittest.main()
