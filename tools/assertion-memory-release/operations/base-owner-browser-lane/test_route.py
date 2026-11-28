from dataclasses import replace
from unittest import TestCase
from route import Qualified, render


class Carrier(TestCase):
    def test_exact_two_fixed_lanes_same_artifact_native_only(self):
        identity=dict(expected_revision='a'*40,expected_archive='b'*64)
        for stage,port in (('test',8891),('prod',8902)):
            witness=Qualified(stage,'a'*40,'b'*64,'base-browser-api',port,True,True,True)
            p=render(stage,witness,**identity)
            self.assertFalse(p['applied'])
            body=p['body']
            self.assertIn(f'proxy_pass http://127.0.0.1:{port}'.encode(),body)
            self.assertIn(b'proxy_set_header Origin $http_origin',body)
            self.assertIn(b'proxy_buffering off',body)
            for forbidden in (b':8901;',b':8893;',b':8903;',b'Authorization',b'X-Memory-'):
                self.assertNotIn(forbidden,body)
            for old in (replace(witness,component='standalone-graph'),replace(witness,port=8901),
                        replace(witness,owner_post_clear=False),replace(witness,graph_live_sse=False),
                        replace(witness,stage='prod' if stage=='test' else 'test')):
                with self.assertRaises(ValueError):
                    render(stage,old,**identity)
