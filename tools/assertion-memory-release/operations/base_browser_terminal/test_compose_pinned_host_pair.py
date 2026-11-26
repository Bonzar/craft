from hashlib import sha256
from unittest import TestCase
from compose_pinned_host_pair import _transform_hook

class ExactInheritedBinding(TestCase):
    def fixture(self):
        lines=['\n']*334
        def line(n,s): lines[n-1]=s+'\n'
        line(154,'class Smoke:')
        line(155,'    def run(self):')
        line(165,'        native_mcp()')
        line(166,'        owner_graph()')
        line(309,'def check_live(policy, release, uid):')
        line(310,'    verify_original(')
        line(311,'        policy)')
        for n in range(312,316): line(n,'    common'+str(n)+' = 1')
        line(316,'    for i in (0,1):')
        line(317,'        meta = original_systemd(UNITS[i])')
        line(318,'        values = parse_meta(meta)')
        line(319,'        trusted_unit_fragment(UNITS[i], values)')
        line(320,'        assert values')
        line(321,'        pid = 123')
        line(329,'        assert pid')
        line(332,'    return tuple(proof)')
        return ''.join(lines).encode()
    def test_preserves_original_native_checks(self):
        old=self.fixture()
        result=_transform_hook(old,sha256(old).hexdigest())
        compile(result,'fixture','exec')
        self.assertEqual(result.count(b'meta = original_systemd(UNITS[i])'),2)
        self.assertEqual(result.count(b'trusted_unit_fragment(UNITS[i], values)'),2)
        self.assertLess(result.index(b'common315 = 1'),result.index(b'def _base_native_pid(unit)'))
        self.assertLess(result.index(b'native_mcp()'),result.index(b'if _base_browser_sidecar_enabled()'))
        self.assertLess(result.index(b'if _base_browser_sidecar_enabled()'),result.index(b'owner_graph()'))
        drift=old.replace(b'trusted_unit_fragment(UNITS[i], values)',b'values = None')
        with self.assertRaises(ValueError):
            _transform_hook(drift,sha256(drift).hexdigest())
