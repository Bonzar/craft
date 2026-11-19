"""Routing authority and cache migration, entirely fictional query-only SQLite."""
import asyncio
import hashlib
import json
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from apps.graph_viewer.server.providers import assertion_memory_topics as topics
from apps.graph_viewer.server.incremental import incremental, updated_manifest
from apps.graph_viewer.server.manager import GraphManager
from apps.graph_viewer.server.layout import VERSION
from apps.graph_viewer.server.providers.base import SOURCE_MEMBERSHIP_POLICY
from prototypes.assertion_memory.background_overviews import POLICY, PURPOSE, _RECORDED_AUTOMATIC_PURPOSES
from prototypes.assertion_memory.question_routes import PLAN_PROTOCOL

EARLY='2025-01-01T00:00:00.000000+00:00'
def sha(body,n):return hashlib.sha256(json.dumps(body,ensure_ascii=False).encode()).hexdigest()[:n]
def fixture(purpose=PURPOSE):
 c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
 c.executescript('CREATE TABLE memory_state(version);INSERT INTO memory_state VALUES(1);CREATE TABLE memory_assertions(assertion_id,scope);CREATE TABLE memory_views(id,definition,current_id,checked_version);CREATE TABLE memory_view_versions(id,view_id,snapshot,created_at);CREATE TABLE memory_question_route_versions(request_id,revision,view_id,plan,recorded_at);')
 for key in ['a','b','root-only']:c.execute('INSERT INTO memory_assertions VALUES(?,?)',(key,'s'))
 themes=[]
 for question,members in [('Conditions?',['a','b']),('Exceptions?',['b'])]:
  key='theme-'+sha([question,('s',),members],32);themes.append({'view_id':key,'question':question,'source_ids':members})
 request='scope-overview-'+sha([POLICY,'s','pipeline'],32)
 plan={'automatic':{'scope':'s','policy':POLICY,'root_request_id':request},'protocol':PLAN_PROTOCOL,'method':'model','reuse_view_id':None,'request':{'question':purpose,'scopes':['s']},'depends_on':[v['view_id'] for v in themes],'related_view_ids':[],'answer_metadata_requests':[],'subquestions':[]}
 plan['discovery']={'request':plan['request'],'pipeline_key':'pipeline','themes':themes,'hierarchy':[],'unassigned_source_ids':['root-only'],'corpus_revision':1,'source_fingerprints':{key:'fingerprint' for key in ['a','b','root-only']},'source_count':3,'snapshot':{}}
 root=request+'-plan-'+sha([request,purpose,('s',),tuple(plan['depends_on']),[],PLAN_PROTOCOL,[],[],('root-only',),1,'pipeline'],24)
 entries=[(root,purpose,['root-only'],plan['depends_on']),(request,purpose,[],[]),('ordinary',purpose,['a'],[])] + [(v['view_id'],v['question'],v['source_ids'],[]) for v in themes]
 for i,(key,query,members,deps) in enumerate(entries,1):
  definition={'id':key,'title':'Shared scope overview','query':query,'scopes':['s'],'root_ids':members,'depends_on':deps,'max_seeds':8}
  c.execute('INSERT INTO memory_views VALUES(?,?,?,1)',(key,json.dumps(definition),i))
  c.execute('INSERT INTO memory_view_versions VALUES(?,?,?,?)',(i,key,json.dumps({'created_at':EARLY,'evidence':[{'id':v} for v in members],'summary':'Stored historical summary'}),EARLY))
 c.execute('INSERT INTO memory_question_route_versions VALUES(?,1,?,?,?)',(request,root,json.dumps(plan),EARLY));c.commit()
 return c,root,request,themes

def publish(c,mode='current'):
 d=sqlite3.connect(':memory:');d.executescript('CREATE TABLE memberships(topic,node,PRIMARY KEY(topic,node));CREATE TABLE overviews(topic,id,text,text_revision);')
 try:return topics.publish_topics(c,d,{'a':0,'b':1,'root-only':2},{'mode':mode,'at':EARLY if mode!='current' else None,'history':True,'undated':'separate'})
 finally:d.close()

class RoutingProjection(unittest.TestCase):
 def test_verified_roots_only_current_and_memberships_remain_exact(self):
  for purpose in _RECORDED_AUTOMATIC_PURPOSES:
   c,root,request,themes=fixture(purpose);before=list(c.iterdump());c.execute('PRAGMA query_only=ON')
   current=publish(c);ids={t['id'] for t in current['topics']}
   self.assertNotIn('topic:'+root,ids);self.assertNotIn('topic:'+request,ids)
   self.assertIn('topic:ordinary',ids)
   self.assertEqual([t['index'] for t in current['topics']],list(range(3)))
   memberships={t['id']:current['memberships'][t['index']]['node_indices'] for t in current['topics']}
   self.assertEqual(memberships['topic:'+themes[0]['view_id']],[0,1]);self.assertEqual(memberships['topic:'+themes[1]['view_id']],[1])
   for mode in ['known_then','known_now']:
    historical=publish(c,mode);self.assertEqual(len(historical['topics']),5);self.assertIn('topic:'+root,{t['id'] for t in historical['topics']})
   self.assertEqual(list(c.iterdump()),before)
 def test_invalid_registry_cannot_hide_ordinary_or_technical_views(self):
  for field in ['policy','protocol','request','source','materialization','theme']:
   c,root,request,themes=fixture();row=c.execute('SELECT plan FROM memory_question_route_versions').fetchone();p=json.loads(row[0])
   if field=='policy':p['automatic']['policy']='invalid'
   if field=='protocol':p['protocol']='invalid'
   if field=='request':p['discovery']['request']={'question':'different','scopes':['s']}
   if field=='source':c.execute("UPDATE memory_assertions SET scope='other'")
   if field=='materialization':c.execute("UPDATE memory_question_route_versions SET view_id='wrong'")
   if field=='theme':p['discovery']['themes'][0]['question']='wrong'
   c.execute('UPDATE memory_question_route_versions SET plan=?',(json.dumps(p),))
   self.assertEqual(len(publish(c)['topics']),5,field)
 def test_incremental_rejects_legacy_policy_before_reading_source(self):
  old=SimpleNamespace(manifest={'preparation':{'source_membership_policy':SOURCE_MEMBERSHIP_POLICY},'layout':{'version':VERSION}},watermarks={'assertion_membership':'test'})
  self.assertIsNone(incremental(None,old,None,{'mode':'current'}))
 def test_policy_persists_in_incremental_manifest(self):
  old=SimpleNamespace(manifest={'preparation':{'topic_projection_policy':'verified-domain-topics-v1'},'nodes':{'stride':16},'edges':{'stride':8},'selector':{'mode':'current'}})
  new=updated_manifest(old,'r',EARLY,'s',{'nodes':1,'edges':0},0)
  self.assertEqual(new['preparation'].get('topic_projection_policy'),'verified-domain-topics-v1')
 def test_poll_rebuilds_unchanged_source_legacy_topic_policy(self):
  async def run():
   source=SimpleNamespace(revision=lambda:'same');m=GraphManager(source,None);selection={'mode':'current'};key=m.key(selection)
   m.latest[key]='old';m.graphs['old']=SimpleNamespace(manifest={'source_revision':'same','layout':{'version':VERSION},'preparation':{'source_membership_policy':SOURCE_MEMBERSHIP_POLICY}},watermarks={'assertion_membership':'test'})
   refreshed=[]
   async def refresh(*args):refreshed.append(args);m.closed=True
   async def sleep(*args):
    if m.preparing:m.closed=True
   m.refresh=refresh
   with patch('apps.graph_viewer.server.manager.asyncio.sleep',sleep):
    task=asyncio.create_task(m.poll())
    # Yield using the original loop rather than the patched sleep.
    for _ in range(5):await asyncio.get_running_loop().run_in_executor(None,lambda:None)
    m.closed=True;await task
    await asyncio.gather(*m.preparing.values())
   self.assertTrue(refreshed)
  asyncio.run(run())
