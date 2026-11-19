"""Read-only source interpretation selection precedes every viewer projection."""
import hashlib
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from starlette.testclient import TestClient
from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.server.projection import prepare
from apps.graph_viewer.server.providers.assertion_memory import AssertionMemorySource
from revision_fixture import fixture, ids, CURRENT, BEFORE, AFTER, SUMMARY, logical_source


class SourceRevisionHTTP(unittest.TestCase):
    def test_withdrawn_interpretation_is_404_even_with_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); path=root/'source.sqlite3'; fixture(path)
            before=hashlib.sha256(path.read_bytes()).hexdigest()
            with TestClient(create_app(path,root/'state',password='owner-test-strong')) as client:
                login=client.post('/api/v1/session',json={'password':'owner-test-strong'})
                headers={'origin':'http://testserver','x-csrf-token':login.json()['csrf_token']}
                bookmark={'provider_id':'assertion-memory','object_kind':'node','object_id':'blue'}
                self.assertEqual(client.put('/api/v1/bookmarks/blue',json=bookmark,headers=headers).status_code,200)
                for history in [0,1]:
                    for _ in range(100):
                        response=client.get('/api/v1/graph',params={'history':history})
                        if response.status_code!=202: break
                        time.sleep(.02)
                    self.assertEqual(response.status_code,200,response.text)
                    m=response.json(); params={'revision':m['revision']}
                    card=client.get('/api/v1/nodes/blue',params=params)
                    self.assertEqual(card.status_code,404,card.text)
                    self.assertEqual(m['counts']['nodes'],3)
                    self.assertEqual(client.get('/api/v1/bookmarks').json()['items'][0]['availability'],'hidden_in_slice')
                    self.assertEqual(m['counts']['edges'],2)
                    self.assertEqual(client.get('/api/v1/search',params=dict(params,q='blue')).json()['hits'],[])
                    self.assertEqual(client.get('/api/v1/edges/blue-edge',params=params).status_code,404)
            self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())
    def test_pinned_http_cards_sources_topics_and_buffers_remain_exact_after_restart(self):
        from revision_fixture import publish
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); path=root/'source.sqlite3'; fixture(path,revised=False)
            state=root/'state'
            with TestClient(create_app(path,state,password='owner-test-strong')) as client:
                client.post('/api/v1/session',json={'password':'owner-test-strong'})
                for _ in range(100):
                    response=client.get('/api/v1/graph')
                    if response.status_code!=202: break
                    time.sleep(.02)
                self.assertEqual(response.status_code,200,response.text)
                m=response.json(); revision=m['revision']
                paths=[m['nodes']['url'],m['edges']['url'],m['topics_url'],
                       '/api/v1/nodes/blue','/api/v1/sources/event','/api/v1/overviews/topic:view']
                saved={url:client.get(url,params={'revision':revision}).content for url in paths}
                self.assertEqual(client.get('/api/v1/nodes/blue',params={'revision':revision}).status_code,200)
                cookies=client.cookies
            with sqlite3.connect(path) as c:
                publish(c,['round','green'],{'blue':'withdrawn','round':'retained','green':'introduced'},bump=1)
            with TestClient(create_app(path,state)) as client:
                client.cookies.update(cookies)
                for url,body in saved.items():
                    response=client.get(url,params={'revision':revision})
                    self.assertEqual(response.status_code,200,response.text)
                    self.assertEqual(response.content,body)
                for _ in range(100):
                    response=client.get('/api/v1/graph')
                    if response.status_code!=202: break
                    time.sleep(.02)
                self.assertEqual(response.status_code,200,response.text)
                self.assertNotEqual(response.json()['revision'],revision)
                self.assertEqual(client.get('/api/v1/nodes/blue',params={'revision':response.json()['revision']}).status_code,404)


class SourceRevisionProjection(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.path=self.root/'source.sqlite3'; fixture(self.path)
        self.source=AssertionMemorySource(self.path)
        self.before=logical_source(self.path)
    def tearDown(self):
        self.source.close()
        self.assertEqual(self.before,logical_source(self.path))
        self.tmp.cleanup()
    def graph(self, **kwargs):
        return prepare(self.source,self.root/'cache',dict(CURRENT,**kwargs))
    def test_selector_matrix_and_counts_filter_owned_backdated_rows(self):
        cases=[({}, {'round','green','direct'},1,1),
               ({'history':True}, {'round','green','direct'},1,1),
               ({'mode':'known_then','at':BEFORE}, {'blue','round','direct'},2,1),
               ({'mode':'known_then','at':AFTER}, {'round','green','direct'},1,1),
               ({'mode':'known_now','at':BEFORE,'undated':'include'}, {'green','direct'},1,1),
               ({'mode':'known_now','at':BEFORE}, {'green'},1,1),
               ({'mode':'known_now','at':BEFORE,'undated':'only'}, {'direct'},1,1)]
        for selection, expected, undated, future in cases:
            with self.subTest(selection=selection):
                graph=self.graph(**selection)
                self.assertEqual(ids(graph),expected)
                self.assertEqual(graph.manifest['counts']['undated_known_count'],undated)
                self.assertEqual(graph.manifest['counts']['future_valid_known_count'],future)
    def test_saved_topic_is_partial_stale_without_rewriting_summary_or_replacing_members(self):
        for selection, expected in [({}, {'round'}),({'mode':'known_then','at':BEFORE},{'blue','round'}),
                                    ({'mode':'known_now','at':BEFORE,'undated':'include'},set())]:
            with self.subTest(selection=selection):
                graph=self.graph(**selection); topics=graph.topics()
                with graph.connect() as c:
                    members={r[0] for r in c.execute('SELECT n.id FROM memberships m JOIN nodes n ON n.ordinal=m.node')}
                    self.assertEqual(c.execute('SELECT text FROM overviews').fetchone()[0],SUMMARY)
                self.assertEqual(members,expected)
                self.assertEqual(topics['topics'][0]['source_ref']['version_id'],1)
                if selection.get('mode')!='known_then':
                    self.assertEqual(topics['topics'][0]['freshness'],'stale')
                    self.assertEqual(topics['completeness'],'partial')
                    self.assertIn('source_topic_membership_filtered_by_revision',topics['unknowns'])
                else:
                    self.assertNotIn('source_topic_membership_filtered_by_revision',topics['unknowns'])

if __name__=='__main__': unittest.main()
