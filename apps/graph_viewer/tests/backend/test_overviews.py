"""Saved source overview bodies are lazy, verbatim and revision-pinned."""
import hashlib
import json
import secrets
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import quote
from unittest.mock import patch

from starlette.testclient import TestClient
from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.server.providers.json_snapshot import JsonSnapshotSource, SourceFormatError
from test_service import fixture, EARLY

CURRENT='Сфера 😀: точный сохранённый обзор.\n'+('Абзац без пересказа — ёж 🦔.\n'*3000)+'Последняя строка.'
HISTORICAL='Исторический обзор 😀: исходный маршрут.'
LATER='2025-04-01T00:00:00Z'
JSON_FIXTURE=Path(__file__).parents[1]/'fixtures'/'independent-source.json'


class SavedOverviewContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.source=self.root/'source.sqlite3'; fixture(self.source); self.state=self.root/'state'
        with sqlite3.connect(self.source) as c:
            c.execute('UPDATE memory_view_versions SET snapshot=? WHERE id=1',
                      (json.dumps({'created_at':EARLY,'summary':HISTORICAL,'evidence':[{'id':'old'}]}),))
            c.execute('INSERT INTO memory_view_versions VALUES(?,?,?,?)',
                      (3,'parent',json.dumps({'created_at':LATER,'summary':CURRENT,'evidence':[{'id':'old'},{'id':'undated'}]}),LATER))
            c.execute("UPDATE memory_views SET current_id=3 WHERE id='parent'")
        self.source_hash=hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.token=secrets.token_urlsafe(40)
        self.secret=self.root/'read.secret'; self.secret.write_text(self.token); self.secret.chmod(0o600)
        self.app=create_app(self.source,self.state,password='owner-test-strong',bridge_read_token_file=self.secret)
        self.client=TestClient(self.app); self.client.__enter__()
    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),self.source_hash)
        self.tmp.cleanup()
    def login(self):
        r=self.client.post('/api/v1/session',json={'password':'owner-test-strong'},headers={'origin':'http://testserver'})
        self.assertEqual(r.status_code,200,r.text); self.csrf=r.json()['csrf_token']
    def manifest(self,**params):
        for _ in range(120):
            r=self.client.get('/api/v1/graph',params=params)
            if r.status_code!=202: break
            time.sleep(.02)
        self.assertEqual(r.status_code,200,r.text); return r.json()
    def overview(self,m,id='topic:parent',**params):
        return self.client.get('/api/v1/overviews/'+quote(id,safe=''),params=dict(revision=m['revision'],**params))
    def source_was_intentionally_changed(self):
        self.source_hash=hashlib.sha256(self.source.read_bytes()).hexdigest()
    def test_owner_paged_literal_text_and_metadata_are_revision_bound(self):
        self.login(); m=self.manifest(history=1)
        topic=next(t for t in self.client.get(m['topics_url']).json()['topics'] if t['id']=='topic:parent')
        self.assertNotIn('summary',topic); self.assertNotIn(CURRENT,self.client.get(m['topics_url']).text)
        pieces=[]; offset=0; expected_revision=hashlib.sha256(CURRENT.encode()).hexdigest()
        while offset is not None:
            r=self.overview(m,offset=offset,limit=1200); self.assertEqual(r.status_code,200,r.text); page=r.json()
            for key in ('slice_member_count','session_filter_applied','overview_status','assembly_coverage',
                        'domain_coverage','purpose','overview_protocol','overview_failure',
                        'accepted_at_that_snapshot','navigation_membership'):
                self.assertEqual(page[key],topic.get(key),key)
            self.assertEqual(page['current_validation'],topic.get('current_validation','unknown'))
            self.assertIs(page['domain_answer_current'],False)
            self.assertEqual(page['topic_id'],'topic:parent'); self.assertEqual(page['revision'],m['revision'])
            self.assertEqual(page['provider_id'],'assertion-memory'); self.assertEqual(page['source_ref'],topic['source_ref'])
            self.assertEqual(page['source_ref']['version_id'],3); self.assertEqual(page['available_at'],LATER)
            self.assertEqual(page['freshness'],'current'); self.assertEqual(page['offset'],offset)
            self.assertEqual(page['text_total'],len(CURRENT)); self.assertEqual(page['text_revision'],expected_revision)
            self.assertEqual(page['offset_unit'],'unicode_codepoints'); pieces.append(page['text']); offset=page['next_offset']
        self.assertEqual(''.join(pieces),CURRENT)
        self.assertEqual(self.overview(m,offset=len(CURRENT)).json()['text'],'')
        self.assertEqual(self.client.head('/api/v1/overviews/topic%3Aparent',params={'revision':m['revision']}).status_code,200)
        self.assertEqual(self.client.head('/api/v1/overviews/topic%3Aparent',params={'revision':m['revision']}).content,b'')
        for params in [{'offset':-1},{'offset':len(CURRENT)+1},{'offset':'1.5'},{'limit':0},{'limit':64001}]:
            self.assertEqual(self.overview(m,**params).status_code,400,params)
        self.assertEqual(self.client.get('/api/v1/overviews/topic%3Aparent').json()['error']['code'],'revision_required')
    def test_missing_nonstrings_empty_and_legacy_overviews_are_explicit(self):
        self.login(); m=self.manifest()
        r=self.overview(m,'topic:child'); self.assertEqual(r.status_code,404); self.assertEqual(r.json()['error']['code'],'overview_unavailable')
        self.assertEqual(self.overview(m,'topic:absent').json()['error']['code'],'not_found')
        with sqlite3.connect(self.source) as c:
            c.execute('UPDATE memory_view_versions SET snapshot=? WHERE id=2',(json.dumps({'summary':{'text':'not a saved body'},'evidence':[]}),))
        self.source_was_intentionally_changed()
        n=self.manifest(mode='known_then',at='2025-02-01T00:00:00Z')
        self.assertEqual(self.overview(n,'topic:child').json()['error']['code'],'overview_unavailable')
        with sqlite3.connect(self.source) as c:
            c.execute('UPDATE memory_view_versions SET snapshot=? WHERE id=2',(json.dumps({'summary':'','evidence':[]}),))
        self.source_was_intentionally_changed()
        e=self.manifest(mode='known_then',at='2025-02-02T00:00:00Z')
        page=self.overview(e,'topic:child'); self.assertEqual(page.status_code,200,page.text)
        self.assertEqual(page.json()['text'],''); self.assertEqual(page.json()['text_total'],0); self.assertIsNone(page.json()['next_offset'])
        # This is a viewer-owned synthetic cache, simulating a pre-feature snapshot.
        with sqlite3.connect(self.state/'cache'/m['revision']/'projection.sqlite3') as c:
            c.execute('DROP TABLE IF EXISTS overviews')
        legacy=self.overview(m); self.assertEqual(legacy.status_code,404); self.assertEqual(legacy.json()['error']['code'],'overview_unavailable')
        self.assertEqual(self.client.get('/api/v1/nodes/old',params={'revision':m['revision']}).status_code,200)
    def test_selected_historical_version_and_restart_remain_pinned(self):
        self.login(); m=self.manifest(mode='known_then',at='2025-02-01T00:00:00Z',history=1)
        historical=self.overview(m); self.assertEqual(historical.status_code,200,historical.text)
        self.assertEqual(historical.json()['text'],HISTORICAL); self.assertEqual(historical.json()['source_ref']['version_id'],1)
        self.assertEqual(historical.json()['available_at'],EARLY); self.assertEqual(historical.json()['freshness'],'unknown')
        cookies=self.client.cookies; self.client.__exit__(None,None,None)
        with sqlite3.connect(self.source) as c:
            c.execute('UPDATE memory_view_versions SET snapshot=? WHERE id=1',(json.dumps({'summary':'Changed after pinned preparation','evidence':[]}),))
        self.source_was_intentionally_changed()
        self.client=TestClient(create_app(self.source,self.state,bridge_read_token_file=self.secret)); self.client.cookies.update(cookies); self.client.__enter__()
        restored=self.overview(m); self.assertEqual(restored.status_code,200,restored.text)
        self.assertEqual(restored.json(),historical.json())
    def test_append_copy_preserves_body_and_uses_new_snapshot_freshness(self):
        self.login(); old=self.manifest(history=1); original=self.overview(old)
        self.assertEqual(original.status_code,200,original.text)
        with patch('apps.graph_viewer.server.manager.prepare',side_effect=AssertionError('Append must use COW path')):
            with sqlite3.connect(self.source) as c:
                c.execute('INSERT INTO memory_assertions VALUES(?,?,?,?,?,?,?,?,?)',('added','New supplied assertion','a','s','session',EARLY,EARLY,'s','{}'))
                c.execute('UPDATE memory_state SET version=version+1')
            self.source_was_intentionally_changed()
            for _ in range(100):
                latest=self.manifest(history=1)
                if latest['revision']!=old['revision']: break
                time.sleep(.02)
        self.assertNotEqual(latest['revision'],old['revision'])
        copied=self.overview(latest); self.assertEqual(copied.status_code,200,copied.text)
        self.assertEqual(copied.json()['text'],CURRENT[:12000]); self.assertEqual(copied.json()['text_revision'],original.json()['text_revision'])
        self.assertEqual(copied.json()['source_ref'],original.json()['source_ref']); self.assertEqual(copied.json()['freshness'],'stale')
        self.assertEqual(self.overview(old).json(),original.json())
    def test_owner_or_read_bearer_authenticates_reads_only(self):
        path='/api/v1/overviews/topic%3Aparent'
        for method in ['GET','HEAD']:
            self.assertEqual(self.client.request(method,path).status_code,401)
        self.login(); m=self.manifest(); self.client.cookies.clear()
        headers={'authorization':'Bearer '+self.token}
        r=self.client.get(path,params={'revision':m['revision'],'limit':15},headers=headers)
        self.assertEqual(r.status_code,200,r.text); self.assertEqual(r.json()['text'],CURRENT[:15])
        self.assertEqual(self.client.head(path,params={'revision':m['revision']},headers=headers).status_code,200)
        self.assertEqual(self.client.get(path,params={'revision':m['revision']},headers={'authorization':'Bearer '+'x'*40}).status_code,401)
        for method in ['POST','PUT','DELETE','PATCH']:
            self.assertEqual(self.client.request(method,path,headers=headers,json={}).status_code,401)


class JsonSavedOverviewContract(unittest.TestCase):
    def test_optional_source_version_summary_is_literal_and_missing_stays_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); value=json.loads(JSON_FIXTURE.read_text())
            value['overviews'][0]['versions'][0]['summary']='Внешний обзор 😀\nТолько исходные слова.'
            path=root/'source.json'; path.write_text(json.dumps(value,ensure_ascii=False)); before=path.read_bytes()
            source=JsonSnapshotSource(path,root/'state')
            with TestClient(create_app(None,root/'state',source=source,password='owner-test-strong')) as client:
                client.post('/api/v1/session',json={'password':'owner-test-strong'})
                for _ in range(100):
                    r=client.get('/api/v1/graph')
                    if r.status_code!=202: break
                    time.sleep(.02)
                self.assertEqual(r.status_code,200,r.text); m=r.json()
                page=client.get('/api/v1/overviews/topic%3Aroot',params={'revision':m['revision']})
                self.assertEqual(page.status_code,200,page.text); self.assertEqual(page.json()['text'],'Внешний обзор 😀\nТолько исходные слова.')
                self.assertEqual(page.json()['provider_id'],'independent-json-fixture'); self.assertEqual(page.json()['source_ref']['source_version_id'],'root-v1')
                missing=client.get('/api/v1/overviews/topic%3Arules',params={'revision':m['revision']})
                self.assertEqual(missing.status_code,404); self.assertEqual(missing.json()['error']['code'],'overview_unavailable')
            self.assertEqual(before,path.read_bytes())
    def test_invalid_json_summary_type_is_rejected_before_ir_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); value=json.loads(JSON_FIXTURE.read_text()); value['overviews'][0]['versions'][0]['summary']=42
            path=root/'source.json'; path.write_text(json.dumps(value))
            with self.assertRaisesRegex(SourceFormatError,'versions\\[0\\].summary'):
                JsonSnapshotSource(path,root/'state')
            self.assertFalse(list((root/'state').rglob('source.sqlite3')))

if __name__=='__main__': unittest.main()
