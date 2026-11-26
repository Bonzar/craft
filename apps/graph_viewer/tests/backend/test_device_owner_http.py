from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse

from apps.graph_viewer.server.app import make_routes, Boundary
from apps.graph_viewer.server.browser_owner_api import BrowserOwnerAPI
from apps.graph_viewer.server.auth import COOKIE, OwnerStore
from apps.graph_viewer.server.common import APIError
from apps.graph_viewer.server.device_owner_http import DeviceOwnerHTTP, PATH
from prototypes.assertion_memory.machine_device_registry import MachineDeviceRegistry

ORIGIN='https://owner.example'
PASSWORD='synthetic device owner credential'

class DeviceHTTP(unittest.IsolatedAsyncioTestCase):
    async def test_owner_http_paths_cookie_origin_session_and_atomic_revoke(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            owner=OwnerStore(root/'owner',PASSWORD)
            cookie,csrf=owner.login(PASSWORD,'local-test')
            second,_=owner.login(PASSWORD,'other-test')
            registry=MachineDeviceRegistry.initialize(root/'device-test.sqlite3','test')
            browser=BrowserOwnerAPI(stage='test',perform=lambda *x:None,
                queue_snapshot=lambda **kw:{'stage':'test','capabilities':{'owner_machine_devices_v1':True}})
            self.assertFalse(any(route.path==PATH for route in make_routes(browser)))
            self.assertFalse((await browser.snapshot(None,1,0)).body.count(b'owner_machine_devices_v1'))
            serving={'ok':True}
            def verified_identity():
                if not serving['ok']:
                    raise ValueError('stale fixture')
                return ('synthetic-active-release','synthetic-generation')
            browser.attach_machine_devices(registry,nullcontext,verified_identity)
            self.assertIn(b'owner_machine_devices_v1', (await browser.snapshot(None,1,0)).body)
            self.assertTrue(any(route.path==PATH for route in make_routes(browser)))

            async def err(request,exc):
                return JSONResponse({'code':exc.code},status_code=exc.status)
            app=Starlette(routes=make_routes(browser),exception_handlers={APIError:err})
            app.add_middleware(Boundary)
            app.state.browser_owner_api=browser
            app.state.owner=owner
            app.state.public_origin=ORIGIN
            async with AsyncClient(transport=ASGITransport(app=app),base_url=ORIGIN) as client:
                self.assertEqual((await client.get(PATH)).status_code,401)
                client.cookies.set(COOKIE,cookie)
                status=await client.get(PATH)
                self.assertEqual(status.status_code,200)
                self.assertIn('no-store',status.headers['cache-control'])
                self.assertIn((await client.get(PATH+'?stage=prod')).status_code,(400,404))
                self.assertEqual((await client.get(PATH.replace('/devices','/de%76ices'))).status_code,404)
                self.assertEqual((await client.get(PATH,headers={'authorization':'Bearer synthetic'})).status_code,403)
                body={'stage':'test','expected_revision':status.json()['revision'],
                      'action':'enroll','fingerprint':'a'*64,'label':'Test unit'}
                headers={'origin':ORIGIN,'x-csrf-token':csrf}
                denied=await client.post(PATH+'/prepare',json=body,headers=headers|{'origin':'https://foreign.example'})
                self.assertEqual(denied.status_code,403)
                invalid=await client.post(PATH+'/prepare',json=body,headers=headers|{'x-csrf-token':'wrong'})
                self.assertEqual(invalid.status_code,403)
                serving['ok']=False
                self.assertNotIn(b'owner_machine_devices_v1',(await browser.snapshot(None,1,0)).body)
                blocked=await client.post(PATH+'/prepare',json=body,headers=headers)
                self.assertEqual(blocked.status_code,503)
                self.assertEqual(registry.machine_digests(),())
                serving['ok']=True
                prepared=await client.post(PATH+'/prepare',json=body,headers=headers)
                self.assertEqual(prepared.status_code,200)
                proof=headers|{'x-csrf-token':prepared.json()['operation_proof']}
                client.cookies.set(COOKIE,second)
                self.assertEqual((await client.put(PATH,json=body,headers=proof)).status_code,403)
                client.cookies.set(COOKIE,cookie)
                serving['ok']=False
                self.assertEqual((await client.put(PATH,json=body,headers=proof)).status_code,503)
                self.assertEqual(registry.machine_digests(),())
                serving['ok']=True
                self.assertEqual((await client.put(PATH,json=body,headers=proof)).status_code,200)
                self.assertEqual((await client.put(PATH,json=body,headers=proof)).status_code,409)
                self.assertEqual(registry.machine_digests(),('a'*64,))
