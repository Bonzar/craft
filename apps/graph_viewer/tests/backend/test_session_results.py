"""Exact legacy completion receipts, on an isolated Source database only."""
import hashlib,json,sqlite3,unittest
import test_session_filter as base_fixture
EARLY=base_fixture.EARLY
from apps.graph_viewer.server.cards import overview_page
from apps.graph_viewer.server.common import APIError
from apps.graph_viewer.server.search import search

class SessionResults(unittest.TestCase):
 graph=base_fixture.SessionFilter.graph
 def tearDown(self):base_fixture.SessionFilter.tearDown(self)
 def setUp(self):
  base_fixture.SessionFilter.setUp(self)
  with sqlite3.connect(self.db) as c:
   c.execute('CREATE TABLE memory_capture_jobs(id TEXT PRIMARY KEY,event_id TEXT,assertion_id TEXT,status TEXT,phase TEXT,updated_at TEXT,refreshed_views TEXT,error TEXT)')
   for version,ids,text in [(3,['b1','c1','missing-claim'],'Old causally saved answer'),(9,['a2'],'Latest unrelated answer')]:
    c.execute('INSERT INTO memory_view_versions VALUES(?,?,?,?)',(version,'mixed',json.dumps({'evidence':[{'id':key} for key in ids],'summary':text}),EARLY))
   c.execute("UPDATE memory_views SET current_id=9 WHERE id='mixed'")
   c.execute("INSERT INTO memory_views VALUES('unrelated','{}',20,1,NULL,NULL)");c.execute('INSERT INTO memory_view_versions VALUES(?,?,?,?)',(20,'unrelated',json.dumps({'evidence':[],'summary':'UnavailableCurrentBody'}),EARLY))
   self.job(c,'A','ea',None,'2025-02-01',{'mixed':3})
   self.job(c,'B','eb',None,'2025-03-01',{'mixed':9})
  self.sha=hashlib.sha256(self.db.read_bytes()).hexdigest()
 def job(self,c,id,event,assertion,updated,outputs,**extra):
  c.execute('INSERT INTO memory_capture_jobs VALUES(?,?,?,?,?,?,?,?)',(id,event,assertion,extra.get('status','completed'),extra.get('phase','done'),updated,json.dumps(outputs),extra.get('error')))
 def edit(self,sql,args=()):
  with sqlite3.connect(self.db) as c:c.execute(sql,args)
  self.sha=hashlib.sha256(self.db.read_bytes()).hexdigest()
 def diagnostic(self):
  from apps.graph_viewer.server.providers.session_results import legacy_session_results
  with self.source.connect() as c:return legacy_session_results(c,'native:chat-A','2026-01-01T00:00:00.000000+00:00')
 def test_literal_legacy_job_diagnostics_are_not_promoted_into_causal_topics(self):
  pins,history=self.diagnostic();self.assertEqual(pins['mixed']['version_id'],3);self.assertFalse(history['history_complete']);graph=self.graph('native:chat-A',True);self.assertEqual(graph.topics()['topics'],[]);self.assertTrue(graph.topics()['layout_topics']);self.assertEqual(graph.manifest['session_filter']['overview_history']['reason'],'recorded_runs_only')
 def test_diagnostic_last_completion_not_max_version_still_does_not_authorize_causal_fallback(self):
  with sqlite3.connect(self.db) as c:self.job(c,'A-later','ea',None,'2025-04-01',{'mixed':1},status='waiting_model')
  self.sha=hashlib.sha256(self.db.read_bytes()).hexdigest();self.assertEqual(self.diagnostic()[0]['mixed']['version_id'],1);self.assertEqual(self.graph('native:chat-A',True).topics()['topics'],[])
 def test_no_receipt_is_unknown_not_current_membership_material(self):
  self.edit('DELETE FROM memory_capture_jobs');self.assertEqual(self.diagnostic()[0],{});graph=self.graph('native:chat-A',True);self.assertEqual(graph.topics()['topics'],[]);self.assertFalse(graph.manifest['session_filter']['overview_history']['history_complete'])
 def test_broken_legacy_diagnostic_does_not_guess_a_current_version(self):
  self.edit("UPDATE memory_capture_jobs SET refreshed_views=? WHERE id='A'",(json.dumps({'mixed':999}),));pins,history=self.diagnostic();self.assertEqual(pins,{});self.assertGreater(history['unresolved'],0);self.assertEqual(self.graph('native:chat-A',True).topics()['topics'],[])
 def test_assertion_fallback_requires_validated_owner_even_for_diagnostics(self):
  self.edit("UPDATE memory_capture_jobs SET assertion_id='unknown',event_id=NULL WHERE id='A'");self.assertEqual(self.diagnostic()[0],{});self.edit('DROP TABLE memory_extraction_runs');self.assertEqual(self.diagnostic()[0],{})
