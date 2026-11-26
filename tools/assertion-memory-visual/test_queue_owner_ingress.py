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
                operations={'pause', 'sessions', 'items/source-preview', 'items/remove', 'database/clear'}
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
