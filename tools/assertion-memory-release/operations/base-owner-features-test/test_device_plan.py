import unittest
from dataclasses import replace

import device_plan as p


class DeviceTestPlan(unittest.TestCase):
    def profile(self):
        uid, rev, tar, visual, chain = 994, 'a'*40, 'b'*64, 'c'*64, 'd'*64
        cur = p.ROOT + '/releases/' + rev + '-' + tar
        e = dict(stage='test', account=p.ACCOUNT, uid=uid, graph_unit=p.GRAPH, mcp_unit=p.MCP,
                 graph_listener='127.0.0.1:8891', mcp_listener='127.0.0.1:8893',
                 current=cur, graph_cwd=cur, mcp_cwd=cur, base_archive=tar, visual_manifest=visual,
                 chain_sha256=chain, config_path=p.CONFIG, token_selector=p.TOKEN, selected_database=p.DATABASE,
                 token_env_selector_absent=True, existing_authority_verified=True, same_namespaces=True,
                 registry_parent_owner=uid, registry_parent_mode=0o700, registry_parent_safe=True,
                 registry_state='absent', registry_identity=None, marker_absent=True,
                 marker_parent_safe=True, generic_owner_carrier=True)
        return e,p.Pins(rev,tar,visual,chain,p.seal(e),uid)

    def test_absent_and_exact_ready_store_are_different(self):
        e,pins=self.profile()
        c=p.plan(e,pins)
        self.assertEqual(c.setup,'initialize_private_registry_once_while_dark')
        self.assertEqual(c.fixed_test_units,(p.MCP,p.GRAPH))
        self.assertEqual(c.registry,p.REGISTRY)
        e.update(registry_state='private_schema_ready',registry_identity='9'*64)
        c=p.plan(e,replace(pins,evidence=p.seal(e)))
        self.assertEqual(c.setup,'reuse_exact_private_registry_without_mutation')

    def test_resealed_drift_rejected(self):
        e,pins=self.profile()
        for change in ({'stage':'prod'},{'token_env_selector_absent':False},
                       {'token_selector':'/other'},{'same_namespaces':1},
                       {'registry_parent_owner':0},{'registry_parent_mode':0o755},
                       {'registry_state':'private_schema_ready'},{'marker_absent':False},
                       {'mcp_unit':'other.service'},{'generic_owner_carrier':False}):
            broken=e|change
            with self.subTest(change=change),self.assertRaises(ValueError):
                p.plan(broken,replace(pins,evidence=p.seal(broken)))

    def test_inverse_never_removes_registry(self):
        e,pins=self.profile()
        c=p.plan(e,pins)
        i=p.inverse(c,marker_sha256=c.marker_sha256,current=c.current,chain_sha256=pins.chain,
                    registry_identity='9'*64,activation_registry_identity='9'*64)
        self.assertTrue(i['keep_private_registry'])
        with self.assertRaisesRegex(ValueError,'rollback_drift'):
            p.inverse(c,marker_sha256=c.marker_sha256,current=c.current,chain_sha256=pins.chain,
                      registry_identity='0'*64,activation_registry_identity='9'*64)
