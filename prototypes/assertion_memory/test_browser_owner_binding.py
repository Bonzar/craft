from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase

from .browser_owner_binding import base_owner_browser
from .mcp_server import ServiceConfig
from prototypes.assertion_memory.storage import MemoryStore

class OwnerTest(TestCase):
    def test_real_owner_domain_single_lease_pause_and_clear_disabled(self):
        with TemporaryDirectory() as tmp:
            db=Path(tmp)/'fictional.sqlite3'
            with MemoryStore(str(db)):
                pass
            state=SimpleNamespace(db=db, provider='none',
                auto_process_fresh_mac=False,processing_session_ids=())
            leases=[]
            @contextmanager
            def lease():
                self.assertFalse(leases)
                leases.append(True)
                try:
                    yield
                finally:
                    self.assertTrue(leases.pop())
            identity=lambda:('assertion-memory-v0.1.34','a'*64)
            api=base_owner_browser(stage='prod',config=state,verified_identity=identity,
                                   release_lease=lease,interactive_enabled=False)
            receipt=api.perform('pause_prepare',{})
            status=api.perform('pause_set',{'paused':True,'expected_revision':receipt['revision']})
            self.assertTrue(status['paused'])
            self.assertFalse(leases)
            self.assertTrue(api.queue_snapshot(limit=1,offset=0)['processing_control']['paused'])
            self.assertFalse(any('clear' in route.path for route in api.routes()))
            with self.assertRaises(ValueError):
                api.perform('clear_commit',{})

    def test_dashboard_live_false_only_for_new_binding(self):
        import asyncio
        import json
        from unittest.mock import patch
        from apps.graph_viewer.server.browser_owner_api import BrowserOwnerAPI
        fixture={'stage':'prod','processing_control':{'can_change':True}}
        @contextmanager
        def lease():
            yield
        with patch('prototypes.assertion_memory.browser_owner_binding.snapshot',return_value=fixture):
            browser=base_owner_browser(stage='prod',config=ServiceConfig(db='/tmp/fixture.sqlite3',provider='none'),
                verified_identity=lambda:None,release_lease=lease,interactive_enabled=False)
            original=BrowserOwnerAPI(stage='prod',perform=lambda *_:None,
                                     queue_snapshot=lambda **_:fixture)
            for api, live in ((browser,False),(original,True)):
                response=asyncio.run(api.snapshot(None,1,0))
                self.assertEqual(json.loads(response.body)['capabilities']['live'],live)

    def test_dashboard_channel_explicitly_unavailable_without_legacy_dashboard(self):
        import asyncio
        from starlette.datastructures import QueryParams
        from apps.graph_viewer.server.app import events
        from apps.graph_viewer.server.common import APIError
        request=SimpleNamespace(query_params=QueryParams('channel=dashboard'),
            app=SimpleNamespace(state=SimpleNamespace(manager=None,queue_dashboard=None)))
        with self.assertRaises(APIError) as error:
            asyncio.run(events(request))
        self.assertEqual(error.exception.status,503)
