from dataclasses import replace
from hashlib import sha256
from unittest import TestCase
from build_releaser import asset
from qualify_release import Published, Selection, entrypoint, qualify


class ImmutablePackage(TestCase):
    def setUp(self):
        raw=asset()
        self.p=Published('assertion-releaser-v1.0.0','a'*40,True,'assertion-releaser.tar.gz',raw)
        self.s=Selection('test','apply',self.p.tag,self.p.ref_commit,sha256(raw).hexdigest())

    def test_same_asset_both_lanes_and_two_deterministic_builds(self):
        self.assertEqual(self.p.asset,asset())
        self.assertEqual(qualify(self.s,self.p),qualify(replace(self.s,lane='prod',operation='rollback'),self.p))
        for publication in (replace(self.p,immutable=False),replace(self.p,ref_commit='b'*40),
                            replace(self.p,asset=self.p.asset+b'change')):
            with self.assertRaises(ValueError):
                qualify(self.s,publication)

    def test_missing_actuator_fails_closed_even_for_verified_digest(self):
        with self.assertRaisesRegex(ValueError,'no installed'):
            entrypoint(self.s,self.p,None,configured_lane='test',verify=None,io=None)
        with self.assertRaisesRegex(ValueError,'no installed'):
            entrypoint(replace(self.s,lane='prod'),self.p,None,
                       configured_lane='test',verify=lambda *_:True,io=object())
