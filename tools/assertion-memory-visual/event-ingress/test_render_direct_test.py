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
