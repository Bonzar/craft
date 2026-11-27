import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from apps.graph_viewer.server import app as appmod
from apps.graph_viewer.server.browser_owner_api import BrowserOwnerAPI


def build(origin, lane=None, *, legacy=False):
    browser = (None if lane is None else
               BrowserOwnerAPI(stage=lane, perform=lambda *a: None, queue_snapshot=lambda *a: None))
    with patch.object(appmod, 'OwnerStore'), patch.object(appmod, 'GraphManager'), \
            patch.object(appmod, 'source_for_path'):
        app=appmod.create_app(Path('/fixture/source'), Path('/fixture/state'), public_origin=origin,
                              existing_owner_only=False, browser_owner_api=browser,
                              owner_snapshot_events=legacy)
    app.state.owner=SimpleNamespace(session=lambda request:
        'synthetic authenticated owner' if request.cookies.get('fixture')=='owner' else None)
    app.state.manager.source.capabilities.return_value={'capabilities':{'live':True},'readiness':[]}
    app.state.manager.live_profiles=[]
    app.state.manager.sync_generation.return_value=(1,2,'0'*32)
    return app


class GraphDataCapability(unittest.TestCase):
    def test_owner_feature_is_not_a_test_prod_or_release_decision(self):
        for origin,lane in (('https://one.invalid','test'),('https://two.invalid','prod')):
            with self.subTest(origin=origin):
                app=build(origin,lane)
                self.assertTrue(app.state.owner_graph_stream)
                client=TestClient(app,base_url=origin)
                anon=client.get('/api/v1/events?wire=graph-v1')
                self.assertEqual(anon.status_code,401)
                self.assertEqual(set(anon.json()),{'error'})
                client.cookies.set('fixture','owner')
                self.assertIs(client.get('/api/v1/capabilities').json()['owner_live_graph_v1'],True)
                self.assertEqual(client.get('/api/v1/events?wire=graph-v1',
                                     headers={'Sec-Fetch-Site':'cross-site'}).status_code,401)

    def test_old_visual_uses_unchanged_snapshot_and_metadata_routes_on_new_base(self):
        origin='https://legacy-owner.invalid'
        app=build(origin,'test',legacy=True)
        client=TestClient(app,base_url=origin)
        client.cookies.set('fixture','owner')
        async def local_marker(request):
            return JSONResponse({'legacy_snapshot':True})
        # Exercise actual production routing/Boundary; replace only output
        # producers so these tests cannot start a service or touch real data.
        with patch.object(appmod,'owner_snapshot_events',side_effect=local_marker) as old, \
                patch.object(appmod,'owner_graph_events') as new:
            self.assertEqual(client.get('/api/v1/events').json(),{'legacy_snapshot':True})
            old.assert_called_once()
            new.assert_not_called()
        graph=SimpleNamespace(revision='1'*32,manifest={'legacy_graph':True})
        app.state.manager.ensure=AsyncMock(return_value=graph)
        with patch.object(appmod,'owner_graph_events') as new:
            old=client.get('/api/v1/graph')
            self.assertEqual(old.status_code,200)
            self.assertIs(old.json()['legacy_graph'],True)
            app.state.manager.ensure.assert_awaited_once()
            new.assert_not_called()

    def test_legacy_non_owner_adapter_cannot_expose_graph_data_stream(self):
        origin='https://legacy-bridge.invalid'
        app=build(origin,legacy=True)
        self.assertFalse(app.state.owner_graph_stream)
        self.assertFalse(hasattr(app.state,'owner_graph_stream_key'))
        client=TestClient(app,base_url=origin)
        client.cookies.set('fixture','owner')
        self.assertNotIn('owner_live_graph_v1',client.get('/api/v1/capabilities').json())
        self.assertEqual(client.get('/api/v1/events?wire=graph-v1').status_code,401)
