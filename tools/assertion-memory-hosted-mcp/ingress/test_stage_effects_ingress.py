import hashlib
import json
import unittest
from stage_nginx_pair import insert_location
from stage_nginx_test_effects import plan
from stage_test_effects_gateway import candidate,parse,INTEGRATION,OLD,NEW,AUTH
BASE=b'''server {
    location = /memory-mcp-test {
        proxy_pass http://127.0.0.1:8893;
    }
    location = /graph { proxy_pass http://127.0.0.1:8903; }
    location = /mcp-prod { proxy_pass http://127.0.0.1:8918; }
}
'''
class EffectsIngress(unittest.TestCase):
    def test_nginx_only_adds_effects(self):
        old,_=insert_location(BASE)
        proof=hashlib.sha256(old).hexdigest()
        new,frag=plan(old,proof)
        self.assertEqual(new.replace(frag,b'',1),old)
        self.assertIn(b'$http_x_memory_mcp_test_effects_auth',frag)
        self.assertNotIn(b'X-Memory-MCP-Test-Read-Auth',frag)
        for before in (old+b'\n',old.replace(b'memory_mcp_test_read_auth',b'http_authorization')):
            with self.assertRaises(ValueError):plan(before,proof)
        drift=old.replace(b'memory_mcp_test_read_auth',b'http_authorization')
        with self.assertRaises(ValueError):plan(drift,hashlib.sha256(drift).hexdigest())
    def test_gateway_keeps_eight_paths_and_denies_changed_existing_read(self):
        origin='https://example.test'
        prior={'paths':{f'/path{i}':{'get':{}} for i in range(7)}}
        head={'Content-Type':'{Content-Type}','X-Memory-MCP-Test-Read-Auth':'{X-Memory-MCP-Test-Read-Auth}'}
        existing={'url':origin+OLD,'type':'http','method':'POST','headers':head}
        prior['paths'][OLD]={'post':{INTEGRATION:existing}}
        raw=json.dumps(prior).encode()
        got=parse(candidate(raw,origin))
        new=got['paths'].pop(NEW)
        self.assertEqual(got,prior)
        self.assertEqual(list(new),['post'])
        self.assertEqual(new['post'][INTEGRATION]['headers'],{'Content-Type':'{Content-Type}',AUTH:'{'+AUTH+'}'})
        existing['url']='https://foreign.test'+OLD
        with self.assertRaises(ValueError):candidate(json.dumps(prior).encode(),origin)
