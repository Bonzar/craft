from copy import deepcopy
import unittest
import render_direct_prod as r

IP='8.8.4.4' # format fixture only; no network is opened
OTHER='8.8.8.8'
AUTH="""location = /__visual-owner-auth-check {
    internal;
    proxy_pass_request_headers off;
    proxy_set_header Cookie $http_cookie;
    proxy_pass http://127.0.0.1:8901/api/v1/capabilities;
}
"""

class Plan(unittest.TestCase):
    def setUp(self):
        self.spec={'paths':{r.PROD_ANCHOR:{'get':{r.HTTP:{'type':'http',
            'url':f'https://{IP}'+r.PROD_ANCHOR_EDGE,'headers':{'Cookie':'{Cookie}'}}}},
            '/visual':{'get':{}}}}

    def test_gateway_adds_only_browser_ticket(self):
        old=deepcopy(self.spec)
        out=r.gateway(self.spec,direct_ip=IP)
        self.assertEqual(self.spec,old)
        ticket=out['paths'].pop(r.TICKET)
        self.assertEqual(out,old)
        self.assertEqual(set(ticket),{'post'})
        self.assertEqual(set(ticket['post'][r.HTTP]['headers']),set(r.FIELDS))
        self.assertNotIn(r.DIRECT,out['paths'])

    def test_separate_no_cookie_direct_and_no_bearer_issuer(self):
        result=r.nginx(AUTH,direct_ip=IP)
        a,b=result.split('location = '+r.PRIVATE+r.DIRECT,1)
        self.assertIn('auth_request '+r.AUTH,a)
        self.assertIn('Cookie $http_cookie',a)
        self.assertNotIn('Authorization',a)
        self.assertIn('if ($http_cookie != "")',b)
        self.assertNotIn('proxy_set_header Cookie',b)
        self.assertIn('proxy_buffering off;',b)
        self.assertIn('gzip off;',b)
        self.assertIn('Authorization $http_authorization',b)
        self.assertEqual(result.count(r.ORIGIN),2)
        self.assertEqual(r.owner_csp("connect-src 'self';",direct_ip=IP),
            "connect-src 'self' https://8.8.4.4;")

    def test_drift_wrong_scope_and_duplicate_fail_closed(self):
        for ip in (OTHER,'127.0.0.1','8.8.4.4:443'):
            with self.assertRaises(ValueError):
                r.gateway(self.spec,direct_ip=ip)
        for bad in (AUTH+'\n'+AUTH,AUTH.replace('8901','8891'),AUTH+'\n'+r.DIRECT, AUTH.replace(
                'proxy_pass_request_headers off;','proxy_set_header Authorization $http_authorization;')):
            with self.assertRaises(ValueError):
                r.nginx(bad,direct_ip=IP)
        self.spec['paths'][r.TICKET]={}
        with self.assertRaises(ValueError):
            r.gateway(self.spec,direct_ip=IP)

if __name__=='__main__': unittest.main()
