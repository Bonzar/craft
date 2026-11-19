"""Relevant read-only revision fingerprints, not worker heartbeat epochs."""
import asyncio,hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
from test_service import fixture,EARLY
from apps.graph_viewer.server.providers.assertion_memory import AssertionMemorySource
from apps.graph_viewer.server.manager import GraphManager

class StableRevision(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'source.sqlite3';fixture(self.path)
  with sqlite3.connect(self.path) as c:
   c.execute('CREATE TABLE memory_derived_work(scope TEXT PRIMARY KEY,updated_at TEXT)');c.execute("INSERT INTO memory_derived_work VALUES('s','first')")
   c.execute('CREATE TABLE memory_extraction_runs(id INTEGER PRIMARY KEY,event_id TEXT,result TEXT,completed_at TEXT)')
   c.execute('CREATE TABLE memory_view_names(id INTEGER PRIMARY KEY,body TEXT)')
   c.execute('CREATE TABLE memory_run_events(id INTEGER PRIMARY KEY,run_id TEXT,kind TEXT,outcome TEXT,version_id INTEGER)')
  self.source=AssertionMemorySource(self.path)
 def tearDown(self):self.source.close();self.tmp.cleanup()
 def write(self,sql,args=()):
  with sqlite3.connect(self.path) as c:c.execute(sql,args)
 def test_worker_heartbeat_has_no_revision_effect_and_unchanged_reads_do_not_rehash(self):
  calls=[];self.source._watch.set_trace_callback(calls.append);first=self.source.revision();self.assertTrue(any('SELECT * FROM memory_views' in sql for sql in calls));calls.clear()
  for _ in range(3):self.assertEqual(self.source.revision(),first)
  self.assertEqual(calls,['PRAGMA data_version']*3,'steady polling never rescans Source rows or recomputes fingerprint')
  self.write("UPDATE memory_derived_work SET updated_at='heartbeat-2'");before=hashlib.sha256(self.path.read_bytes()).hexdigest();self.assertEqual(self.source.revision(),first);self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before);self.assertEqual(self.source._watch.total_changes,0);self.assertEqual(self.source._watch.execute('PRAGMA query_only').fetchone()[0],1)
 def test_every_declared_viewer_input_and_causal_reuse_changes_revision_without_state_bump(self):
  operations=[('INSERT INTO memory_view_names VALUES(?,?)',(1,'name')),('INSERT INTO memory_extraction_runs VALUES(?,?,?,?)',(1,'event','{"assertion_ids":[]}',EARLY)),('INSERT INTO memory_raw_events VALUES(?,?,?,?,?,?,?,?,?)',('new-event','session','turn','new source text','actor','saved','s',EARLY,'{}')),("UPDATE memory_views SET current_id=2,current_check_id=2,error='failed' WHERE id='parent'",()),("UPDATE memory_views SET definition=? WHERE id='parent'",(json.dumps({'title':'Changed exact source definition'}),)),('INSERT INTO memory_assertions VALUES(?,?,?,?,?,?,?,?,?)',('new-claim','literal','actor','saved','session',EARLY,EARLY,'s','{}')),('INSERT INTO memory_edges VALUES(?,?,?,?,?,?)',('new-edge','old','unicode','supports',EARLY,'{}')),('INSERT INTO memory_view_versions VALUES(?,?,?,?)',(3,'parent','{}',EARLY)),('INSERT INTO memory_view_checks VALUES(?,?,?,?,?,?)',(3,'parent',3,1,EARLY,'current')),('INSERT INTO memory_question_route_versions VALUES(?,?,?,?,?)',('r2',2,'parent','{}',EARLY)),('INSERT INTO memory_run_events VALUES(?,?,?,?,?)',(1,'same-version-run','view','reused',1)),('INSERT INTO memory_run_events VALUES(?,?,?,?,?)',(2,'same-version-run','lifecycle','completed',None))]
  previous=self.source.revision()
  for sql,args in operations:
   with self.subTest(sql=sql):self.write(sql,args);current=self.source.revision();self.assertNotEqual(current,previous);self.assertEqual(current,self.source.revision());previous=current
  self.assertTrue(previous.endswith(':runs:2'));self.assertEqual(self.source._watch.execute('SELECT version FROM memory_state').fetchone()[0],1)
 def test_same_relevant_content_is_stable_across_provider_restart_and_file_replacement_is_detected(self):
  first=self.source.revision();self.source.close();self.source=AssertionMemorySource(self.path);self.assertEqual(self.source.revision(),first)
  replacement=self.path.with_name('replacement.sqlite3')
  with sqlite3.connect(self.path) as original,sqlite3.connect(replacement) as target:original.backup(target)
  replacement.replace(self.path);self.assertNotEqual(self.source.revision(),first)
 def test_all_marks_and_ledger_share_one_wal_read_snapshot(self):
  self.source.close()
  with sqlite3.connect(self.path) as c:c.execute('PRAGMA journal_mode=WAL')
  self.source=AssertionMemorySource(self.path);published=[]
  def concurrent(sql):
   if sql=='SELECT count(*) FROM memory_edges' and not published:
    self.write('INSERT INTO memory_extraction_runs VALUES(?,?,?,?)',(1,'event','{"assertion_ids":[]}',EARLY));published.append(True)
  self.source._watch.set_trace_callback(concurrent);first=self.source.revision();self.source._watch.set_trace_callback(None);self.assertTrue(published)
  second=self.source.revision();self.assertNotEqual(first,second,'ledger committed during mark reads is observed wholly on the next snapshot');self.assertEqual(second,self.source.revision())

class StableManager(unittest.IsolatedAsyncioTestCase):
 async def test_heartbeat_does_not_prepare_another_graph_or_allocate_cache_revision(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);path=root/'source.sqlite3';fixture(path)
   with sqlite3.connect(path) as c:c.execute('CREATE TABLE memory_derived_work(scope TEXT,updated_at TEXT)');c.execute("INSERT INTO memory_derived_work VALUES('s','first')")
   manager=GraphManager(AssertionMemorySource(path),root/'cache');await manager.start();selection={'mode':'current','at':None,'history':False,'undated':'separate'}
   try:
    for _ in range(100):
     old=await manager.ensure(selection)
     if old:break
     await asyncio.sleep(.02)
    self.assertIsNotNone(old);directories=sorted(p.name for p in (root/'cache').iterdir());before=manager.source.revision()
    with patch.object(manager,'refresh',new=AsyncMock(wraps=manager.refresh)) as refresh:
     for index in range(3):
      with sqlite3.connect(path) as c:c.execute('UPDATE memory_derived_work SET updated_at=?',(f'heartbeat-{index}',))
      await asyncio.sleep(.38)
     self.assertEqual(manager.source.revision(),before);self.assertEqual(refresh.await_count,0);self.assertEqual((await manager.ensure(selection)).revision,old.revision);self.assertEqual(sorted(p.name for p in (root/'cache').iterdir()),directories)
   finally:await manager.stop()
