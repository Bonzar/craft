
import unittest
from stage_nginx_pair import plan,old_to_new,NEW_TEST
prefix=b'server {\n    listen 443 ssl;\n'
suffix=b'    location = /memory-mcp-test {\n        proxy_pass http://127.0.0.1:8831;\n    }\n}\n'
baseline=prefix+b'    location = /mcp {\n        proxy_pass http://127.0.0.1:8803;\n    }\n    location = /memory-graph-read {\n        proxy_pass http://127.0.0.1:8818;\n    }\n'+suffix
class TestPair(unittest.TestCase):
 def test_exact_two_stable_prod_substitutions_and_rollback(self):
  c=old_to_new(baseline);b,a,r=plan(baseline,c)
  self.assertEqual(old_to_new(b),a)
  self.assertEqual(b.replace(r,b'',1),baseline)
  self.assertEqual(a.replace(r,b'',1),c)
  self.assertEqual(b.count(NEW_TEST),1)
  self.assertEqual(a.count(NEW_TEST),1)
 def test_already_inserted_or_anchor_missing_deny(self):
  b,a,_=plan(baseline,old_to_new(baseline))
  for item in (b,baseline.replace(b'/memory-mcp-test ',b'/unexpected '),baseline+suffix):
   with self.assertRaises(ValueError):plan(item,old_to_new(item))
 def test_scope_drift_deny(self):
  for c in (old_to_new(baseline)+b'\n',old_to_new(baseline).replace(b':8831',b':8903')):
   with self.assertRaises(ValueError):plan(baseline,c)
 def test_untrusted_target_and_limits(self):
  for b in (baseline+b'#unexpected 127.0.0.1:8832',baseline.replace(b'listen',b'\rlisten'),baseline+b' '*128000):
   with self.assertRaises(ValueError):plan(b,old_to_new(b))
if __name__=='__main__':unittest.main()
