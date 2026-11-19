"""A source authored as JSON reaches the unchanged authenticated public API."""
import copy
import hashlib
import importlib.abc
import json
import math
import shutil
import sqlite3
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import quote
from unittest.mock import patch

from starlette.testclient import TestClient
from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.server.providers.json_snapshot import JsonSnapshotSource, SourceFormatError

FIXTURE=Path(__file__).parents[1]/'fixtures'/'independent-source.json'
ALL={'requirement','old','plan','replacement','late','literal'}
LONG='Решили: '+('точный 😀 текст; '*5000)+'КОНЕЦ'
TEXTS={'requirement':'Сохранять исходные тексты без сокращения.', 'old':'Старый маршрут: мир, мир 😀.',
       'plan':'План: отправиться к морю 1 марта.', 'replacement':'Новый маршрут проходит через лес.',
       'late':'Решили: это неизвестный вид из внешней системы.', 'literal':LONG}


class NoSourceConstructors(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path,target=None):
        if fullname.startswith('prototypes.assertion_memory') or fullname.startswith('assertion_memory'):
            raise AssertionError('JSON adapter must not import the source engine')


class JsonPublicAPI(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.path=self.root/'independent.json'; shutil.copyfile(FIXTURE,self.path)
        self.before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.guard=NoSourceConstructors(); sys.meta_path.insert(0,self.guard)
        self.open()
    def open(self):
        self.source=JsonSnapshotSource(self.path,self.root/'state')
        self.client=TestClient(create_app(None,self.root/'state',password='owner-test-strong',source=self.source))
        self.client.__enter__()
    def tearDown(self):
        self.client.__exit__(None,None,None)
        sys.meta_path.remove(self.guard)
        self.assertEqual(self.before,hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.tmp.cleanup()
    def login(self):
        r=self.client.post('/api/v1/session',json={'password':'owner-test-strong'},headers={'origin':'http://testserver'})
        self.assertEqual(r.status_code,200,r.text); self.csrf=r.json()['csrf_token']
    def manifest(self,**params):
        for _ in range(100):
            r=self.client.get('/api/v1/graph',params=params)
            if r.status_code!=202: break
            time.sleep(.02)
        self.assertEqual(r.status_code,200,r.text); return r.json()
    def card(self,id,m,**params):
        r=self.client.get('/api/v1/nodes/'+quote(id,safe=''),params=dict(revision=m['revision'],**params))
        self.assertEqual(r.status_code,200,r.text); return r.json()
    def ids(self,m):
        return {self.client.get(f'/api/v1/graph/{m["revision"]}/nodes/{i}').json()['id'] for i in range(m['counts']['nodes'])}
    def test_real_graph_binary_cards_sources_and_search(self):
        for path in ['/graph','/nodes/old','/sources/journal%2F%CE%B1']:
            self.assertEqual(self.client.get('/api/v1'+path).status_code,401)
        self.login(); m=self.manifest(history=1)
        self.assertEqual(m['provider_id'],'independent-json-fixture')
        self.assertEqual(m['source_revision'],'sha256:'+self.before)
        self.assertTrue(m['complete_graph']); self.assertEqual(m['counts']['nodes'],6); self.assertEqual(m['counts']['edges'],3)
        caps=self.client.get('/api/v1/capabilities').json()
        self.assertFalse(caps['capabilities']['live']); self.assertTrue(caps['capabilities']['topic_hierarchy'])
        nodes=list(struct.iter_unpack('<fffI',self.client.get(m['nodes']['url']).content))
        self.assertEqual(len(nodes),6); self.assertTrue(all(math.isfinite(x) for n in nodes for x in n[:3]))
        cards=[self.client.get(f'/api/v1/graph/{m["revision"]}/nodes/{i}').json() for i in range(6)]
        self.assertEqual({c['id'] for c in cards},ALL)
        self.assertEqual({c['id']:n[3] for c,n in zip(cards,nodes)},{'requirement':0,'old':1,'plan':0,'replacement':0,'late':0,'literal':0})
        edges=list(struct.iter_unpack('<II',self.client.get(m['edges']['url']).content))
        self.assertEqual([(cards[f]['id'],cards[t]['id']) for f,t in edges],[('replacement','old'),('literal','requirement'),('literal','requirement')])
        for c in cards:
            pieces=[c['text']]; offset=c['next_text_offset']
            while offset is not None:
                page=self.card(c['id'],m,offset=offset); pieces.append(page['text']); offset=page['next_text_offset']
            self.assertEqual(''.join(pieces),TEXTS[c['id']])
        old=self.card('old',m); self.assertEqual(old['actor']['id'],'author/Елена')
        self.assertEqual(old['source'],'Synthetic handwritten journal'); self.assertEqual(old['time']['valid_at'],'2026-01-01'); self.assertEqual(old['time']['valid_precision'],'day')
        self.assertEqual(old['sources'],[{'record_id':'journal/α','start':10,'end':37,'offset_unit':'unicode_codepoints','available':True,'context_truncated':False}])
        r=self.client.get('/api/v1/sources/journal%2F%CE%B1',params={'revision':m['revision'],'focus_start':10,'focus_end':37}).json()
        self.assertEqual(r['text'],'Журнал 😀: Старый маршрут: мир, мир 😀. Конец.'); self.assertEqual(r['highlights'],[{'start':10,'end':37}]); self.assertEqual(r['context'],[])
        unknown=self.card('requirement',m)
        self.assertIsNone(unknown['actor']['id']); self.assertIsNone(unknown['source']); self.assertEqual(unknown['episode'],'not-saved')
        self.assertEqual(self.client.get('/api/v1/sources/not-saved',params={'revision':m['revision']}).status_code,404)
        self.assertEqual(self.card('literal',m)['status']['disposition'],'assertion'); self.assertEqual(self.card('late',m)['status']['disposition'],'assertion')
        late=self.card('late',m)['time']
        self.assertEqual(late['recorded_at'],'2026-01-01T00:00:00Z'); self.assertEqual(late['interpreted_at'],'2026-02-01T00:00:00Z')
        self.assertEqual(late['source_recorded_at'],'2025-12-31T20:15:00+03:00'); self.assertEqual(late['source_available_at'],'2026-01-02T00:00:00Z')
        edge=self.client.get('/api/v1/edges/route-replacement',params={'revision':m['revision']}).json()
        self.assertEqual((edge['kind'],edge['from']['id'],edge['to']['id']),('supersedes','replacement','old'))
        self.assertEqual(edge['from']['fragment']['status'],'exact'); self.assertEqual(edge['to']['fragment']['status'],'ambiguous')
        self.assertEqual(edge['to']['fragment']['spans'],[{'start':16,'end':19},{'start':21,'end':24}])
        custom=self.client.get('/api/v1/edges/custom-parallel',params={'revision':m['revision']}).json()
        self.assertEqual(custom['kind'],'annotates/заметка'); self.assertEqual(custom['from']['fragment']['status'],'missing')
        self.assertEqual(custom['to']['text'],TEXTS['requirement'])
        hits=self.client.get('/api/v1/search',params={'revision':m['revision'],'q':'сокращения'}).json()['hits']
        self.assertEqual([h['id'] for h in hits],['requirement'])
        self.assertEqual(self.client.get('/api/v1/search',params={'revision':m['revision'],'q':'маршрут','mode':'semantic'}).status_code,503)
    def test_literal_history_memberships_and_effective_replacement(self):
        self.login()
        cases=[('known_then','2026-01-05',None,{'requirement','old','plan','literal'}),
               ('known_then','2026-02-01',None,ALL),('known_then','2026-03-02',None,ALL),
               ('known_now','2026-01-05',None,{'old','late','literal'}),
               ('known_now','2026-02-01',None,{'old','late','literal'}),
               ('known_now','2026-01-05','only',{'requirement'})]
        for mode,day,undated,ids in cases:
            with self.subTest(mode=mode,day=day,undated=undated):
                args={'mode':mode,'at':day+'T00:00:00Z','history':1}
                if undated: args['undated']=undated
                m=self.manifest(**args); self.assertEqual(self.ids(m),ids)
                if 'old' in ids:
                    self.assertEqual(self.card('old',m)['status']['disposition'],'superseded' if day=='2026-03-02' else 'decision')
                if mode=='known_now': self.assertEqual(m['counts']['undated_known_count'],1)
        current=self.manifest(); self.assertEqual(self.ids(current),ALL); self.assertEqual(current['counts']['visible_nodes'],5)
        self.assertEqual(self.card('old',current)['status']['replacements'],['replacement'])
        early=self.manifest(mode='known_then',at='2026-01-05T00:00:00Z'); self.assertEqual(early['counts']['future_valid_known_count'],1)
    def test_explicit_source_topics_shared_members_and_restart_bookmarks(self):
        self.login(); m=self.manifest(history=1); t=self.client.get(m['topics_url']).json()
        topics={x['id']:x for x in t['topics']}
        self.assertEqual({id:x['full_title'] for id,x in topics.items()},{'topic:root':'Маршруты — общий обзор','topic:rules':'Требования к текстам','topic:plans':'Планы на весну'})
        self.assertEqual(topics['topic:root']['source_ref']['source_version_id'],'root-v1')
        self.assertEqual(topics['topic:root']['available_at'],'2026-01-01T00:00:00Z')
        members={x['topic_index']:x['node_indices'] for x in t['memberships']}
        ordinal={self.card(id,m)['id']:self.card(id,m)['ordinal'] for id in ALL}
        self.assertEqual(set(members[topics['topic:rules']['index']]),{ordinal['requirement'],ordinal['literal']})
        self.assertTrue(all(ordinal['requirement'] in v for v in members.values()))
        self.assertEqual([(topics['topic:root']['index'],topics['topic:plans']['index'],'subquestion'),(topics['topic:root']['index'],topics['topic:rules']['index'],'subquestion')],[(x['parent'],x['child'],x['relation']) for x in t['links']])
        self.assertEqual(t['links'][0]['provenance']['source_link_id'],'root-plans')
        self.assertEqual(t['links'][0]['provenance']['source_provenance'],{'document':'synthetic outline','line':2})
        historical=self.manifest(mode='known_now',at='2026-01-05T00:00:00Z')
        self.assertIn('historical_topic_membership_partial_current_materialization',self.client.get(historical['topics_url']).json()['unknowns'])
        headers={'origin':'http://testserver','x-csrf-token':self.csrf}
        for id,kind,obj,provider in [('node','node','literal','independent-json-fixture'),('topic','topic','topic:rules','independent-json-fixture'),('other','node','literal','assertion-memory')]:
            r=self.client.put('/api/v1/bookmarks/'+id,json={'provider_id':provider,'object_kind':kind,'object_id':obj},headers=headers)
            self.assertEqual(r.status_code,200,r.text)
        self.client.__exit__(None,None,None); self.open(); self.login(); self.manifest()
        bookmarks={x['id']:x for x in self.client.get('/api/v1/bookmarks').json()['items']}
        self.assertEqual(bookmarks['node']['availability'],'available'); self.assertEqual(bookmarks['topic']['availability'],'available')
        self.assertEqual(bookmarks['other']['availability'],'provider_unavailable')
    def test_frozen_snapshot_is_read_only_and_input_replacement_does_not_relabel_it(self):
        with self.source.connect() as c:
            with self.assertRaises(sqlite3.OperationalError): c.execute("DELETE FROM memory_assertions")
        self.assertEqual(self.source.path.stat().st_mode&0o777,0o600)
        self.assertEqual(self.source.path.parent.stat().st_mode&0o777,0o700)
        self.login()
        # Adapter captures one immutable input revision; replacing input is unsupported.
        data=json.loads(self.path.read_text()); data['source_revision']='replacement'
        replacement=self.root/'new.json'; replacement.write_text(json.dumps(data)); replacement.replace(self.path)
        try:
            with patch.object(self.source,'revision',return_value='external-newer-revision'):
                m=self.manifest()
            self.assertEqual(m['source_revision'],'sha256:'+self.before); self.assertEqual(self.ids(m),ALL)
        finally: shutil.copyfile(FIXTURE,self.path)


class SourceValidation(unittest.TestCase):
    def reject(self,mutate,expected):
        data=json.loads(FIXTURE.read_text()); mutate(data)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'input.json'; source.write_text(json.dumps(data,ensure_ascii=True))
            before=source.read_bytes()
            with self.assertRaisesRegex(SourceFormatError,expected): JsonSnapshotSource(source,root/'state')
            self.assertEqual(source.read_bytes(),before)
            self.assertFalse(list((root/'state').rglob('source.sqlite3')))
    def test_malformed_container_types_and_provenance_fail_with_bounded_schema_errors(self):
        cases=[(lambda d:d['overviews'][0].update(current_version_id=[]),'current_version_id'),
               (lambda d:d['topic_links'][0].update(relation={}), 'relation'),
               (lambda d:d['nodes'][0].update(published_status={'published':1,'disposition':'cancelled'}), 'published_status'),
               (lambda d:d['topic_links'][0]['provenance'].update(note='\ud800'), 'Unicode')]
        for mutate,error in cases:
            with self.subTest(error=error): self.reject(mutate,error)
    def test_missing_knowledge_time_is_not_fabricated(self):
        self.reject(lambda d:d['nodes'][0].pop('recorded_at'),'nodes\\[0\\].recorded_at')
        self.reject(lambda d:d['edges'][0].pop('recorded_at'),'edges\\[0\\].recorded_at')
    def test_invalid_source_references_offsets_ids_and_cycles_are_rejected(self):
        cases=[(lambda d:d['nodes'][1]['evidence_refs'][0].update(episode_id='absent'),'episode_id'),
               (lambda d:d['nodes'][1]['evidence_refs'][0].update(end=9999),'end'),
               (lambda d:d['nodes'].append(copy.deepcopy(d['nodes'][0])),'duplicate'),
               (lambda d:d['edges'][0].update(to_id='absent'),'to_id'),
               (lambda d:d['overviews'][0]['versions'][0]['member_ids'].append('absent'),'member_ids'),
               (lambda d:d['topic_links'].append(dict(id='cycle',parent_id='rules',child_id='root',relation='subquestion',recorded_at='2026-01-01T00:00:00Z',provenance={'document':'outline'})),'cycle'),
               (lambda d:d['nodes'][0].update(recorded_at='yesterday'),'recorded_at'),
               (lambda d:d['nodes'][0].update(kind='model_verified'),'kind')]
        for mutate,error in cases:
            with self.subTest(error=error): self.reject(mutate,error)

if __name__=='__main__': unittest.main()
