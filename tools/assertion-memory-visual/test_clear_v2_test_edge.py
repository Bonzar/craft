from hashlib import sha256
import unittest
from clear_v2_test_edge import plan, rollback, ROOT, CLEAR, LEGACY, WILDCARD, spec_pin

SERVER = '''  location = /__mvt_auth {
    internal;
    proxy_pass_request_headers off;
    proxy_set_header Cookie $http_cookie;
    proxy_pass http://127.0.0.1:8891/api/v1/capabilities;
  }
  location ~ "^/__mvt/api/v1/processing(?:/[a-z0-9-]+){1,4}$" {
    auth_request /__mvt_auth;
    include /etc/nginx/assertion-visual-test-edge/markers.conf;
    proxy_pass_request_headers off;
    proxy_set_header Host test.example.test;
    proxy_set_header Origin $http_origin;
    proxy_pass http://127.0.0.1:8829;
  }
  location /__mvt/ { return 404; }
'''

def gateways():
    h={h:'{'+h+'}' for h in ('Cookie','Origin','Content-Type')}
    base={'type':'http','url':'https://192.0.2.9/__mvt/api/v1/{tail}'}
    return {'paths':{WILDCARD:{'post':{'x-yc-apigateway-integration':base|{'headers':h}},
        'put':{'x-yc-apigateway-integration':base|{'headers':h|{'X-CSRF-Token':'{X-CSRF-Token}'}}}},
        '/api/v1/events-proof':{'get':{'proven':'preserve'}}}}

class TestCarrier(unittest.TestCase):
    def apply(self,scope=None,server=SERVER,proven=True):
        baseline=scope if scope is not None else gateways()
        return plan(baseline,server,host='test.example.test',ipv4='192.0.2.9',
                    previous_nginx_sha=sha256(server.encode()).hexdigest(),previous_gateway_sha=spec_pin(baseline),
                    owner_dashboard_proven=proven)

    def test_two_paths_only_preserve_sse_and_caller_has_no_new_authority(self):
        p=self.apply()
        gw,n=p['active']['gateway'],p['active']['nginx']
        self.assertEqual(rollback(p,gw,n),p['dark'])
        self.assertEqual(p['dark']['gateway'],gateways())
        for tail in (ROOT+LEGACY,ROOT+LEGACY+'/prepare'):
            deny=f'location = /__mvt{tail} {{\n      return 404;'
            self.assertIn(deny,n)
            self.assertIn(deny,p['dark']['nginx'])
        for tail in (ROOT+CLEAR,ROOT+CLEAR+'/prepare'):
            self.assertIn(f'location = /__mvt{tail} {{\n      return 404;',p['dark']['nginx'])
            self.assertNotIn(f'location = /__mvt{tail} {{\n      return 404;',n)
        with self.assertRaisesRegex(ValueError,'drifted'):
            rollback(p,gw,n+'# external edit')
        self.assertEqual(set(gw['paths'])-set(gateways()['paths']),
                         {ROOT+CLEAR,ROOT+CLEAR+'/prepare'})
        self.assertEqual(gw['paths']['/api/v1/events-proof'],gateways()['paths']['/api/v1/events-proof'])
        self.assertEqual(n.count('proxy_pass http://127.0.0.1:8829/api/v1/processing/database/clear-v2'),2)
        self.assertIn('proxy_pass http://127.0.0.1:8891/api/v1/capabilities;',n)
        for bad in (SERVER.replace('    internal;',''),
                    SERVER.replace('[a-z0-9-]+','.+'),
                    SERVER.replace('proxy_pass_request_headers off;','proxy_pass_request_headers on;')):
            with self.assertRaises(ValueError): self.apply(server=bad)

    def test_reject_missing_owner_snapshot_or_unscoped_gateway(self):
        with self.assertRaises(ValueError): self.apply(proven=False)
        gateway=gateways()
        gateway['paths'][WILDCARD]['put']['x-yc-apigateway-integration']['headers']['Authorization']='{Authorization}'
        with self.assertRaises(ValueError): self.apply(scope=gateway)
