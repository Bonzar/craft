"""Public contract behavior against isolated SQLite, never a personal database."""
import hashlib
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from starlette.testclient import TestClient
from apps.graph_viewer.server.app import create_app

SCHEMA = '''
CREATE TABLE memory_state(singleton INTEGER PRIMARY KEY,version INTEGER);
INSERT INTO memory_state VALUES(1,1);
CREATE TABLE memory_assertions(assertion_id TEXT PRIMARY KEY,text TEXT,actor TEXT,source TEXT,episode TEXT,valid_at TEXT,recorded_at TEXT,scope TEXT,metadata TEXT);
CREATE TABLE memory_edges(edge_id TEXT PRIMARY KEY,from_id TEXT,to_id TEXT,kind TEXT,recorded_at TEXT,metadata TEXT);
CREATE TABLE memory_raw_events(id TEXT PRIMARY KEY,session_id TEXT,turn_id TEXT,prompt TEXT,actor TEXT,source TEXT,scope TEXT,recorded_at TEXT,metadata TEXT);
CREATE TABLE memory_event_positions(event_id TEXT PRIMARY KEY,position INTEGER);
CREATE TABLE memory_views(id TEXT PRIMARY KEY,definition TEXT,current_id INTEGER,checked_version INTEGER,error TEXT,current_check_id INTEGER);
CREATE TABLE memory_view_versions(id INTEGER PRIMARY KEY,view_id TEXT,snapshot TEXT,created_at TEXT);
CREATE TABLE memory_view_checks(id INTEGER PRIMARY KEY,view_id TEXT,version_id INTEGER,source_version INTEGER,checked_at TEXT,freshness TEXT);
CREATE TABLE memory_question_route_versions(request_id TEXT,revision INTEGER,view_id TEXT,plan TEXT,recorded_at TEXT);
'''
EARLY='2025-01-01T00:00:00+00:00'
LATER='2025-03-01T00:00:00+00:00'


def fixture(path):
    with sqlite3.connect(path) as c:
        c.executescript(SCHEMA)
        def add(id, valid=EARLY, meta=None, text=None, recorded=EARLY,scope='s'):
            c.execute('INSERT INTO memory_assertions VALUES(?,?,?,?,?,?,?,?,?)',
                      (id,text or id,'actor','saved','session',valid,recorded,scope,json.dumps(meta or {})))
        add('old',meta={'kind':'decision','event_id':'event','source_span':{'start':21000,'end':21006}})
        add('future',LATER,{'kind':'decision'})
        add('undated',None)
        add('late',meta={'interpretation':{'interpreted_at':LATER}})
        add('proposal',meta={'kind':'proposal'})
        add('unicode',text='Привет 😀 мир, мир')
        c.execute('INSERT INTO memory_edges VALUES(?,?,?,?,?,?)',('replacement','future','old','supersedes',EARLY,'{}'))
        c.execute('INSERT INTO memory_edges VALUES(?,?,?,?,?,?)',('proposal-edge','proposal','old','supersedes',EARLY,'{}'))
        c.execute('INSERT INTO memory_edges VALUES(?,?,?,?,?,?)',('conflict','old','unicode','contradicts',EARLY,json.dumps({'source_quote':'old','target_quote':'мир'})))
        c.execute('INSERT INTO memory_raw_events VALUES(?,?,?,?,?,?,?,?,?)',('event','session','turn','x'*21000+'😀 quote'+'y'*1000,'actor','saved','s',EARLY,'{}'))
        c.execute('INSERT INTO memory_event_positions VALUES(?,?)',('event',0))
        for i,title,deps in [('parent','Главная тема',['child']),('child','Подвопрос',[])]:
            definition={'id':i,'title':title,'query':title,'scopes':['s'],'depends_on':deps}
            snap={'evidence':[{'id':'old'},{'id':'undated'}]}
            n=1 if i=='parent' else 2
            c.execute('INSERT INTO memory_views VALUES(?,?,?,?,?,?)',(i,json.dumps(definition),n,1,None,n))
            c.execute('INSERT INTO memory_view_versions VALUES(?,?,?,?)',(n,i,json.dumps(snap),EARLY))
            c.execute('INSERT INTO memory_view_checks VALUES(?,?,?,?,?,?)',(n,i,n,1,EARLY,'current'))
        c.execute('INSERT INTO memory_question_route_versions VALUES(?,?,?,?,?)',('r',1,'parent',json.dumps({'reuse_view_id':None,'subquestions':['Подвопрос']}),EARLY))


class ViewerContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.source=self.root/'source.sqlite3'; fixture(self.source)
        self.original=hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.client=TestClient(create_app(self.source,self.root/'state',password='owner-test-strong'))
        self.client.__enter__()
    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.assertEqual(self.original,hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.tmp.cleanup()
    def login(self):
        r=self.client.post('/api/v1/session',json={'password':'owner-test-strong'},headers={'origin':'http://testserver'})
        self.assertEqual(r.status_code,200,r.text)
        self.csrf=r.json()['csrf_token']
    def manifest(self,query=''):
        for _ in range(100):
            r=self.client.get('/api/v1/graph'+query)
            if r.status_code != 202: break
            time.sleep(.03)
        self.assertEqual(r.status_code,200,r.text)
        return r.json()
    def test_private_routes_require_authentication(self):
        for p in ['/graph','/capabilities','/nodes/old','/edges/replacement','/sources/event','/search?q=old','/events','/bookmarks','/graph/missing/nodes.bin']:
            r=self.client.get('/api/v1'+p)
            self.assertEqual(r.status_code,401,(p,r.text))
        self.assertEqual(self.client.get('/api/v1/health').json(),{'ok':True})
    def test_login_csrf_bookmarks_logout(self):
        self.assertEqual(self.client.post('/api/v1/session',json={'password':'wrong'}).status_code,401)
        self.login()
        b={'provider_id':'assertion-memory','object_kind':'node','object_id':'old'}
        self.assertEqual(self.client.put('/api/v1/bookmarks/b',json=b).status_code,403)
        h={'origin':'http://testserver','x-csrf-token':self.csrf}
        self.assertEqual(self.client.put('/api/v1/bookmarks/b',json=b,headers=h).status_code,200)
        self.assertEqual(self.client.get('/api/v1/bookmarks').json()['items'][0]['object_id'],'old')
        self.assertEqual(self.client.delete('/api/v1/session',headers=h).status_code,200)
        self.assertEqual(self.client.get('/api/v1/bookmarks').status_code,401)
    def test_knowledge_membership_and_effective_status_are_separate(self):
        self.login()
        m=self.manifest('?mode=known_then&at=2025-02-01T00:00:00Z')
        self.assertEqual(m['counts']['nodes'],5)
        self.assertEqual(m['counts']['future_valid_known_count'],1)
        self.assertEqual(m['counts']['visible_nodes'],5)
        card=self.client.get('/api/v1/nodes/old',params={'revision':m['revision']}).json()
        self.assertNotEqual(card['status']['disposition'],'superseded')
        n=self.manifest('?mode=known_now&at=2025-02-01T00:00:00Z')
        self.assertEqual(n['counts']['undated_known_count'],1)
        self.assertEqual(n['counts']['excluded_unknown_valid_count'],1)
        self.assertEqual(n['counts']['nodes'],4)
        current=self.manifest()
        self.assertEqual(current['counts']['nodes'],6)
        self.assertEqual(current['counts']['visible_nodes'],5)
        self.assertEqual(self.client.get('/api/v1/nodes/old',params={'revision':current['revision']}).json()['status']['replacements'],['future'])
    def test_binary_ordinals_topics_and_search(self):
        self.login(); m=self.manifest('?history=1')
        self.assertEqual(len(self.client.get(m['nodes']['url']).content),16*m['counts']['nodes'])
        self.assertEqual(len(self.client.get(m['edges']['url']).content),8*m['counts']['edges'])
        t=self.client.get(m['topics_url']).json()
        self.assertEqual(len(t['topics']),2)
        self.assertEqual([x['relation'] for x in t['links']],['subquestion'])
        r=self.client.get('/api/v1/search',params={'q':'Привет','revision':m['revision']}).json()
        self.assertEqual(r['hits'][0]['id'],'unicode')
        self.assertEqual(self.client.get('/api/v1/search',params={'q':'hi','mode':'semantic','revision':m['revision']}).status_code,503)
    def test_node_card_incident_continuation_uses_its_advertised_visibility(self):
        with sqlite3.connect(self.source) as c:
            c.executemany('INSERT INTO memory_edges VALUES(?,?,?,?,?,?)',[(f'many-{i}','unicode','undated','supports',EARLY,'{}') for i in range(25)])
        self.original=hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.login();m=self.manifest()
        card=self.client.get('/api/v1/nodes/unicode',params={'revision':m['revision']}).json()
        page=card['incident_edges']
        self.assertEqual(page['total'],26)
        self.assertEqual(page['visible_total'],25)
        self.assertIsNotNone(page['next_cursor'])
        continued=self.client.get(page['url'],params={'revision':m['revision'],'visible':'1','cursor':page['next_cursor']})
        self.assertEqual(continued.status_code,200,continued.text)
        self.assertEqual(len(continued.json()['items']),5)

    def test_url_encoded_stable_ids_with_slashes_remain_exact(self):
        from urllib.parse import quote
        id='source/Привет/edges'
        with sqlite3.connect(self.source) as c:
            c.execute('INSERT INTO memory_assertions VALUES(?,?,?,?,?,?,?,?,?)',(id,'exact slash ID','a','s','session',EARLY,EARLY,'s','{}'))
        self.original=hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.login();m=self.manifest()
        response=self.client.get('/api/v1/nodes/'+quote(id,safe=''),params={'revision':m['revision']})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['id'],id)

    def test_large_binary_gzip_has_compressed_content_length(self):
        import gzip
        with sqlite3.connect(self.source) as c:
            c.executemany('INSERT INTO memory_assertions VALUES(?,?,?,?,?,?,?,?,?)',[(f'bulk-{i}',f'bulk {i}','a','s','session',EARLY,EARLY,'s','{}') for i in range(5000)])
        self.original=hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.login();m=self.manifest()
        with self.client.stream('GET',m['nodes']['url'],headers={'accept-encoding':'gzip'}) as response:
            raw=b''.join(response.iter_raw())
            self.assertEqual(response.headers.get('content-encoding'),'gzip')
            self.assertIsNotNone(response.headers.get('content-length'))
            self.assertEqual(int(response.headers['content-length']),len(raw))
            self.assertEqual(len(gzip.decompress(raw)),m['counts']['nodes']*16)

    def test_focused_unicode_sources_and_incident_paging(self):
        self.login(); m=self.manifest('?history=1'); rev=m['revision']
        r=self.client.get('/api/v1/sources/event',params={'revision':rev,'focus_start':21000,'focus_end':21006,'limit':100})
        self.assertEqual(r.status_code,200,r.text); page=r.json()
        self.assertLessEqual(page['offset'],21000)
        self.assertGreater(page['offset']+len(page['text']),21000)
        self.assertIn('😀 quote',page['text']); self.assertTrue(page['before_available'])
        r=self.client.get('/api/v1/nodes/old/edges',params={'revision':rev,'limit':1,'visible':0}).json()
        self.assertEqual(r['total'],3); ids=[r['items'][0]['id']]
        while r['next_cursor']:
            r=self.client.get('/api/v1/nodes/old/edges',params={'revision':rev,'limit':1,'visible':0,'cursor':r['next_cursor']}).json()
            ids.extend(x['id'] for x in r['items'])
        self.assertEqual(len(set(ids)),3)
        edge=self.client.get('/api/v1/edges/conflict',params={'revision':rev}).json()
        self.assertEqual(edge['to']['fragment']['status'],'ambiguous')
        self.assertEqual(edge['to']['fragment']['spans'],[{'start':9,'end':12},{'start':14,'end':17}])

if __name__=='__main__': unittest.main()
