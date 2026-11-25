from copy import deepcopy
from datetime import datetime, timezone
import unittest

from verify_test_pause_gate import validate_external, release_generation


class GateTest(unittest.TestCase):
    def test_live_owner_proof_must_bind_exact_releases_and_restore(self):
        base=dict(revision='1'*40,payload_sha256='2'*64,tag='assertion-memory-v1.0.1',run_id='3')
        visual=dict(manifest_sha256='4'*64,tag='assertion-memory-visual-v1.0.1',run_id='5')
        profile=dict(project_id_sha256='6'*64,access_identity_sha256='7'*64)
        now=datetime(2026,11,25,0,0,1,tzinfo=timezone.utc)
        def state(paused,rev,put):
            return dict(prepare=200,put=put,readback=200,paused=paused,revision=rev*32)
        good=dict(source_sha='a'*40,deployment_sha256='b'*64,saved_version_sha256='c'*64,
                  project_id_sha256='6'*64,acl_before_sha256='7'*64,acl_after_sha256='7'*64,
                  plugin_before_sha256='d'*64,plugin_after_sha256='d'*64,archive_sha256='e'*64,
                  base_generation=release_generation(base['revision'],base['payload_sha256'],base['tag'],'test'),
                  visual_digest='4'*64,stage='test',observed_at='2026-11-25T00:00:00Z',
                  smoke=dict(initial=state(False,'1',None),changed=state(True,'2',200),
                             restored=state(False,'3',200),denied=dict(no_owner=403,bad_csrf=403)))
        validates=lambda value:validate_external(value,profile,base,visual,'e'*64,now=now)
        self.assertEqual(validates(good)['base_test_run_id'],'3')
        for k,v in {'stage':'prod','archive_sha256':'a'*64,'base_generation':'a'*64,
                    'visual_digest':'a'*64,'acl_after_sha256':'a'*64,'plugin_after_sha256':'a'*64,
                    'observed_at':'2026-11-24T00:00:00Z','smoke':{}}.items():
            with self.subTest(k=k),self.assertRaises(ValueError):
                validates(good|{k:v})
        for section,k,v in [('initial','prepare',404),('changed','put',202),('restored','revision','2'*32),
                            ('restored','paused',True),('denied','bad_csrf',200)]:
            value=deepcopy(good)
            value['smoke'][section][k]=v
            with self.subTest(section=section,k=k),self.assertRaises(ValueError):
                validates(value)
