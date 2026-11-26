from copy import deepcopy
import unittest
from render_direct_test import gateway,nginx,csp,ISSUER,DIRECT
from test_render_test import NGINX,W

class DirectPlan(unittest.TestCase):
    def test_direct_read_does_not_enter_managed_gateway(self):
        old=deepcopy(W)
        old['paths']['/api/v1/{tail+}']['post']['x-yc-apigateway-integration']={
            'type':'http','url':'https://192.0.2.7/__mvt/api/v1/{tail}',
            'headers':{'Cookie':'{Cookie}','Origin':'{Origin}','X-CSRF-Token':'{X-CSRF-Token}'}}
        modified=gateway(old)
        added=modified['paths'].pop(ISSUER)
        self.assertNotIn(DIRECT,modified['paths'])
        self.assertEqual(modified,old)
        self.assertEqual(set(added),{'post'})
        broken=deepcopy(old)
        broken['paths']['/api/v1/{tail+}']['post']['x-yc-apigateway-integration']['url']='https://192.0.2.7/__mvp/api/v1/{tail}'
        with self.assertRaises(ValueError):gateway(broken)

    def test_proxy_stream_does_not_forward_owner_cookie_and_csp_is_exact(self):
        modified=nginx(NGINX)
        self.assertIn('proxy_buffering off;',modified)
        self.assertEqual(modified.count('access_log off;'),2)
        extracted=modified.split('location = /__mvt/api/v1/events-direct',1)[1].split('location ~',1)[0]
        self.assertIn('if ($http_cookie != "") { return 403; }',extracted)
        self.assertNotIn('proxy_set_header Cookie',extracted)
        self.assertIn('Authorization $http_authorization',extracted)
        self.assertEqual(csp("default-src 'self'; connect-src 'self';","https://8.8.8.8"),
                         "default-src 'self'; connect-src 'self' https://8.8.8.8;")
        with self.assertRaises(ValueError):csp("connect-src *;","https://8.8.8.8")
        with self.assertRaises(ValueError):csp("connect-src 'self';","https://127.0.0.1")

    def test_diagnostic_proxy_uses_verified_owner_authority(self):
        from render_direct_test import proof_nginx,proof_gateway,PROOF,PROOF_FILE
        base=NGINX+'''
  location = /__mvt_auth {
    internal;
    proxy_pass_request_headers off;
    proxy_set_header Cookie $http_cookie;
    proxy_pass http://127.0.0.1:8891/api/v1/capabilities;
  }
'''
        origin='https://8.8.8.8'
        policy=("default-src 'none'; script-src 'sha256-"+'A'*43+"='; connect-src 'self' "+origin+
                "; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        rendered=proof_nginx(base,policy,origin)
        self.assertIn('auth_request /__mvt_auth;',rendered)
        self.assertIn('alias '+PROOF_FILE+';',rendered)
        self.assertIn('Cache-Control "private, no-store"',rendered)
        with self.assertRaises(ValueError):
            proof_nginx(base.replace('/api/v1/capabilities;', '/api/v1/health;'),policy,origin)
        with self.assertRaises(ValueError):
            proof_nginx(base,policy+';',origin)
        updated=proof_gateway(W)
        entry=updated['paths'].pop(PROOF)
        self.assertEqual(set(entry),{'get'})
        self.assertEqual(entry['get']['x-yc-apigateway-integration']['headers'],{'Cookie':'{Cookie}'})
        self.assertEqual(updated,W)

    def test_combined_exact_plan_and_inverse(self):
        from direct_test_bundle import render_routes,render_gateway,validate_page
        source=NGINX+'''
  location = /__mvt_auth {
    internal;
    proxy_pass_request_headers off;
    proxy_set_header Cookie $http_cookie;
    proxy_pass http://127.0.0.1:8891/api/v1/capabilities;
  }
'''
        import base64,hashlib
        script="console.log('synthetic example')"
        proof=f'<html><script>{script}</script></html>\n'
        origin='https://8.8.8.8'
        pin=base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        policy=(f"default-src 'none'; script-src 'sha256-{pin}'; connect-src 'self' {origin}"
                "; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.assertEqual(validate_page(proof,policy),hashlib.sha256(proof.encode()).hexdigest())
        with self.assertRaises(ValueError):
            validate_page(proof.replace('example','changed'),policy)
        candidate=render_routes(source,policy,origin)
        self.assertEqual(render_routes(candidate,policy,origin,True),source)
        with self.assertRaises(ValueError):
            render_routes(candidate.replace('proxy_read_timeout 15s;','proxy_read_timeout 16s;'),policy,origin,True)
        before=deepcopy(W)
        before['paths']['/api/v1/{tail+}']['post']['x-yc-apigateway-integration']={
            'type':'http','url':'https://192.0.2.7/__mvt/api/v1/{tail}',
            'headers':{'Cookie':'{Cookie}','Origin':'{Origin}','X-CSRF-Token':'{X-CSRF-Token}'}}
        installed=render_gateway(before)
        self.assertEqual(render_gateway(installed,True),before)
        installed['paths'][ISSUER]['post']['x-yc-apigateway-integration']['timeouts']['read']=10
        with self.assertRaises(ValueError):
            render_gateway(installed,True)
