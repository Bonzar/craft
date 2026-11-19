"""Public API replay recovery across two service lifespans with one owner state."""
import hashlib
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
from starlette.testclient import TestClient
from test_service import fixture
from apps.graph_viewer.server.app import create_app


class RestartContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.source=self.root/'source.sqlite3';fixture(self.source);self.state=self.root/'state'
    def tearDown(self):
        self.tmp.cleanup()
    def manifest(self,client,query=None):
        for _ in range(120):
            response=client.get('/api/v1/graph',params=query or {'history':'1'})
            if response.status_code!=202:break
            time.sleep(.02)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()
    def first_lifespan(self):
        with TestClient(create_app(self.source,self.state,password='owner-test-strong')) as first:
            self.assertEqual(first.post('/api/v1/session',json={'password':'owner-test-strong'}).status_code,200)
            original=self.manifest(first)
            pinned=self.manifest(first,{'mode':'known_then','at':'2025-02-01T00:00:00Z','history':'1'})
            card=first.get('/api/v1/nodes/old',params={'revision':pinned['revision']}).json()
            with sqlite3.connect(self.source) as c:
                c.execute("DELETE FROM memory_edges WHERE edge_id='proposal-edge'")
                c.execute('UPDATE memory_state SET version=version+1')
            for _ in range(120):
                latest=self.manifest(first)
                if latest['revision']!=original['revision']:break
                time.sleep(.02)
            self.assertGreater(int(latest['live_cursor']),0)
            cookies=first.cookies
        return latest,pinned,card,cookies
    def restarted_client(self,cookies):
        client=TestClient(create_app(self.source,self.state));client.cookies.update(cookies)
        return client
    def test_old_cursor_and_revision_get_protocol_reset_instead_of_terminal_http_error(self):
        latest,_,_,cookies=self.first_lifespan()
        with self.restarted_client(cookies) as second:
            self.assertTrue(second.get('/api/v1/session').json()['authenticated'])
            response=second.get('/api/v1/events',params={'revision':latest['revision']},headers={'Last-Event-ID':latest['live_cursor']})
            self.assertEqual(response.status_code,200,response.text)
            self.assertTrue(response.headers['content-type'].startswith('text/event-stream'))
            self.assertIn('event: reset',response.text)
            data=json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith('data: ')))
            self.assertEqual(data['reason'],'cursor_expired')
            self.assertEqual(parse_qs(urlsplit(data['manifest_url']).query)['history'],['1'])
            self.assertEqual(self.manifest(second)['counts']['edges'],2)
    def test_known_then_card_and_recovery_url_preserve_exact_cached_perspective(self):
        latest,pinned,card,cookies=self.first_lifespan()
        with self.restarted_client(cookies) as second:
            response=second.get('/api/v1/nodes/old',params={'revision':pinned['revision']})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['text_revision'],card['text_revision'])
            self.assertEqual(response.json()['status'],card['status'])
            self.assertNotEqual(response.json()['status']['disposition'],'superseded')
            response=second.get('/api/v1/events',params={'revision':pinned['revision'],'after':latest['live_cursor']})
            self.assertEqual(response.status_code,200,response.text)
            data=json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith('data: ')))
            query=parse_qs(urlsplit(data['manifest_url']).query)
            self.assertEqual(query['mode'],['known_then']);self.assertEqual(query['at'],[pinned['selector']['at']])
            after=second.get('/api/v1/nodes/old',params={'revision':pinned['revision']})
            self.assertEqual(after.status_code,200)
            self.assertEqual(after.json()['text_revision'],card['text_revision'])
            self.assertEqual(after.json()['status'],card['status'])
            self.assertEqual(second.head('/api/v1/events',params={'revision':pinned['revision'],'after':data['cursor']}).status_code,200)
    def test_malformed_cursors_remain_denied_and_truly_missing_cards_remain_409(self):
        latest,_,_,cookies=self.first_lifespan()
        baseline=hashlib.sha256(self.source.read_bytes()).hexdigest()
        with self.restarted_client(cookies) as second:
            for cursor in ['-1','garbage','1.5','9'*5000]:
                self.assertEqual(second.get('/api/v1/events',params={'revision':latest['revision'],'after':cursor}).status_code,400)
            response=second.get('/api/v1/nodes/old',params={'revision':'f'*32})
            self.assertEqual(response.status_code,409,response.text)
            self.assertEqual(response.json()['error']['code'],'revision_expired')
            self.assertEqual(second.get('/api/v1/nodes/old',params={'revision':'../../source.sqlite3'}).status_code,409)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),baseline)

    def test_deleted_or_invalid_cached_snapshot_is_explicitly_expired(self):
        import shutil
        latest,pinned,_,cookies=self.first_lifespan()
        shutil.rmtree(self.state/'cache'/pinned['revision'])
        path=self.state/'cache'/latest['revision']/'manifest.json'
        manifest=json.loads(path.read_text());manifest['counts']['nodes']+=1;path.write_text(json.dumps(manifest))
        with self.restarted_client(cookies) as second:
            for revision in [pinned['revision'],latest['revision']]:
                response=second.get('/api/v1/nodes/old',params={'revision':revision})
                self.assertEqual(response.status_code,409,response.text)
                self.assertEqual(response.json()['error']['code'],'revision_expired')

    def test_cached_binary_length_and_provider_identity_must_match(self):
        latest,pinned,_,cookies=self.first_lifespan()
        nodes=self.state/'cache'/pinned['revision']/'nodes.bin'
        nodes.write_bytes(nodes.read_bytes()[:-1])
        path=self.state/'cache'/latest['revision']/'manifest.json'
        manifest=json.loads(path.read_text());manifest['provider_id']='another-provider';path.write_text(json.dumps(manifest))
        with self.restarted_client(cookies) as second:
            for revision in [pinned['revision'],latest['revision']]:
                self.assertEqual(second.get('/api/v1/nodes/old',params={'revision':revision}).status_code,409)


class RestartModel:
    def __init__(self,identity='fastembed:restart-fixture',deny_documents=False):
        self.identity=identity;self.deny_documents=deny_documents
    def __call__(self,texts):
        if self.deny_documents and texts not in [['query warmup'],['query']]:
            raise ValueError('Document inference disabled for this test boundary')
        return [[1.,0.,0.] for _ in texts]


class SemanticRestartContract(unittest.TestCase):
    setUp=RestartContract.setUp
    tearDown=RestartContract.tearDown
    manifest=RestartContract.manifest
    def cached_semantic(self):
        selector={'mode':'known_then','at':'2025-02-01T00:00:00Z','history':'1'}
        with TestClient(create_app(self.source,self.state,password='owner-test-strong',semantic_model=RestartModel())) as first:
            first.post('/api/v1/session',json={'password':'owner-test-strong'})
            for _ in range(120):
                manifest=self.manifest(first,selector)
                if manifest['preparation'].get('semantic',{}).get('status')=='ready':break
                time.sleep(.02)
            self.assertEqual(manifest['preparation']['semantic']['status'],'ready')
            cookies=first.cookies
        return manifest,cookies,selector
    def resumed_semantic(self,manifest,cookies,selector,model):
        with TestClient(create_app(self.source,self.state,semantic_model=model)) as second:
            second.cookies.update(cookies)
            self.assertEqual(second.get('/api/v1/nodes/old',params={'revision':manifest['revision']}).status_code,200)
            self.assertEqual(second.head('/api/v1/events',params={'revision':manifest['revision']}).status_code,200)
            for _ in range(120):
                resumed=self.manifest(second,selector)
                status=resumed['preparation'].get('semantic',{}).get('status')
                if status in {'ready','unavailable'}:break
                time.sleep(.02)
            response=second.get('/api/v1/search',params={'revision':manifest['revision'],'mode':'semantic','q':'query'})
            return resumed,response.status_code
    def test_matching_semantic_model_and_shape_restore_a_ready_index(self):
        manifest,cookies,selector=self.cached_semantic()
        resumed,status=self.resumed_semantic(manifest,cookies,selector,RestartModel(deny_documents=True))
        self.assertEqual(status,200)
        self.assertTrue(resumed['preparation']['semantic'].get('restored'), 'Ready cached matrix should be restored after validating model and shape')
    def test_wrong_model_identity_is_not_silently_attached_to_cached_vectors(self):
        manifest,cookies,selector=self.cached_semantic()
        resumed,status=self.resumed_semantic(manifest,cookies,selector,RestartModel('fastembed:different-model',True))
        self.assertEqual(status,503)
        self.assertEqual(resumed['preparation']['semantic']['status'],'unavailable')
    def test_wrong_vector_shape_is_rebuilt_from_digest_cache_instead_of_attached(self):
        manifest,cookies,selector=self.cached_semantic()
        path=self.state/'cache'/manifest['revision']/'vectors.f32';path.write_bytes(path.read_bytes()[:-4])
        resumed,status=self.resumed_semantic(manifest,cookies,selector,RestartModel(deny_documents=True))
        self.assertEqual(status,200)
        self.assertFalse(resumed['preparation']['semantic'].get('restored',False))
        self.assertEqual(path.stat().st_size,resumed['counts']['nodes']*3*4)
