"""Offline authorization tests: no capture data, database, worker, or credentials."""
import asyncio
import json
from pathlib import Path
from unittest import TestCase
from policy import EFFECTS, authorized, compile_catalog
from vm_relay import make_app

def catalogs():
    base=json.loads((Path(__file__).parent/'catalog.v011.review.json').read_text())
    x=base['catalog']
    authority={'revision':x['canonical_revision'],'schema_sha256':x['canonical_schema_hash'],
      'sdk':'1.30.0','module_sha256':'4eea4c6bf55be51fa972c54ee5fb0a259b5b5766acbd69676e1e6804566fa11c'}
    schemas={n:v['canonicalInputSchema'] for n,v in x['tools'].items()}
    source={'revision':authority['revision'],'module_sha256':authority['module_sha256'],
            'runtime_mcp_sdk':authority['sdk'],'tools':schemas}
    return base,compile_catalog(source,authority),compile_catalog(source,authority,include_test_effects=True)

class Effects(TestCase):
    def setUp(self):
        self.base,self.old,self.new=catalogs()
        self.profile={'lane':'test','base_release':'assertion-memory-v0.1.1',
                      'catalog_sha256':self.new['catalog_sha256'],'enabled_capabilities':['baseline_read','intake_process']}
        self.read='a'*64;self.effect='b'*64;self.call_count=0
    def not_called(self,*a):
        self.call_count+=1
        raise AssertionError('upstream must not be reached')

    def test_no_schema_migration_in_legacy_and_only_two_projections(self):
        self.assertEqual(self.base,self.old)
        t=self.new['catalog']['tools']
        self.assertEqual({n for n,v in t.items() if 'intake_process' in v['capability_projections']},EFFECTS)
        for n,v in t.items():
            self.assertEqual(v['canonicalInputSchema'],self.base['catalog']['tools'][n]['canonicalInputSchema'])
            self.assertEqual(authorized(self.profile,self.new,'test',n,'intake_process'),n in EFFECTS)
            self.assertFalse(authorized(self.profile,self.new,'prod',n,'intake_process'))
    def test_refuses_write_route_mismatches_without_upstream(self):
        app=make_app(self.profile,self.new,self.read,self.not_called,self.not_called,effect_token=self.effect)
        async def status(path,secret,tool):
            outputs=[]
            async def send(value):outputs.append(value)
            async def receive():return {'type':'http.request','body':json.dumps({
                'request_id':'aaaaaaaa-bbbb-4ccc-addd-eeeeeeeeeeee','operation':tool,'arguments':{}}).encode()}
            await app({'type':'http','method':'POST','path':path,'headers':[
                (b'content-type',b'application/json'),(b'authorization',('Bearer '+secret).encode())]},receive,send)
            return outputs[0]['status']
        async def test():
            self.assertEqual(await status('/memory-mcp-prod-effects',self.effect,'memory_process'),404)
            self.assertEqual(await status('/memory-mcp-test-effects',self.read,'memory_process'),401)
            self.assertEqual(await status('/memory-mcp-test-tools',self.effect,'memory_process'),401)
            self.assertEqual(await status('/memory-mcp-test-tools',self.read,'memory_process'),400)
            self.assertEqual(await status('/memory-mcp-test-effects',self.effect,'memory_query'),400)
            self.assertEqual(await status('/memory-mcp-test-effects',self.effect,'memory_capture_message'),400)
        asyncio.run(test())
        self.assertEqual(self.call_count,0)
    def test_prod_and_reused_credential_cannot_start(self):
        with self.assertRaises(ValueError):make_app(self.profile|{'lane':'prod'},self.new,self.read,
            self.not_called,self.not_called,effect_token=self.effect)
        with self.assertRaises(ValueError):make_app(self.profile,self.new,self.read,
            self.not_called,self.not_called,effect_token=self.read)
