import ast,hashlib,json,sqlite3,struct,tempfile,time,unittest,os
from pathlib import Path
from unittest.mock import patch
from starlette.testclient import TestClient
from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.server.common import APIError,selector
from apps.graph_viewer.server.projection import prepare
from apps.graph_viewer.server.providers.assertion_memory import AssertionMemorySource
from apps.graph_viewer.server.sessions import session_page
from apps.graph_viewer.server.cards import node_row,node_card,edge_card,incident
from apps.graph_viewer.server.search import search
from apps.graph_viewer.server.incremental import incremental
ROOT=Path(os.environ.get('GRAPH_VIEWER_TEST_ROOT',Path(__file__).resolve().parents[4]))
EARLY='2025-01-01T00:00:00+00:00'
SCHEMA=next(ast.literal_eval(n.value) for n in ast.parse((ROOT/'apps/graph_viewer/tests/backend/test_service.py').read_text()).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SCHEMA' for t in n.targets))

def fixture(path):
 with sqlite3.connect(path) as c:
  c.executescript(SCHEMA)
  c.execute('CREATE TABLE memory_extraction_runs(id INTEGER PRIMARY KEY,event_id TEXT,result TEXT,completed_at TEXT)')
  for event,logical,native,recorded in [('ea','same-ingestion','chat-A',EARLY),('eb','same-ingestion','chat-B',EARLY),('ec','logical-only',None,EARLY),('empty','same-ingestion','empty-chat',EARLY),('future','same-ingestion','future-chat','2099-01-01T00:00:00+00:00')]:
   c.execute('INSERT INTO memory_raw_events VALUES(?,?,?,?,?,?,?,?,?)',(event,logical,'turn','original source text','actor','saved','s',recorded,json.dumps({'native_session_id':native} if native else {})))
  for key,event,extra in [('a1','ea',{'kind':'decision'}),('a2','ea',{}),('cancel','ea',{'viewer_status':{'published':True,'disposition':'cancelled'}}),('b1','eb',{'kind':'decision'}),('c1','ec',{}),('unknown','ea',{})]:
   # B deliberately claims A in incidental metadata; ledger remains authoritative.
   meta={'event_id':'ea','source_span':{'start':0,'end':4},**extra}
   c.execute('INSERT INTO memory_assertions VALUES(?,?,?,?,?,?,?,?,?)',(key,'exact text '+key,'actor','saved','same-ingestion',EARLY,EARLY,'s',json.dumps(meta)))
  for event,ids in [('ea',['a1','a2','cancel']),('eb',['b1']),('ec',['c1'])]:
   c.execute('INSERT INTO memory_extraction_runs(event_id,result,completed_at) VALUES(?,?,?)',(event,json.dumps({'assertion_ids':ids}),EARLY))
  for key,a,b,kind in [('replacement','b1','a1','supersedes'),('local','a2','a1','supports'),('cross','a2','b1','supports'),('self','a2','a2','supports')]:
   c.execute('INSERT INTO memory_edges VALUES(?,?,?,?,?,?)',(key,a,b,kind,EARLY,'{}'))
  snapshot={'evidence':[{'id':key} for key in ['a1','a2','cancel','b1','c1','unknown']],'summary':'Original mixed-session overview text'}
  c.execute('INSERT INTO memory_views VALUES(?,?,?,?,?,?)',('mixed',json.dumps({'title':'Mixed source material'}),1,1,None,1))
  c.execute('INSERT INTO memory_view_versions VALUES(?,?,?,?)',(1,'mixed',json.dumps(snapshot),EARLY))
  c.execute('INSERT INTO memory_view_checks VALUES(?,?,?,?,?,?)',(1,'mixed',1,1,EARLY,'current'))

class SessionFilter(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=self.root/'source.sqlite3';fixture(self.db);self.sha=hashlib.sha256(self.db.read_bytes()).hexdigest();self.source=AssertionMemorySource(self.db)
 def tearDown(self):
  self.source.close();self.assertEqual(self.sha,hashlib.sha256(self.db.read_bytes()).hexdigest());self.tmp.cleanup()
 def graph(self,session=None,history=False,**extra):
  return prepare(self.source,self.root/'cache',selector(dict(extra,history='1' if history else '0',**({'session_id':session} if session else {}))))
 def test_native_catalog_uses_owner_ledger_and_separates_shared_logical_ingestion(self):
  graph=self.graph();page=session_page(graph,{})
  self.assertEqual({r['id'] for r in page['items']},{'native:chat-A','native:chat-B','native:empty-chat','logical:logical-only'})
  self.assertEqual(page['unknown_origin_assertion_count'],1)
  by={r['id']:r for r in page['items']};self.assertEqual(by['native:chat-A']['assertion_count'],1);self.assertEqual(by['native:chat-B']['assertion_count'],1);self.assertEqual(by['native:empty-chat']['assertion_count'],0)
  self.assertTrue(all(r['display_title'] is None for r in page['items']))
  first=session_page(graph,{'limit':'2'});second=session_page(graph,{'limit':'2','cursor':first['next_cursor']});self.assertEqual(len(first['items']+second['items']),4)
  with self.assertRaises(APIError):session_page(graph,{'limit':'2','query':'A','cursor':first['next_cursor']})
 def test_filter_preserves_full_identity_status_membership_and_text_but_induces_visible_edges(self):
  base=self.graph(history=True);selected=self.graph('native:chat-A',True)
  with base.connect() as a,selected.connect() as b:
   self.assertEqual([tuple(r) for r in a.execute('SELECT ordinal,id,text,x,y,z,disposition FROM nodes')],[tuple(r) for r in b.execute('SELECT ordinal,id,text,x,y,z,disposition FROM nodes')])
   self.assertEqual([tuple(r) for r in a.execute('SELECT * FROM edges')],[tuple(r) for r in b.execute('SELECT * FROM edges')]);self.assertEqual(base.topics()['memberships'],selected.topics()['layout_memberships']);self.assertEqual([tuple(r) for r in b.execute('SELECT * FROM memberships')],[])
   self.assertEqual(b.execute("SELECT flags&3 FROM nodes WHERE id='a1'").fetchone()[0],1,'replacement outside selected session still determines global status')
   self.assertEqual({r['id'] for r in b.execute('SELECT id FROM nodes WHERE flags&4=0')},{'a1','a2','cancel'})
   with self.assertRaises(APIError):node_row(b,id='b1')
   with self.assertRaises(APIError):edge_card(b,selected,id='cross')
   card=node_card(b,selected,node_row(b,id='a1'),{});self.assertIn('b1',card['status']['replacements'])
   edges=incident(b,selected,node_row(b,id='a2'),{'visible':'0'});self.assertEqual({e['id'] for e in edges['items']},{'local','self'})
  self.assertEqual(selected.topics()['topics'],[]);self.assertEqual(selected.manifest['session_filter']['overview_history']['state'],'unknown');self.assertFalse(selected.manifest['session_filter']['overview_history']['history_complete'])
  self.assertEqual(selected.manifest['counts']['eligible_before_status_filter'],3);self.assertEqual(selected.manifest['counts']['visible_edges'],2)
  self.assertEqual({r['id'] for r in search(selected,{'q':'exact','limit':'50'})['hits']},{'a1','a2','cancel'})
 def test_empty_unknown_history_and_selector_identity(self):
  empty=self.graph('native:empty-chat');self.assertEqual(empty.manifest['counts']['visible_nodes'],0);self.assertEqual(empty.manifest['counts']['eligible_before_status_filter'],0);self.assertEqual(len(session_page(empty,{})['items']),4)
  current=self.graph('native:chat-A');self.assertEqual(current.manifest['counts']['visible_nodes'],1);self.assertEqual(current.manifest['counts']['visible_edges'],1)
  self.assertIsNone(incremental(self.source,current,self.root/'cache',current.manifest['selector']))
  with self.assertRaises(APIError) as failure:self.graph('native:missing')
  self.assertEqual((failure.exception.code,failure.exception.status),('session_unavailable',409))
  with self.assertRaises(APIError):selector({'session_id':'chat-A'})
  self.assertEqual(selector({}),selector({'session_id':None}))
 def test_public_route_auth_and_typed_prepare_error(self):
  with TestClient(create_app(None,self.root/'api',password='owner-test-strong',source=self.source)) as client:
   self.assertEqual(client.post('/api/v1/session',json={'password':'owner-test-strong'}).status_code,200)
   self.assertTrue(client.get('/api/v1/capabilities').json()['capabilities']['session_filter'])
   def graph(query):
    for _ in range(100):
     response=client.get('/api/v1/graph',params=query)
     if response.status_code!=202:return response
     time.sleep(.02)
    self.fail('prepare did not finish')
   response=graph({'session_id':'native:chat-B'});self.assertEqual(response.status_code,200,response.text);revision=response.json()['revision']
   page=client.get('/api/v1/graph/'+revision+'/sessions');self.assertEqual(page.status_code,200);self.assertEqual(len(page.json()['items']),4)
   self.assertEqual(client.get('/api/v1/nodes/a2',params={'revision':revision}).status_code,404)
   failure=graph({'session_id':'native:missing'});self.assertEqual(failure.status_code,409,failure.text);self.assertEqual(failure.json()['error']['code'],'session_unavailable')


 def test_cutoff_and_semantic_reuse_never_run_a_model(self):
  from apps.graph_viewer.server.manager import GraphManager
  historical=self.graph('native:empty-chat',mode='known_then',at='2025-02-01T00:00:00+00:00')
  self.assertEqual(historical.manifest['counts']['visible_nodes'],0)
  with self.assertRaises(APIError):self.graph('native:empty-chat',mode='known_then',at='2024-12-01T00:00:00+00:00')
  parent=self.graph();child=self.graph('native:chat-A');calls=[]
  def model(*args):calls.append(args);raise AssertionError('filter must not run model')
  parent.semantic_matrix=object();parent.semantic_model=model;parent.semantic_info={'status':'ready'}
  manager=GraphManager(self.source,self.root/'cache',semantic_model=model);manager.graphs[parent.revision]=parent
  manager.schedule_semantic(child)  # reset fence rejects candidates not registered in this epoch
  self.assertFalse(hasattr(child,'semantic_matrix'))
  manager.graphs[child.revision]=child
  manager.schedule_semantic(child)
  self.assertIs(child.semantic_matrix,parent.semantic_matrix);self.assertTrue(child.manifest['session_filter']['semantic_search_available']);self.assertEqual(calls,[])
 def test_json_session_uses_explicit_primary_episode_not_incidental_evidence(self):
  from apps.graph_viewer.server.providers.json_snapshot import JsonSnapshotSource
  doc=json.loads((ROOT/'apps/graph_viewer/tests/fixtures/independent-source.json').read_text());doc['episodes'][0]['session_id']='JSON-A';doc['episodes'][1]['session_id']='JSON-B'
  # A reference is not a primary origin. The foreign source declares episode_id.
  literal=next(n for n in doc['nodes'] if n['id']=='literal');literal['evidence_refs']=[{'episode_id':'extra','start':0,'end':1}]
  path=self.root/'foreign.json';path.write_text(json.dumps(doc));source=JsonSnapshotSource(path,self.root/'foreign-state')
  original=__import__
  def no_engine(name,*args,**kwargs):
   if name.startswith('prototypes.assertion_memory'):raise AssertionError('JSON source must remain independent')
   return original(name,*args,**kwargs)
  try:
   with patch('builtins.__import__',side_effect=no_engine):graph=prepare(source,self.root/'foreign-cache',selector({'session_id':'logical:JSON-A','history':'1'}))
   with graph.connect() as c:self.assertEqual({r[0] for r in c.execute('SELECT id FROM nodes WHERE flags&4=0')},{'old'})
   self.assertEqual({r['id'] for r in session_page(graph,{})['items']},{'logical:JSON-A','logical:JSON-B'})
  finally:source.close()

if __name__=='__main__':unittest.main()
