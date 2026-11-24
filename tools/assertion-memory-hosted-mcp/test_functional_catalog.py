"""Schema/transport tests, no database or model access."""
import asyncio
import json
from pathlib import Path
from unittest import TestCase
from policy import compile_catalog,digest
from vm_relay import make_app

DOC=json.loads((Path(__file__).parent/'post-search-source-contract.review.json').read_text())
def authority(d):
    return {'revision':d['revision'],'module_sha256':d['module_sha256'],
            'schema_sha256':digest(d['tools']),'sdk':d['runtime_mcp_sdk']}

class Functional(TestCase):
    def setUp(self):
        self.calls=[]
        self.read=compile_catalog(DOC,authority(DOC),require_functional=True)
        self.all=compile_catalog(DOC,authority(DOC),require_functional=True,include_effects=True)
    def deny(self,*args):
        self.calls.append(args)
        raise AssertionError('no upstream in denial tests')
    def test_canonical_shape_and_effect_gate(self):
        ts=self.all['catalog']['tools']
        self.assertEqual(len(ts),18)
        self.assertEqual({n for n,p in ts.items() if p['capability_projections'].get('generate')},{'memory_query'})
        self.assertEqual(ts['memory_search']['canonicalInputSchema'],DOC['tools']['memory_search'])
        self.assertEqual(set(ts['memory_search']['canonicalInputSchema']['properties']),{'query','scope','session_id','limit'})
        self.assertFalse(self.read['catalog']['tools']['memory_query']['capability_projections'])
        with self.assertRaises(ValueError):compile_catalog(DOC,authority(DOC))
    def test_query_is_not_allowed_by_the_read_or_ingest_only_profile(self):
        async def test():
            for lane in ('test','prod'):
                p={'lane':lane,'base_release':'assertion-memory-v0.1.1',
                    'catalog_sha256':self.all['catalog_sha256'],'read_catalog_sha256':self.read['catalog_sha256'],
                    'enabled_capabilities':['baseline_read','intake_process']}
                app=make_app(p,self.all,'a'*64,self.deny,self.deny,effect_token='b'*64)
                async def status(path,key,name,args):
                    outs=[]
                    async def out(data):outs.append(data)
                    async def inp():return {'type':'http.request','body':json.dumps({
                        'request_id':'aaaaaaaa-bbbb-4ccc-addd-eeeeeeeeeeee','operation':name,'arguments':args}).encode()}
                    await app({'type':'http','method':'POST','path':'/memory-mcp-'+lane+'-'+path,
                              'headers':[(b'content-type',b'application/json'),(b'authorization',('Bearer '+key).encode())]},inp,out)
                    return outs[0]['status']
                self.assertEqual(await status('tools','a'*64,'memory_query',{}),400)
                self.assertEqual(await status('effects','b'*64,'memory_query',{}),400)
                self.assertEqual(await status('effects','b'*64,'memory_search',{}),400)
        asyncio.run(test())
        self.assertFalse(self.calls)
