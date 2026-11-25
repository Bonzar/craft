from hashlib import sha256
from pathlib import Path
import json
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from . import queue_rest as rest


class QueueReadProd(unittest.IsolatedAsyncioTestCase):
    async def test_exact_optional_prod_binding_projected_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d).resolve()/'fixture.sqlite3'
            with sqlite3.connect(db) as conn:
                conn.executescript("CREATE TABLE memory_interactive_queries (id TEXT,state TEXT,created REAL);"
                                   "INSERT INTO memory_interactive_queries VALUES ('fixture','queued',1);")
            before=db.read_bytes()
            key='a'*64
            other='b'*64
            fp=sha256(key.encode()).hexdigest()
            config=SimpleNamespace(db=str(db),processing_session_ids=[],provider='none',
                                   auto_process_fresh_mac=True)
            lane_keys={'test':fp,'prod':fp}
            current=Path(rest.__file__).resolve().parents[2]
            with patch.object(rest,'PROD_DATABASE',db), patch.object(rest.os,'getuid',return_value=986),                 patch.object(rest.os,'geteuid',return_value=986),                 patch.object(rest.pwd,'getpwuid',return_value=SimpleNamespace(pw_name=rest.PROD_ACCOUNT)),                 patch.object(rest,'_policy',side_effect=lambda _,lane,**kw:lane_keys[lane]),                 patch.object(rest,'_identity',return_value=('assertion-memory-v0.1.16','d'*64)),                 patch.object(Path,'resolve',autospec=True,side_effect=self.resolved(current,db)):
                self.assertIsNone(rest.configured_queue_rest(rest.PROD_HTTP_CONFIG,8893,other))
                self.assertIsNone(rest.configured_queue_rest(rest.PROD_HTTP_CONFIG,8903,other)) # collision
                lane_keys['test']=None
                lane_keys['prod']=None
                self.assertIsNone(rest.configured_queue_rest(rest.PROD_HTTP_CONFIG,8903,other)) # dark
                lane_keys['prod']=fp
                bind=rest.configured_queue_rest(rest.PROD_HTTP_CONFIG,8903,other)
                app=bind(config,True)
                async def one(token,server,method='GET'):
                    events=[]
                    async def send(event): events.append(event)
                    async def recv(): return {'type':'http.request','body':b''}
                    await app(dict(type='http',path=rest.PATH,raw_path=rest.PATH.encode(),method=method,
                                   client=('127.0.0.1',2020),server=server,
                                   headers=[(b'host',b'127.0.0.1:8903'),(b'authorization',('Bearer '+token).encode())]),
                              recv,send)
                    return events[0]['status'],json.loads(events[1]['body'])
                self.assertEqual((await one(key,('127.0.0.1',8893)))[0],403)
                self.assertEqual((await one(other,('127.0.0.1',8903)))[0],401)
                self.assertEqual((await one(key,('127.0.0.1',8903),'PUT'))[0],405)
                status,body=await one(key,('127.0.0.1',8903))
                self.assertEqual((status,body['stage'],body['release_generation']),(200,'prod','d'*64))
                self.assertFalse(body['processing_control']['can_change'])
                lane_keys['test']=fp
                self.assertEqual((await one(key,('127.0.0.1',8903)))[0],503)
            self.assertEqual(db.read_bytes(),before)

    @staticmethod
    def resolved(current,db):
        def resolve(path,strict=False):
            if path==Path(rest.__file__) or path==db:
                return path
            return current
        return resolve
