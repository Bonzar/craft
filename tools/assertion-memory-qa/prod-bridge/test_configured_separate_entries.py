import unittest
from unittest.mock import patch
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"assertion-memory-release"/"experimental"))
import configured_provider_checks as c
t=c.Target('env','test','account','command')
def profile(name):
 return c.Profile(name,f'assertion-memory-{name}.yml','configured-release.yml','configured-release-reusable.yml',t,t,c.ReceiptLayout('sha'))
base,visual=profile('base'),profile('visual')
tag='assertion-memory-v0.1.1'
def env(caller, stage='build'):
 return {'GITHUB_REPOSITORY':c.REPO,'GITHUB_REF':c.MAIN,'GITHUB_REF_PROTECTED':'true','GITHUB_WORKFLOW_REF':caller,'GITHUB_RUN_ATTEMPT':'1','GITHUB_JOB':stage,'GITHUB_RUN_ID':'9','GITHUB_SHA':'a'*40,'GITHUB_EVENT_NAME':'workflow_dispatch','PROFILE':'base','GRAPH_TAG':tag}
class Split(unittest.TestCase):
 @patch.object(c.canonical,'from_tag',return_value=dict(tag=tag,revision='b'*40,release_id='1',build_run_id='2',artifact_id='3',archive_sha='c'*64))
 def test_routes(self, fake):
  self.assertIs(c.build_selection(None,(base,visual),env(base.separate_caller),{'inputs':{'tag':tag}})[0],base)
  self.assertIs(c.build_selection(None,(base,visual),env(base.caller),{'inputs':{'profile':'base','tag':tag}})[0],base)
  for caller,picks in ((visual.separate_caller,{'tag':tag}),(base.caller,{'tag':tag}),(base.separate_caller,{'profile':'base','tag':tag}),(base.separate_caller.replace('main','topic'),{'tag':tag})):
   with self.assertRaises(ValueError): c.build_selection(None,(base,visual),env(caller),{'inputs':picks})
 def test_bound_receipt_caller(self):
  ctx=c.control(base,env(base.separate_caller,'prod'),'prod',{'inputs':{'tag':tag}})
  self.assertEqual(ctx.caller,base.separate_caller)
  self.assertEqual(ctx.workflow,'configured-base-release.yml')
  with self.assertRaises(ValueError): c.control(base,env(visual.separate_caller,'prod'),'prod',{'inputs':{'tag':tag}})
 def test_crossed_automatic_producer(self):
  altered=env(visual.separate_caller)
  altered['GITHUB_EVENT_NAME']='workflow_run'
  with self.assertRaises(ValueError):
   c.build_selection(None,(base,visual),altered,{'workflow_run':{'path':'.github/workflows/assertion-memory-base.yml'}})
if __name__=='__main__': unittest.main()
