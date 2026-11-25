"""Disposable Base SQLite plus real owner status ASGI; no privileged host access."""
import asyncio
import fcntl
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from admin_operations import AdminError
from visual_status import configured, mount_visual_status
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_service import ThoughtService


class VisualStatus(unittest.TestCase):
    token = 'a'*64
    owner = 'b'*64
    tag = 'assertion-memory-v1.0.7'
    generation = 'c'*64
    rid = '07ab7eff-c457-4d6a-8634-5338c49e0836'

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.db = self.root/'fixture.sqlite3'
        self.lock = self.root/'deploy.lock'
        self.lock.touch(mode=0o600)
        self.store = MemoryStore(self.db)
        self.addCleanup(self.store.close)
        ThoughtService(self.store, None)
        self.identity = self.tag, self.generation
        self.cfg = SimpleNamespace(db=str(self.db), provider='codex',
                                   processing_session_ids=('demo',), auto_process_fresh_mac=False)

    def request(self, *, query=b'', headers=None, mutation=None, stage='test', request_body=None,
                method='POST', path=None):
        app = mount_visual_status(config=self.cfg, stage=stage, owner_hash=self.owner, token=self.token,
                                  deploy_lock=self.lock, live_identity=lambda:self.identity,
                                  interactive_enabled=True)
        p = dict(request_id=self.rid,environment=stage,action='status',actor=self.owner,
                 arguments={'limit':20,'offset':0})
        p.update(mutation or {})
        body = json.dumps(p).encode() if request_body is None else request_body
        out = []
        async def recv():
            return dict(type='http.request', body=body, more_body=False)
        async def send(value):
            out.append(value)
        real_headers = [(b'authorization',('Bearer '+self.token).encode()),(b'content-type',b'application/json')]
        asyncio.run(app(dict(type='http',method=method,path=path or '/memory-visual-'+stage+'-admin',
                             headers=real_headers if headers is None else headers,query_string=query),recv,send))
        return out[0]['status'], json.loads(out[1]['body']), dict(out[0]['headers'])

    def test_status_uses_actual_scoped_db_release_and_disables_writes(self):
        for lane in ('test','prod'):
            status, b, h = self.request(stage=lane)
            self.assertEqual(status,200)
            self.assertEqual(h[b'cache-control'],b'private, no-store')
            self.assertEqual((b['request_id'],b['environment'],b['release_version']),(self.rid,lane,self.tag))
            self.assertEqual((b['result']['stage'],b['result']['release_version']),(lane,self.tag))
            self.assertEqual([q['id'] for q in b['result']['queues']],['source','maintenance','interactive'])
            self.assertEqual(b['result']['capabilities'],{'enroll_device':False,'reset_database':False})
        for action in ('enroll_device','prepare_reset','reset_database'):
            self.assertEqual(self.request(mutation={'action':action})[0],503)

    def test_cross_lane_or_unverified_owner_cannot_read(self):
        self.assertEqual(self.request(headers=[])[0],403)
        self.assertEqual(self.request(mutation={'actor':'d'*64})[0],403)
        self.assertEqual(self.request(mutation={'environment':'prod'})[0],400)
        self.assertEqual(self.request(method='GET')[0],404)
        self.assertEqual(self.request(query=b'lane=prod')[0],404)
        self.assertEqual(self.request(path='/memory-visual-prod-admin')[0],404)
        self.assertEqual(self.request(path='/mcp')[0],404)

    def test_bad_pagination_duplicate_members_and_lock_contention(self):
        for offset in (False,-1,1_000_001):
            self.assertEqual(self.request(mutation={'arguments':{'offset':offset,'limit':30}})[0],400)
        self.assertEqual(self.request(request_body=b'{"x":1,"x":2}')[0],400)
        self.assertEqual(self.request(request_body=b' '*4097)[0],400)
        with self.lock.open('rb') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertEqual(self.request()[0],503)
        self.assertEqual(self.request()[0],200)
        self.identity = None
        self.assertEqual(self.request()[0],503)

    def test_config_is_optional_and_pinned_to_distinct_token_file(self):
        seen=[]
        d=self.root
        def read(path,*_,**flags):
            seen.append((path,flags))
            return path.read_bytes()
        self.assertIsNone(configured(d,d,read_regular=read,reserved=set()))
        f=d/'visual-status.json'
        token_file=d/'state'/'visual-status-token'
        token_file.parent.mkdir()
        token_file.write_text(self.token)
        f.write_text(json.dumps({'owner_hash':self.owner,'token_file':str(token_file)}))
        self.assertEqual(configured(d,d,read_regular=read,reserved=set()),(self.owner,self.token))
        self.assertEqual(seen[-2],(f,{'owner':0}))
        self.assertEqual(seen[-1],(token_file,{'owner':os.getuid(),'private':True}))
        f.write_text(json.dumps({'owner_hash':self.owner,'token_file':str(token_file)+'.wrong'}))
        with self.assertRaises(ValueError):
            configured(d,d,read_regular=read,reserved=set())


if __name__=='__main__':
    unittest.main()
