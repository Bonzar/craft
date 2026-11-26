"""Ordinary same-origin stream fixtures and epoch-fenced snapshot authority."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from starlette.testclient import TestClient
from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.server.browser_owner_api import BrowserOwnerAPI
from apps.graph_viewer.server.owner_snapshot_stream import owner_snapshot_events
from test_service import fixture

ORIGIN='https://owner.test.example'


class OwnerSnapshotStream(unittest.TestCase):
    def app(self, *, enabled):
        tmp=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        source=tmp/'source.sqlite3'
        fixture(source)
        browser=BrowserOwnerAPI(stage='prod',perform=lambda *_:None,
            queue_snapshot=lambda **_: {'stage':'prod','queues':[]})
        return create_app(source,tmp/'viewer',password='synthetic-test-owner',
            public_origin=ORIGIN,browser_owner_api=browser,owner_snapshot_events=enabled)
    def test_cookie_origin_first_epoch_snapshot_and_legacy_selector(self):
        app=self.app(enabled=True)
        with TestClient(app,base_url=ORIGIN) as c:
            self.assertEqual(c.get('/api/v1/events').status_code,401)
            self.assertEqual(c.post('/api/v1/session',headers={'Origin':ORIGIN},json={'password':'synthetic-test-owner'}).status_code,200)
            self.assertEqual(c.get('/api/v1/capabilities').json()['owner_live_bootstrap'],1)
            self.assertEqual(c.get('/api/v1/events',headers={'Origin':'https://other.test.example'}).status_code,403)
            self.assertEqual(c.get('/api/v1/events',headers={'Last-Event-ID':'1'}).status_code,400)
            with patch('apps.graph_viewer.server.owner_snapshot_stream.LIVE_SECONDS',0):
                first=c.get('/api/v1/events')
            self.assertEqual(first.status_code,200,first.text)
            expected=first.text.splitlines()[1].split('"')[3]
            self.assertRegex(expected,r'^[a-f0-9]{64}$')
            self.assertEqual(first.text,'event: hello\ndata: {"epoch":"'+expected+'"}\n\nevent: reset\ndata: {}\n\n')
            queue=c.get('/api/v1/capabilities?queue_status=1').json()
            self.assertEqual(queue['owner_live_epoch'],expected)
            import time
            for _ in range(40):
                graph=c.get('/api/v1/graph?mode=current')
                if graph.status_code==200:break
                time.sleep(.02)
            self.assertEqual(graph.json()['owner_live_epoch'],expected)
            # Existing revision-specific consumer remains supported.
            self.assertEqual(c.head('/api/v1/events',params={'revision':graph.json()['revision']}).status_code,200)

    def test_default_off_does_not_label_graph_or_enable_capability(self):
        with TestClient(self.app(enabled=False),base_url=ORIGIN) as c:
            c.post('/api/v1/session',headers={'Origin':ORIGIN},json={'password':'synthetic-test-owner'})
            self.assertNotIn('owner_live_bootstrap',c.get('/api/v1/capabilities').json())

    def test_change_hints_are_metadata_only_and_epoch_transition_ends_stream(self):
        a=(1,2,'a'*32);b=(1,2,'b'*32)
        current=a;valid=True
        manager=SimpleNamespace(cursor=0,closed=False,journal=[],sync_generation=lambda:current)
        request=SimpleNamespace(scope={'raw_path':b'/api/v1/events'},url=SimpleNamespace(query=''),headers=SimpleNamespace(getlist=lambda _:[]),method='GET')
        request.app=SimpleNamespace(state=SimpleNamespace(owner_snapshot_events=True,owner_snapshot_slots=asyncio.Semaphore(4),
            owner=SimpleNamespace(session=lambda _: 'synthetic' if valid else None),manager=manager))
        async def safe():return False
        request.is_disconnected=safe
        async def fixture():
            nonlocal current
            response=await owner_snapshot_events(request)
            body=response.body_iterator
            self.assertIn('"epoch":',await anext(body))
            self.assertEqual(await anext(body),'event: reset\ndata: {}\n\n')
            manager.journal.append((1,'queue_changed',{'private':'never on stream'}))
            self.assertEqual(await anext(body),'event: changed\ndata: {}\n\n')
            manager.journal.append((2,'delta',{'different_selection':'never on stream'}))
            self.assertEqual(await anext(body),'event: changed\ndata: {}\n\n')
            current=b
            self.assertEqual(await anext(body),'event: reset\ndata: {}\n\n')
            with self.assertRaises(StopAsyncIteration):await anext(body)
        asyncio.run(fixture())
