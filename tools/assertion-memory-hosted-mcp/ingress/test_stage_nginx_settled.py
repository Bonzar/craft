import unittest
from stage_nginx_pair import sha
from stage_nginx_settled import plan_settled
BASE=b'''server {
    location = /memory-mcp-test {
        proxy_pass http://127.0.0.1:8893;
    }
    location = /prod-graph { proxy_pass http://127.0.0.1:8903; }
    location = /prod-mcp { proxy_pass http://127.0.0.1:8918; }
}
'''
class TestSettled(unittest.TestCase):
    def test_only_one_insert_no_neighbor_changes(self):
        updated,fragment,proof=plan_settled(BASE,sha(BASE))
        self.assertEqual(updated.count(fragment),1)
        self.assertEqual(updated.replace(fragment,b'',1),BASE)
        self.assertEqual(proof['unchanged_locations_bytes_sha256'],sha(BASE))
    def test_drift_rejected_even_when_upstreams_same(self):
        with self.assertRaisesRegex(ValueError,'digest mismatch'):
            plan_settled(BASE+b'# unrelated drift',sha(BASE))
    def test_old_duplicate_and_existing_443_route_rejected_even_with_fresh_hash(self):
        for b in (BASE.replace(b':8903',b':8803'),BASE+b'# :8832',BASE+b'# http://127.0.0.1:8918',BASE+BASE.split(b'    location = /prod-graph',1)[0]):
            with self.subTest(),self.assertRaises(ValueError):
                plan_settled(b,sha(b))
