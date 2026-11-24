import io
import json
import tarfile
import unittest
from package_release import build,digest
from verify_native_receipt import validate

def profile(lane,letter):
    return {'lane':lane,'owner_only':True,'include_effects':True,'effects_timeout_ms':8000,
            'project_id_sha256':letter*64,'site_origin_sha256':digest(('origin-'+lane).encode()),
            'access_identity_sha256':digest(('acl-'+lane).encode())}
class ReleasePackage(unittest.TestCase):
    def setUp(self):
        self.tag='assertion-memory-sites-v0.1.0'
        self.src='9'*40
        self.code=b'export default 42;'
        self.profiles={'test':profile('test','a'),'prod':profile('prod','b')}
        self.bundle,self.manifest=build(self.tag,self.src,self.code,self.profiles)
    def test_deterministic_one_code_and_no_extra_config(self):
        self.assertEqual(build(self.tag,self.src,self.code,self.profiles),(self.bundle,self.manifest))
        with tarfile.open(fileobj=io.BytesIO(self.bundle),mode='r:gz') as tar:
            self.assertEqual(sorted(tar.getnames()),['manifest.json','profiles/prod.json','profiles/test.json','worker.mjs'])
            self.assertEqual(tar.extractfile('worker.mjs').read(),self.code)
            for lane,d in self.profiles.items():
                self.assertEqual(json.load(tar.extractfile(f'profiles/{lane}.json')),d)
        overlap=self.profiles|{'prod':self.profiles['prod']|{'project_id_sha256':'a'*64}}
        with self.assertRaises(ValueError):build(self.tag,self.src,self.code,overlap)
        leak=self.profiles|{'prod':self.profiles['prod']|{'credential':'not allowed'}}
        with self.assertRaises(ValueError):build(self.tag,self.src,self.code,leak)
    def test_receipt_binds_exact_release_and_preserved_owner_and_plugin(self):
        p=self.profiles['test']; h=digest(self.bundle)
        r={'tag':self.tag,'archive_sha256':h,'lane':'test','published_source_sha':'1'*40,
           'sites_deployment_id':'synthetic-deployment-id','sites_saved_version_id':'synthetic-version-id',
           'owner_only_before':True,'owner_only_after':True,'project_id_sha256':p['project_id_sha256'],
           'access_identity_sha256_before':p['access_identity_sha256'],'access_identity_sha256_after':p['access_identity_sha256'],
           'plugin_id_sha256_before':'4'*64,'plugin_id_sha256_after':'4'*64,'native_status':'succeeded'}
        result=validate(self.bundle,h,r)
        self.assertTrue(result['owner_only'])
        self.assertNotIn(r['sites_deployment_id'],str(result))
        for k,v in {'archive_sha256':'0'*64,'owner_only_after':False,'access_identity_sha256_after':'0'*64,
                    'plugin_id_sha256_after':'0'*64,'native_status':'pending','lane':'prod'}.items():
            with self.subTest(key=k),self.assertRaises(ValueError):
                validate(self.bundle,h,r|{k:v})
