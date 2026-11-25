from copy import deepcopy
from datetime import datetime, timezone
import unittest

from verify_test_pause_gate import validate_external, release_generation


from package_release import build, digest, wire
from prepare_test_pause import prepare, ANCHOR, ENTRY


class RefreshExistingTest(unittest.TestCase):
    def test_exact_owner_wrapper_can_refresh_modules_without_editing_entry(self):
        site='https://fixture.owner.chatgpt.site'
        gw='https://fixture.owner.apigw.yandexcloud.net/memory-visual-test-pause'
        proj='synthetic-test-project'
        acl='a'*64
        tag='assertion-memory-v1.2.3'
        sha='f'*40
        test=dict(lane='test',owner_only=True,include_effects=True,effects_timeout_ms=1234,
            project_id_sha256=digest(proj.encode()),site_origin_sha256=digest(site.encode()),
            access_identity_sha256=acl)
        prod={**test,'lane':'prod','project_id_sha256':'b'*64,'site_origin_sha256':'c'*64}
        files={'native-pause.mjs':b'exact module','v25-bind.mjs':b'exact binding'}
        raw,_=build(tag,sha,b'worker',{'test':test,'prod':prod},test_pause=files)
        def stage(entry,refresh=False,**kwargs):
            return prepare(raw,archive_sha256=digest(raw),tag=tag,release_sha=sha,
                 observed_acl_sha256=kwargs.get('acl',acl),observed_entry_sha256=digest(entry),
                 hosting_bytes=wire({'project_id':proj}),entry_bytes=entry,origin=site,
                 gateway=kwargs.get('gateway',gw),refresh_existing=refresh)
        entry=stage(ANCHOR)[ENTRY]
        self.assertEqual(stage(entry,True),files)
        for edited in (ANCHOR,entry+entry,entry.replace(b'matchesEnrolledOwner,',b'otherOwner,')):
            with self.assertRaises(ValueError): stage(edited,True)
        with self.assertRaises(ValueError): stage(entry,True,acl='d'*64)
        with self.assertRaises(ValueError): stage(entry,True,gateway=gw.replace('fixture','other'))


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
