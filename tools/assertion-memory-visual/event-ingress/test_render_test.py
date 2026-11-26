from copy import deepcopy
import unittest
from render_test import PRIVATE_PATH, PUBLIC_PATH, apigw, nginx

NGINX='''
  location ~ "^/__mvt/api/v1/(?:graph|search)$" {
    if ($request_method !~ ^(GET|POST)$) { return 405; }
    rewrite ^/__mvt(/.*)$ $1 break;
    proxy_pass_request_headers off;
    proxy_set_header Host 127.0.0.1:8891;
    proxy_set_header Cookie $http_cookie;
    proxy_set_header Content-Type $http_content_type;
    proxy_set_header X-CSRF-Token $http_x_csrf_token;
    proxy_cache off;
    proxy_pass http://127.0.0.1:8891;
  }
  location /__mvt/ { return 404; }
'''
W={'openapi':'3.0.0','paths':{'/api/v1/{tail+}':{'get':{'x-yc-apigateway-integration':{
    'type':'http','url':'https://192.0.2.7/__mvt/api/v1/{tail}',
    'headers':{'Cookie':'{Cookie}','Origin':'{Origin}','Sec-Fetch-Site':'{Sec-Fetch-Site}'}}},
    'post':{'responses':{}}},'/login':{'get':{'responses':{}}}}}

class Prepare(unittest.TestCase):
    def test_nginx_exact_all_existing_kept(self):
        rendered=nginx(NGINX)
        self.assertEqual(rendered.count(PRIVATE_PATH),1)
        self.assertIn('proxy_buffering off;',rendered)
        self.assertIn('proxy_set_header Last-Event-ID $http_last_event_id;',rendered)
        self.assertIn('proxy_set_header Cookie $http_cookie;',rendered)
        self.assertIn('if ($request_method != GET)',rendered)
        start=rendered.index('  location = '+PRIVATE_PATH)
        end=rendered.index('  location ~')
        self.assertEqual(rendered[:start]+rendered[end:],NGINX)
        with self.assertRaises(ValueError):
            nginx(NGINX.replace('8891','8901'))
        with self.assertRaises(ValueError):
            nginx(NGINX.replace('proxy_cache off;','proxy_cache off;\nproxy_set_header Authorization xyz;'))
    def test_gateway_get_only_keeps_every_existing_value(self):
        old=deepcopy(W)
        transformed=apigw(W)
        self.assertEqual(old,W)
        endpoint=transformed['paths'].pop(PUBLIC_PATH)
        self.assertEqual(transformed,W)
        self.assertEqual(set(endpoint),{'get'})
        route=endpoint['get']['x-yc-apigateway-integration']
        self.assertEqual(route['url'],'https://192.0.2.7'+PRIVATE_PATH)
        self.assertEqual(route['query'],{'after':'{after}','revision':'{revision}'})
        self.assertEqual(set(route['headers']),{'Cookie','Origin','Sec-Fetch-Site','Last-Event-ID'})
        cross_lane=deepcopy(W)
        cross_lane['paths']['/api/v1/{tail+}']['get']['x-yc-apigateway-integration']['url']='https://192.0.2.7/__mvp/api/v1/{tail}'
        with self.assertRaises(ValueError):
            apigw(cross_lane)
        old['paths']['/api/v1/{tail+}']['get']['x-yc-apigateway-integration']['headers']['Cookie']='bad'
        with self.assertRaises(ValueError):
            apigw(old)

if __name__=='__main__':
    unittest.main()
