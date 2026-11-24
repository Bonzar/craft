
import contextlib, io, unittest
import yaml
import stage_test_gateway as g
ORIGIN='https://192.0.2.10'
def base():
 return {'openapi':'3.0.0','info':{'title':'fictional','version':'1.0'},'paths':{
   **{f'/other-{i}':{'post':{'responses':{'200':{'description':'stable'}}}} for i in range(6)},
   g.OLD:{'post':{g.INTEGRATION:{'type':'http','url':ORIGIN+g.OLD},'responses':{'200':{'description':'proof'}}}}
 }}
def wire(x):return yaml.safe_dump(x,sort_keys=False).encode()
class GatewayPlan(unittest.TestCase):
 def test_one_explicit_https_test_path_only(self):
  old=base(); out=g.parse(g.candidate(wire(old),ORIGIN)); new=out['paths'].pop(g.NEW)
  self.assertEqual(out,old); self.assertEqual(list(new),['post'])
  relay=new['post'][g.INTEGRATION]
  self.assertEqual(relay['url'],ORIGIN+g.NEW)
  self.assertEqual(relay['headers'],{'Content-Type':'{Content-Type}',g.AUTH:'{'+g.AUTH+'}'})
  self.assertTrue(relay['omitEmptyHeaders'])
 def test_never_trust_drift_or_existing_path(self):
  for n in ('count','wildcard','any_method','already_present','wrong_upstream'):
   b=base()
   if n=='count':b['paths'].pop('/other-0')
   if n=='wildcard':b['paths']['/{all+}']=b['paths'].pop('/other-0')
   if n=='any_method':b['paths']['/other-0']['x-yc-apigateway-any-method']={}
   if n=='already_present':b['paths'][g.NEW]=b['paths'].pop('/other-0')
   if n=='wrong_upstream':b['paths'][g.OLD]['post'][g.INTEGRATION]['url']='https://wrong.example'+g.OLD
   with self.subTest(n=n), self.assertRaises(ValueError):g.candidate(wire(b),ORIGIN)
  for origin in ('http://192.0.2.10',ORIGIN+'/alternate','https://someone@192.0.2.10'):
   with self.assertRaises(ValueError):g.candidate(wire(base()),origin)
 def test_duplicate_alias_and_oversize_denials(self):
  for b in (b'paths: {}\npaths: {}\n',b'paths: {}\nx: {200: one, "200": two}\n',b'paths: &anything {}\nelse: *anything\n',
            b'paths: !!map {}\n',b'x'*257000):
   with self.assertRaises(ValueError):g.parse(b)
if __name__=='__main__':unittest.main()
