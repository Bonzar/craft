import unittest
from unittest.mock import patch
import neutral_edge_plan as edge
from neutral_edge_plan import derive,digest,inverse

SAMPLE=b"""server {
  listen 443 ssl;
  location /visual { return 200; }
  location = /memory-api-prod {
    proxy_pass http://127.0.0.1:8903;
  }
  location = /memory-api-test {
    proxy_pass http://127.0.0.1:8893;
    proxy_set_header Host localhost;
  }
  location = /memory-mcp-test-tools {
    proxy_pass http://127.0.0.1:8893;
  }
}
"""

def plan(src,**override):
    return derive(src,**(dict(expected_sha256=digest(src),registered_clients=0,
        gateway_paths_absent=True,alternate_forwards_absent=True)|override))

class TestExactAlias(unittest.TestCase):
    def test_change_only_exact_test_block_and_reverse(self):
        p=plan(SAMPLE);c=p['candidate']
        old_line=b'  location = /memory-api-test {\n'
        self.assertEqual(c.replace(old_line+b'    return 404; # TEST qa-runner uses only the existing direct loopback API\n',old_line),SAMPLE)
        self.assertTrue(p['prod_bytes_identical']);self.assertFalse(p['writes_performed'])
        self.assertEqual(inverse(c,expected_after_sha256=digest(c),expected_before_sha256=digest(SAMPLE)),SAMPLE)
    def test_one_known_pinned_inline_guard_survives_both_directions(self):
        guard=b'    if ($flag) { return 400; }\n'
        witness=SAMPLE.replace(b'  location = /memory-api-test {\n',b'  location = /memory-api-test {\n'+guard)
        candidate=witness.replace(guard,b'    '+edge.DENIAL+b'\n'+guard)
        with patch.multiple(edge,PINNED_GUARD_FILE=digest(witness),PINNED_GUARD_SHIELDED=digest(candidate),
                            PINNED_GUARD_LINE=digest(guard)):
            self.assertEqual(plan(witness)['candidate'],candidate)
            self.assertEqual(inverse(candidate,expected_after_sha256=digest(candidate),
                             expected_before_sha256=digest(witness)),witness)
            with self.assertRaises(ValueError):plan(witness.replace(b'return 400',b'return 401'))
        with self.assertRaises(ValueError):plan(witness)
    def test_inventory_and_drift_fail_closed(self):
        cases=dict(registered_clients=1,gateway_paths_absent=False,alternate_forwards_absent=False,expected_sha256='f'*64)
        for k,v in cases.items():
            with self.subTest(k=k),self.assertRaises(ValueError):plan(SAMPLE,**{k:v})
        p=plan(SAMPLE)
        with self.assertRaises(ValueError):inverse(p['candidate']+b'\n',expected_after_sha256=p['after_sha256'],expected_before_sha256=digest(SAMPLE))
        with self.assertRaises(ValueError):plan(p['candidate'])
    def test_duplicate_changed_destination_and_nested_blocks_are_refused(self):
        for text in (SAMPLE+SAMPLE,SAMPLE.replace(b'location = /memory-api-test',b'location /memory-api-test'),SAMPLE.replace(b':8893',b':8903'),
                     SAMPLE.replace(b'    proxy_set_header Host localhost;',b'    if ($flag) { return 400; }')):
            with self.assertRaises(ValueError):plan(text)
    def test_active_legacy_equivalent_directive_requires_review(self):
        with self.assertRaises(ValueError):
            plan(SAMPLE.replace(b'    proxy_set_header Host localhost;',b'    internal;'))
