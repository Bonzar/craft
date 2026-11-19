"""Genuine Core reader, byte-exact fixture import; never instantiate a Source writer."""
import hashlib,importlib.util,json,os,sqlite3,unittest
from pathlib import Path
from unittest.mock import patch
import test_session_filter as base_fixture
from apps.graph_viewer.server.providers.causal_results import result_bundle
from apps.graph_viewer.server.providers.assertion_memory_topics import read_pinned_material
from apps.graph_viewer.server.providers.version_identity import topic_version_id,parse_topic_version_id
from apps.graph_viewer.server.common import APIError,selector
from apps.graph_viewer.server.cards import overview_page
from prototypes.assertion_memory import run_provenance as sdk
EARLY=base_fixture.EARLY;TIME='2025-02-01T00:00:00+00:00';CUTOFF='2026-01-01T00:00:00.000000+00:00'

class CausalResults(unittest.TestCase):
 graph=base_fixture.SessionFilter.graph
 def setUp(self):
  base_fixture.SessionFilter.setUp(self)
  with sqlite3.connect(self.db) as c:
   c.row_factory=sqlite3.Row;sdk.schema(c)
   for view,current in [('parent',12),('child',9)]:c.execute('INSERT INTO memory_views VALUES(?,?,?,?,?,?)',(view,json.dumps({'title':view}),current,1,None,None))
   for version,view,upstream,evidence in [(7,'child',{},['a2']),(9,'child',{},['b1']),(12,'parent',{'child':7},['b1']),(13,'parent',{'child':9},[])]:c.execute('INSERT INTO memory_view_versions VALUES(?,?,?,?)',(version,view,json.dumps({'created_at':EARLY,'upstream':upstream,'evidence':[{'id':id} for id in evidence],'summary':f'exact {view} version {version}'}),EARLY))
   self.run_rows(c,'AB',['ea','eb'],[('parent',12,{'child':7}),('child',7,{})])
   self.run_rows(c,'new-child',['ea'],[('child',9,{})])
   self.run_rows(c,'pending',['ea'],[('parent',13,{'child':9})],lifecycle='yielded')
   self.run_rows(c,'caller',[None],[('child',9,{})],kind='direct_request',cause_status='caller_context_only',frontier={'request_session_id':'chat-A'})
  self.sha=hashlib.sha256(self.db.read_bytes()).hexdigest();self.mock=patch('apps.graph_viewer.server.providers.causal_results._sdk',return_value=sdk);self.mock.start()
 def tearDown(self):self.mock.stop();base_fixture.SessionFilter.tearDown(self)
 def run_rows(self,c,id,causes,outputs,lifecycle='completed',kind='derived_scope',cause_status='complete',frontier=None):
  c.execute('INSERT INTO memory_run_provenance VALUES(?,?,?,?,?,?,?,?)',(id,kind,'owner','s',TIME,cause_status,json.dumps(frontier or {}),'{}'))
  for event in causes:
   cause=c.execute('INSERT INTO memory_run_causes(scope,generation_from,generation_to,source_event_id,job_id,recorded_at) VALUES(?,?,?,?,?,?)',('s',0,1,event,id,TIME)).lastrowid;c.execute('INSERT INTO memory_run_cause_links VALUES(?,?)',(id,cause))
  sdk.outcome(c,id,'started',recorded_at=TIME)
  for view,version,upstream in outputs:c.execute('INSERT INTO memory_run_events(run_id,kind,outcome,recorded_at,view_id,version_id,check_id,upstream,review_status,failure_proof) VALUES(?,?,?,?,?,?,?,?,?,?)',(id,'view','produced',TIME,view,version,version+100,json.dumps(upstream),'accepted',0))
  sdk.outcome(c,id,lifecycle,recorded_at=TIME)
 def bundle(self,session='native:chat-A',**options):
  with self.source.connect() as c:return result_bundle(c,session,CUTOFF,sdk=sdk,read_material=lambda view,version:read_pinned_material(c,view,version,CUTOFF),**options)
 def test_compact_completed_union_retains_exact_old_input_all_cocauses_and_pending_diagnostics(self):
  value=self.bundle();pins={(p['view_id'],p['version_id']):p for p in value['pins']};self.assertEqual(set(pins),{('parent',12),('child',9),('child',7)});self.assertEqual(pins['child',7]['role'],'input');self.assertEqual(pins['parent',12]['upstream'],[{'view_id':'child','version_id':7,'topic_id':topic_version_id('child',7)}]);self.assertEqual(pins['parent',12]['cause_sessions'],['native:chat-A','native:chat-B']);self.assertEqual(value['history']['pending_output_count'],1);self.assertEqual(value['history']['caller_context_output_count'],1);self.assertFalse(value['history']['history_complete']);self.assertEqual(value['history']['reason'],'recorded_runs_only')
  other=self.bundle('native:chat-B');self.assertEqual({(p['view_id'],p['version_id']) for p in other['pins']},{('parent',12),('child',7)})
 def test_selected_completed_run_and_old_watermark_are_coherent_and_pending_is_not_finished(self):
  selected=self.bundle(run_id='AB');self.assertEqual({(p['view_id'],p['version_id']) for p in selected['pins']},{('parent',12),('child',7)});self.assertTrue(all(p['role']=='output' for p in selected['pins']))
  with self.assertRaises(APIError):self.bundle(run_id='pending')
  with sqlite3.connect(self.db) as c:
   watermark=c.execute("SELECT max(id) FROM memory_run_events WHERE run_id='AB'").fetchone()[0];sdk.outcome(c,'AB','failed',recorded_at=TIME)
  self.sha=hashlib.sha256(self.db.read_bytes()).hexdigest();self.assertFalse(any(p['view_id']=='parent' for p in self.bundle()['pins']));self.assertTrue(any(p['view_id']=='parent' for p in self.bundle(snapshot_id=watermark)['pins']))
 def test_native_and_fallback_namespaces_do_not_collide(self):
  with sqlite3.connect(self.db) as c:
   for id,logical,metadata in [('native-same','other',{'native_session_id':'same'}),('logical-same','same',{})]:c.execute('INSERT INTO memory_raw_events VALUES(?,?,?,?,?,?,?,?,?)',(id,logical,'turn','source','actor','source','s',EARLY,json.dumps(metadata)))
   self.run_rows(c,'native-run',['native-same'],[('child',7,{})]);self.run_rows(c,'logical-run',['logical-same'],[('child',9,{})])
  self.sha=hashlib.sha256(self.db.read_bytes()).hexdigest();self.assertEqual([p['version_id'] for p in self.bundle('native:same')['pins']],[7]);self.assertEqual([p['version_id'] for p in self.bundle('logical:same')['pins']],[9])
 def test_default_projection_never_promotes_legacy_jobs_and_missing_reader_is_explicit(self):
  with sqlite3.connect(self.db) as c:
   c.execute("CREATE TABLE memory_capture_jobs(id TEXT,event_id TEXT,assertion_id TEXT,status TEXT,phase TEXT,updated_at TEXT,refreshed_views TEXT,error TEXT)");c.execute("INSERT INTO memory_capture_jobs VALUES('legacy','ea',NULL,'completed','done',?, ?,NULL)",(TIME,json.dumps({'parent':13})))
  self.sha=hashlib.sha256(self.db.read_bytes()).hexdigest()
  with patch('apps.graph_viewer.server.providers.causal_results._sdk',side_effect=ImportError('absent')):
   graph=self.graph('native:chat-A',True);self.assertEqual(graph.topics()['topics'],[]);self.assertEqual(graph.manifest['session_filter']['overview_history']['reason'],'reader_unavailable')
 def test_version_qualified_cards_preserve_two_versions_and_full_family_xyz(self):
  base=self.graph(history=True);selected=self.graph('native:chat-A',True);topics=selected.topics()['topics'];self.assertEqual(len({t['id'] for t in topics}),3)
  for version in[7,9]:
   id=topic_version_id('child',version);self.assertTrue(self.source.has_object('topic',id))
   with selected.connect() as c:self.assertEqual(overview_page(c,selected,id,{})['text'],f'exact child version {version}')
  with selected.connect() as c:
   with self.assertRaises(APIError):overview_page(c,selected,'topic:child',{})
   self.assertTrue(json.loads(c.execute('SELECT body FROM session_result_bundle').fetchone()[0])['runs'])
  with base.connect() as a,selected.connect() as b:self.assertEqual([tuple(r) for r in a.execute('SELECT ordinal,id,x,y,z FROM nodes')],[tuple(r) for r in b.execute('SELECT ordinal,id,x,y,z FROM nodes')])
 def test_bounded_sdk_pages_decode_all_reuse_occurrences_and_large_event_details(self):
  with sqlite3.connect(self.db) as c:
   for i in range(105):c.execute('INSERT INTO memory_run_events(run_id,kind,outcome,recorded_at,view_id,version_id,check_id,upstream,review_status,failure_proof) VALUES(?,?,?,?,?,?,?,?,?,?)',('AB','view','reused',TIME,'child',7,200+i,'{}','long'*2000 if i==0 else 'accepted',0))
   sdk.outcome(c,'AB','completed',recorded_at=TIME)
  self.sha=hashlib.sha256(self.db.read_bytes()).hexdigest();value=self.bundle(run_id='AB');pin=next(p for p in value['pins'] if p['view_id']=='child');self.assertEqual(len(pin['occurrences']),106);self.assertEqual(value['history']['runs'][0]['output_count'],107);self.assertEqual(next(p for p in self.bundle()['pins'] if p['view_id']=='child' and p['role']=='output')['version_id'],7,'latest completed reuse event wins over a numerically newer version')
 def test_selector_and_transport_identity_are_exact_and_canonical(self):
  value=topic_version_id('a/ю?[view]',7);self.assertEqual(parse_topic_version_id(value),('a/ю?[view]',7));self.assertIsNone(parse_topic_version_id(value+'='));self.assertNotEqual(value,topic_version_id('a/ю?[view]',9));self.assertEqual(selector({'session_id':'native:chat-A','run_id':'AB','output_snapshot_id':'4'})['output_snapshot_id'],4)
  for params in[{'run_id':'AB'},{'output_snapshot_id':1},{'session_id':'native:chat-A','output_snapshot_id':True},{'session_id':'native:chat-A','output_snapshot_id':1.5}]:
   with self.assertRaises(APIError):selector(params)

 def test_selected_run_keeps_multiple_versions_of_one_view_and_exact_occurrences(self):
  with sqlite3.connect(self.db) as c:
   c.execute('INSERT INTO memory_run_events(run_id,kind,outcome,recorded_at,view_id,version_id,check_id,upstream,review_status,failure_proof) VALUES(?,?,?,?,?,?,?,?,?,?)',('AB','view','reused',TIME,'child',9,109,'{}','accepted',0));sdk.outcome(c,'AB','completed',recorded_at=TIME)
  self.sha=hashlib.sha256(self.db.read_bytes()).hexdigest();value=self.bundle(run_id='AB');self.assertEqual({(p['view_id'],p['version_id']) for p in value['pins']},{('parent',12),('child',7),('child',9)});self.assertTrue(all(p['role']=='output' for p in value['pins']))
 def test_recursive_exact_inputs_and_partial_cause_coverage_are_explicit(self):
  with sqlite3.connect(self.db) as c:
   for view,version,upstream in [('top',20,{'middle':21}),('middle',21,{'leaf':22}),('leaf',22,{})]:
    c.execute('INSERT INTO memory_views VALUES(?,?,?,?,?,?)',(view,json.dumps({'title':view}),version,1,None,None));c.execute('INSERT INTO memory_view_versions VALUES(?,?,?,?)',(version,view,json.dumps({'created_at':EARLY,'summary':view,'evidence':[],'upstream':upstream}),EARLY))
   self.run_rows(c,'partial-empty',['empty',None],[('top',20,{'middle':21})],cause_status='partial')
  self.sha=hashlib.sha256(self.db.read_bytes()).hexdigest();value=self.bundle('native:empty-chat');pins={(p['view_id'],p['version_id']):p for p in value['pins']};self.assertEqual(set(pins),{('top',20),('middle',21),('leaf',22)});self.assertEqual(pins['top',20]['cause_status'],'partial');self.assertEqual(pins['middle',21]['role'],'input');self.assertEqual(pins['middle',21]['upstream'][0]['version_id'],22);self.assertIsNone(pins['leaf',22]['event_id']);self.assertEqual(len(value['runs'][0]['causes']),2)

 def test_pinned_event_check_labels_are_preserved_without_acceptance_from_a_different_current_check(self):
  with sqlite3.connect(self.db) as c:
   c.execute('INSERT INTO memory_run_events(run_id,kind,outcome,recorded_at,view_id,version_id,check_id,upstream,review_status,failure_proof) VALUES(?,?,?,?,?,?,?,?,?,?)',('AB','view','reused',TIME,'child',7,777,'{}','unresolved',1));sdk.outcome(c,'AB','completed',recorded_at=TIME)
  self.sha=hashlib.sha256(self.db.read_bytes()).hexdigest();graph=self.graph('native:chat-A',True,run_id='AB');topic=next(t for t in graph.topics()['topics'] if t['source_ref']=={'view_id':'child','version_id':7,'route_revision':None});pin=topic['session_result'];self.assertEqual(pin['check_id'],777);self.assertEqual(pin['review_status'],'unresolved');self.assertTrue(pin['failure_proof']);self.assertIsNone(topic['accepted_at_that_snapshot']);self.assertTrue(any(value['check_id']==777 for value in pin['occurrences']))
  with graph.connect() as c:
   first=overview_page(c,graph,topic['id'],{'limit':'5'});second=overview_page(c,graph,topic['id'],{'offset':'5','limit':'5'});self.assertEqual(first['session_result'],pin);self.assertEqual(second['session_result'],pin);self.assertEqual(first['slice_member_count'],topic['slice_member_count']);self.assertEqual(first['unavailable_member_count'],topic['unavailable_member_count'])

 def test_bookmark_retains_run_watermark_and_zero_selected_result_availability(self):
  import asyncio
  from apps.graph_viewer.server.auth import validate_bookmark
  from apps.graph_viewer.server.bookmarks import resolve_bookmarks
  graph=self.graph('native:chat-A',True,run_id='AB',output_snapshot_id='4');id=topic_version_id('parent',12);selection=graph.manifest['selector'];clean=validate_bookmark({'provider_id':self.source.provider_id,'object_kind':'topic','object_id':id,'selector':selection});self.assertEqual(clean['selector']['run_id'],'AB');self.assertEqual(clean['selector']['output_snapshot_id'],4)
  class Manager:
   available=True
   source=self.source
   async def ensure(self,value):return graph
  item={**clean,'availability':'unknown'};result=asyncio.run(resolve_bookmarks(Manager(),{'items':[item]}));self.assertEqual(result['items'][0]['availability'],'available');self.assertEqual(next(t for t in graph.topics()['topics'] if t['id']==id)['slice_member_count'],0)

 def test_causal_only_reuse_advances_dynamic_projection_without_changing_canonical_state_and_fixed_watermark_stays_pinned(self):
  import asyncio
  from apps.graph_viewer.server.manager import GraphManager
  dynamic=self.graph('native:chat-A',True);fixed=self.graph('native:chat-A',True,run_id='AB',output_snapshot_id='4');before=self.source.revision()
  with sqlite3.connect(self.db) as c:
   state=c.execute('SELECT version FROM memory_state').fetchone()[0];count=c.execute('SELECT count(*) FROM memory_view_versions').fetchone()[0]
   c.execute('INSERT INTO memory_run_events(run_id,kind,outcome,recorded_at,view_id,version_id,check_id,upstream,review_status,failure_proof) VALUES(?,?,?,?,?,?,?,?,?,?)',('AB','view','reused',TIME,'child',7,999,'{}','accepted',0));sdk.outcome(c,'AB','completed',recorded_at=TIME)
   self.assertEqual(c.execute('SELECT version FROM memory_state').fetchone()[0],state);self.assertEqual(c.execute('SELECT count(*) FROM memory_view_versions').fetchone()[0],count)
  self.sha=hashlib.sha256(self.db.read_bytes()).hexdigest();after=self.source.revision();self.assertNotEqual(before,after);self.assertIn(':runs:',after)
  manager=GraphManager(self.source,self.root/'cache')
  for old in [dynamic,fixed]:manager.graphs[old.revision]=old;manager.latest[manager.key(old.manifest['selector'])]=old.revision
  async def refresh():
   await manager.refresh(dynamic.manifest['selector'],manager.key(dynamic.manifest['selector']),dynamic.revision)
   await manager.refresh(fixed.manifest['selector'],manager.key(fixed.manifest['selector']),fixed.revision)
  asyncio.run(refresh());new=manager.graphs[manager.latest[manager.key(dynamic.manifest['selector'])]];still=manager.graphs[manager.latest[manager.key(fixed.manifest['selector'])]]
  self.assertGreater(new.manifest['session_filter']['overview_history']['snapshot_id'],dynamic.manifest['session_filter']['overview_history']['snapshot_id']);self.assertEqual(still.manifest['session_filter']['overview_history']['snapshot_id'],4);self.assertEqual(still.topics()['topics'],fixed.topics()['topics']);self.assertEqual(new.manifest['preparation']['causal_output_snapshot_id'],new.manifest['session_filter']['overview_history']['snapshot_id'])
  with dynamic.connect() as a,new.connect() as b:self.assertEqual([tuple(r) for r in a.execute('SELECT ordinal,id,x,y,z FROM nodes')],[tuple(r) for r in b.execute('SELECT ordinal,id,x,y,z FROM nodes')])

 def test_completed_request_context_is_readable_with_exact_metadata_without_propagating_assertion_origin(self):
  value=self.bundle(run_id='caller');self.assertEqual(len(value['pins']),1);pin=value['pins'][0];self.assertEqual((pin['view_id'],pin['version_id']),('child',9));self.assertEqual(pin['attribution'],'request_context');self.assertEqual(pin['cause_status'],'caller_context_only');self.assertEqual(pin['cause_sessions'],[]);self.assertEqual(pin['check_id'],109);self.assertEqual(pin['lifecycle'],'completed')
  source=self.graph('native:chat-A',True,run_id='AB');caller=self.graph('native:chat-A',True,run_id='caller')
  with source.connect() as a,caller.connect() as b:
   self.assertEqual([tuple(r) for r in a.execute('SELECT ordinal,id,x,y,z,flags FROM nodes')],[tuple(r) for r in b.execute('SELECT ordinal,id,x,y,z,flags FROM nodes')]);self.assertEqual([tuple(r) for r in a.execute('SELECT * FROM node_sessions')],[tuple(r) for r in b.execute('SELECT * FROM node_sessions')]);self.assertEqual({r[0] for r in b.execute('SELECT id FROM nodes WHERE flags&4=0')},{'a1','a2','cancel'})
   page=overview_page(b,caller,topic_version_id('child',9),{});self.assertEqual(page['text'],'exact child version 9');self.assertEqual(page['session_result'],{key:value for key,value in pin.items() if key!='view_id'})
 def test_latest_caller_occurrence_preserves_all_source_cocauses_of_the_same_immutable_pair(self):
  with sqlite3.connect(self.db) as c:self.run_rows(c,'caller-parent',[None],[('parent',12,{'child':7})],kind='direct_request',cause_status='caller_context_only',frontier={'request_session_id':'chat-A'})
  self.sha=hashlib.sha256(self.db.read_bytes()).hexdigest();value=self.bundle();pin=next(p for p in value['pins'] if (p['view_id'],p['version_id'])==('parent',12));self.assertEqual(pin['attribution'],'request_context');self.assertEqual(pin['cause_sessions'],[])
  source=next(o for o in pin['occurrences'] if o['run_id']=='AB');request=next(o for o in pin['occurrences'] if o['run_id']=='caller-parent');self.assertEqual(source['attribution'],'source_cause');self.assertEqual(source['cause_sessions'],['native:chat-A','native:chat-B']);self.assertEqual(request['attribution'],'request_context');self.assertEqual(request['cause_sessions'],[])
  other=next(p for p in self.bundle('native:chat-B')['pins'] if p['view_id']=='parent');self.assertEqual(other['attribution'],'source_cause');self.assertEqual(other['cause_sessions'],['native:chat-A','native:chat-B'])
 def test_failed_request_outputs_remain_diagnostics_and_namespace_ambiguity_never_creates_source_roots(self):
  with sqlite3.connect(self.db) as c:
   c.execute('INSERT INTO memory_raw_events VALUES(?,?,?,?,?,?,?,?,?)',('same-logical','chat-A','turn','source','actor','source','s',EARLY,'{}'))
   self.run_rows(c,'caller-failed',[None],[('parent',13,{'child':9})],lifecycle='failed',kind='direct_request',cause_status='caller_context_only',frontier={'request_session_id':'chat-A'})
  self.sha=hashlib.sha256(self.db.read_bytes()).hexdigest();value=self.bundle();self.assertFalse(any(p['version_id']==13 for p in value['pins']));failed=next(run for run in value['history']['runs'] if run['run_id']=='caller-failed');self.assertEqual(failed['lifecycle'],'failed');self.assertEqual(failed['attribution'],'request_context');self.assertEqual(value['history']['pending_output_count'],2)
  with self.assertRaises(APIError):self.bundle(run_id='caller-failed')
  for selected in ['native:chat-A','logical:chat-A']:
   pins=self.bundle(selected,run_id='caller')['pins'];self.assertEqual(pins[0]['attribution'],'request_context');self.assertEqual(pins[0]['cause_sessions'],[])
  graph=self.graph('logical:chat-A',True,run_id='caller');self.assertEqual(graph.manifest['counts']['visible_nodes'],0);self.assertEqual(len(graph.topics()['topics']),1)
