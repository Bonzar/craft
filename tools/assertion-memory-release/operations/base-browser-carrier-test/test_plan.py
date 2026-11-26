import unittest
from unittest.mock import patch
import plan

def ensure(tail, content):
    return "location = /__mvt/api/v1/"+tail+" {\n"+content+"\n}\n"

SSE=''.join(ensure(k,'   # exact preserve '+k) for k in plan.SSE)
NGINX=('location = /__mvt/visual { return 200; }\n'+SSE+
       ensure('session','    proxy_set_header Origin $http_origin;')+
       'location ~ "^/__mvt/api/v1/processing(?:/[a-z0-9-]+){1,4}$" {\n'
       '    if ($something) { return 403; }\n'
       '    proxy_pass http://127.0.0.1:8829;\n}\n'
       'location /__mvt/ { return 404; }\n'
       'location /prod { return 200; }\n')

def spec():
    return {'paths':{plan.WILD:{m:{'parameters':[{'name':'tail','in':'path','required':True}],
      'x-yc-apigateway-integration':{'url':plan.TARGET,'type':'http','method':m.upper(),
        'headers':{'Cookie':'{Cookie}','Origin':'{Origin}'}}}
      for m in ('get','post','put')},'/visual':{'get':{}},'/api/v1/events-ticket':{'post':{}}}}

class TestTestCarrier(unittest.TestCase):
    def test_narrow_generation(self):
        raw=spec()
        with patch.object(plan,'TEST_NGINX_SHA',plan.sha(NGINX.encode())):
            pair=plan.plan(NGINX,raw,gateway_sha=plan.spec_sha(raw),verified_backend=True,verified_old_deny=True)
        nginx,gate=pair['after']
        self.assertEqual(nginx.count('location ^~ /__mvt/api/v1/'),1)
        self.assertNotIn('127.0.0.1:8829',nginx)
        self.assertIn('proxy_pass http://127.0.0.1:8891;',nginx)
        self.assertIn('proxy_set_header Origin $http_origin;',nginx)
        self.assertIn('proxy_pass_request_headers off;',nginx)
        self.assertIn('location = /__mvt/api/v1/processing/database/clear { return 404; }',nginx)
        self.assertIn(SSE,nginx)
        self.assertIn('location /prod { return 200; }',nginx)
        self.assertEqual(gate['paths']['/visual'],raw['paths']['/visual'])
        self.assertEqual(gate['paths']['/api/v1/events-ticket'],raw['paths']['/api/v1/events-ticket'])
        for method in plan.METHODS:
            op=gate['paths'][plan.WILD][method]['x-yc-apigateway-integration']
            self.assertEqual(op['method'],method.upper())
            self.assertEqual(op['query'],{'*':'*'})
            self.assertEqual(set(op['headers']),set(plan.WIRE))
            self.assertNotIn('Authorization',op['headers'])
        self.assertEqual(plan.inverse(pair,pair['after']),(NGINX,raw))
        with self.assertRaisesRegex(ValueError,'drift'):
            plan.inverse(pair,(nginx+' ',gate))

    def test_fail_closed(self):
        for edited in (NGINX+ensure('unreviewed',''),NGINX.replace(SSE,''),NGINX.replace('return 404','return 200')):
            with self.assertRaises(ValueError): plan.nginx_delta(edited)
        s=spec()
        with self.assertRaisesRegex(ValueError,'boundary'):
            plan.plan(NGINX,s,gateway_sha=plan.spec_sha(s))
        with self.assertRaisesRegex(ValueError,'baseline'):
            plan.plan(NGINX,s,gateway_sha='wrong',verified_backend=True,verified_old_deny=True)
        g=s['paths'][plan.WILD]['get']['x-yc-apigateway-integration']
        for extra in ({'Authorization':'{Authorization}'},{'*':'*'},{'X-Memory-Visual-Owner':'test'}):
            g['headers'].update(extra)
            with self.assertRaisesRegex(ValueError,'authority'):plan.gateway_delta(s)
            for k in extra:g['headers'].pop(k)
        g['url']=plan.TARGET.replace('__mvt','prod')
        with self.assertRaisesRegex(ValueError,'authority'):plan.gateway_delta(s)
