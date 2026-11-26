from clear_v2_ingress import plan, reverse, ROOT, CLEAR
from copy import deepcopy
import unittest
from queue_owner_ingress import owner_routes


class OwnerIngress(unittest.TestCase):
    def test_identical_closed_route_contract_both_lanes(self):
        for lane,port in [('test',8829), ('prod',8830)]:
            with self.subTest(lane=lane):
                host=f'visual-{lane}.example.test'
                paths,nginx=owner_routes(lane=lane,host=host,backend_ip='192.0.2.100',
                    prefix='/__visual-owner',owner_auth='/__visual-owner-auth-check',
                    marker_include=f'/etc/nginx/{lane}/owner-markers.conf')
                operations={'pause', 'sessions', 'items/source-preview', 'items/remove', 'database/clear-v2'}
                self.assertEqual(set(paths),{'/api/v1/processing/'+action+suffix
                                             for action in operations for suffix in ('','/prepare')})
                self.assertEqual(nginx.count('auth_request /__visual-owner-auth-check;'),10)
                self.assertEqual(nginx.count(f'proxy_pass http://127.0.0.1:{port}'),10)
                self.assertNotIn('proxy_set_header Authorization ',nginx)
                self.assertNotIn('8893',nginx)
                self.assertNotIn('8903',nginx)
                for path,entry in paths.items():
                    method='post' if path.endswith('/prepare') else 'put'
                    self.assertEqual(set(entry),{method})
                    headers=entry[method]['x-yc-apigateway-integration']['headers']
                    self.assertNotIn('Authorization',headers)
                    self.assertFalse(any(name.startswith('X-Memory-') for name in headers))
                    self.assertEqual('X-CSRF-Token' in headers,method=='put')
class Proof(unittest.TestCase):
    def fixtures(self,lane):
        args=dict(lane=lane,host=f'{lane}.example.test',backend_ip='192.0.2.200',prefix='/__owner',
                  owner_auth='/__validate-owner',marker_include=f'/etc/nginx/{lane}-marker.conf')
        paths,server=owner_routes(**args)
        paths={k:v for k,v in paths.items() if CLEAR not in k}
        paths['/live/heartbeat']={'get':{'description':'leave unchanged'}}
        at=server.index('location = /__owner'+ROOT+CLEAR+'/prepare {')
        return args, {'openapi':'3.0.0','paths':paths},server[:at]+'# end sentinel\n'

    def test_additive_both_lanes(self):
        for lane,port in [('test',8829),('prod',8830)]:
            args,spec,server=self.fixtures(lane)
            result=plan(spec,server,**args)
            routes=result['gateway_after']['paths']
            self.assertEqual(set(routes)-set(spec['paths']),{ROOT+CLEAR,ROOT+CLEAR+'/prepare'})
            self.assertEqual({k:routes[k] for k in spec['paths']},spec['paths'])
            self.assertTrue(result['nginx_after'].endswith('# end sentinel\n'))
            for suffix,method in [('/prepare','post'),('','put')]:
                value=routes[ROOT+CLEAR+suffix]
                self.assertEqual(set(value),{method})
                self.assertNotIn('Authorization',value[method]['x-yc-apigateway-integration']['headers'])
                self.assertIn(f'http://127.0.0.1:{port}'+ROOT+CLEAR+suffix,result['nginx_after'])
            with self.assertRaises(ValueError):
                plan(result['gateway_after'],result['nginx_after'],**args)

    def test_inverse_preserves_independent_gateway_and_nginx_updates(self):
        args,spec,server=self.fixtures('test')
        server=''.join('  '+line for line in server.splitlines(keepends=True))
        result=plan(spec,server,**args)
        after=result['gateway_after']
        text=result['nginx_after']
        after['paths']['/unrelated']={'get':{}}
        original,raw,_=reverse(after,text+'# unrelated nginx\n',**args)
        self.assertEqual(original['paths']['/unrelated'],{'get':{}})
        self.assertEqual(raw,server+'# unrelated nginx\n')
        with self.assertRaises(ValueError):
            reverse(after,text.replace('client_max_body_size 20k;','client_max_body_size 40k;'),**args)

    def test_auth_drift_or_legacy_rejected(self):
        args,spec,server=self.fixtures('test')
        changed=deepcopy(spec)
        changed['paths'][ROOT+'items/remove']['put']['x-yc-apigateway-integration']['headers']['Authorization']='{Authorization}'
        with self.assertRaises(ValueError):
            plan(changed,server,**args)
        for bad in (server.replace('auth_request /__validate-owner;','auth_request /__other;'),
                    server+'location = /__owner/api/v1/processing/database/clear {\n}\n'):
            with self.assertRaises(ValueError):
                plan(spec,bad,**args)
        with self.assertRaises(ValueError):
            plan(dict(spec,paths=spec['paths']|{ROOT+'database/clear':{}}),server,**args)


from test_clear_v2_test_edge import TestCarrier  # included in the standard Visual PR test group
