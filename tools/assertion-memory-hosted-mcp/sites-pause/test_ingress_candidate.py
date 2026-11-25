"""Offline checks; use actual nginx -t and provider test in the release controller."""
from copy import deepcopy
import unittest

from ingress_candidate import HEADER, PATH, SIBLING, nginx_fragment, prepare_gateway


class IngressTest(unittest.TestCase):
    origin='https://test-relay.example'
    def existing(self):
        return {'openapi':'3.0.0','info':{'title':'Test','version':'1'},
            'paths':{SIBLING:{'post':{'x-yc-apigateway-integration':{
                'type':'http','url':self.origin+SIBLING,'method':'POST'}}},
                '/unrelated':{'get':{'security':[{'existing':[]}]}}}}

    def test_single_addition_has_no_external_credentials_or_proxy_headers(self):
        original=self.existing()
        copied=deepcopy(original)
        staged=prepare_gateway(original,self.origin)
        self.assertEqual(original,copied)
        mapping=staged['paths'].pop(PATH)
        self.assertEqual(staged,original)
        self.assertEqual(set(mapping),{'post'})
        integration=mapping['post']['x-yc-apigateway-integration']
        self.assertEqual(integration['url'],self.origin+PATH)
        self.assertEqual(integration['headers'],{HEADER:'{'+HEADER+'}','Content-Type':'{Content-Type}'})
        self.assertNotIn('query',integration)
        block=nginx_fragment()
        self.assertIn('proxy_pass_request_headers off;',block)
        self.assertIn('proxy_set_header Authorization $http_x_memory_test_pause_auth;',block)
        self.assertIn('proxy_pass http://127.0.0.1:8893'+PATH+';',block)
        for forbidden in ('proxy_set_header Cookie','proxy_set_header Origin','proxy_set_header X-Oai',
                          'proxy_set_header Authorization $http_authorization'):
            self.assertNotIn(forbidden,block)

    def test_reject_wrong_origin_sibling_prior_route_or_wildcard(self):
        for wrong in ('http://test-relay.example','https://test-relay.example/prefix',
                      'https://test-relay.example:8443','https://prod-relay.example'):
            with self.subTest(wrong=wrong),self.assertRaises(ValueError):
                prepare_gateway(self.existing(),wrong)
        for drift in (PATH,PATH+'/','/{path+}','/memory-{path+}'):
            with self.subTest(drift=drift),self.assertRaises(ValueError):
                other=self.existing()
                other['paths'][drift]={}
                prepare_gateway(other,self.origin)

if __name__=='__main__':
    unittest.main()
