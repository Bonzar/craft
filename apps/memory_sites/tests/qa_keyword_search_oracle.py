"""Original graph keyword HTTP responses and exact synthetic FTS projection rows."""
import json
import sqlite3
import tempfile
import time
from pathlib import Path

from starlette.testclient import TestClient
from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.server.providers.json_snapshot import JsonSnapshotSource

def build():
    source=Path(__file__).resolve().parents[2]/'graph_viewer/tests/fixtures/independent-source.json'
    fixture=json.loads(source.read_text())
    for field in fixture['overviews'][:2]:
        field['versions'][0]['summary']="Общий французский маршрут — только вымышленные сведения. Café Straße 🧭."
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        source=root/'synthetic.json'
        source.write_text(json.dumps(fixture,ensure_ascii=False),encoding='utf8')
        app=create_app(None,root,password='synthetic-test-only',source=JsonSnapshotSource(source,root),synthetic_fixture=True)
        with TestClient(app) as client:
            client.post('/api/v1/session',json={'password':'synthetic-test-only'},headers={'origin':'http://testserver'}).raise_for_status()
            for _ in range(100):
                response=client.get('/api/v1/graph')
                if response.status_code!=202:break
                time.sleep(.02)
            response.raise_for_status()
            manifest=response.json()
            revision=manifest['revision']
            graph=app.state.manager.get(revision)
            with graph.connect() as c:
                nodes=[list(row) for row in c.execute('SELECT id,ordinal,text,flags FROM nodes ORDER BY ordinal')]
                search=[list(row) for row in c.execute('SELECT rowid,text FROM search ORDER BY rowid')]
                topics=graph.topics()['topics']
                records={t['index']:t for t in topics}
                maps=[[r['topic']+1,records[r['topic']].get('query') or records[r['topic']].get('definition_title') or '',r['text'],r['topic'],r['id']]
                      for r in c.execute('SELECT topic,id,text FROM overviews ORDER BY topic')]
            cases=[]
            def check(**kw):
                response=client.get('/api/v1/search',params=kw)
                cases.append([str(response.request.url).removeprefix('http://testserver'),response.status_code,response.json()])
                return response.json()
            first=check(revision=revision,q='маршрут',limit=1)
            assert first['topic_hits'] and first['next_cursor']
            page=check(revision=revision,q='маршрут',limit=1,cursor=first['next_cursor'])
            assert page['topic_hits']
            if page['next_cursor']:check(revision=revision,q='маршрут',limit=1,cursor=page['next_cursor'])
            check(revision=revision,q='другой',cursor=first['next_cursor'])
            for q in ('текст','Café','STRASSE','🚦','маршрут общий','французский','--'):
                check(revision=revision,q=q)
            check(revision=revision,q='маршрут',limit=200)
            check(revision=revision,q='маршрут',limit=201)
            check(revision=revision,q='',mode='unexpected')
            check(revision=revision,q='')
            check(revision=revision,q='маршрут',mode='semantic')
            check(q='маршрут')
            check(revision='stale',q='маршрут')
            for n in nodes:
                terms=n[2].split()
                if terms: check(revision=revision,q=terms[0],limit=1)
            assert any(row[2].get('hits') for row in cases)
            return dict(revision=revision,nodes=nodes,search=search,topics=topics,maps=maps,cases=cases)

if __name__=='__main__':print(json.dumps(build(),ensure_ascii=False))
