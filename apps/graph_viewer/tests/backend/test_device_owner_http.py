from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse

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
            guarded=DeviceOwnerHTTP(stage='test',registry=registry,release_guard=nullcontext)

            async def err(request,exc):
                return JSONResponse({'code':exc.code},status_code=exc.status)
            app=Starlette(routes=guarded.routes(),exception_handlers={APIError:err})
            app.state.owner=owner
            app.state.public_origin=ORIGIN
            async with AsyncClient(transport=ASGITransport(app=app),base_url=ORIGIN) as client:
                self.assertEqual((await client.get(PATH)).status_code,401)
                client.cookies.set(COOKIE,cookie)
                status=await client.get(PATH)
                self.assertEqual(status.status_code,200)
                self.assertEqual(status.headers['cache-control'],'no-store')
                self.assertEqual((await client.get(PATH+'?stage=prod')).status_code,400)
                self.assertEqual((await client.get(PATH.replace('/devices','/de%76ices'))).status_code,404)
                self.assertEqual((await client.get(PATH,headers={'authorization':'Bearer synthetic'})).status_code,403)
                body={'stage':'test','expected_revision':status.json()['revision'],
                      'action':'enroll','fingerprint':'a'*64,'label':'Test unit'}
                headers={'origin':ORIGIN,'x-csrf-token':csrf}
                denied=await client.post(PATH+'/prepare',json=body,headers=headers|{'origin':'https://foreign.example'})
                self.assertEqual(denied.status_code,403)
                invalid=await client.post(PATH+'/prepare',json=body,headers=headers|{'x-csrf-token':'wrong'})
                self.assertEqual(invalid.status_code,403)
                prepared=await client.post(PATH+'/prepare',json=body,headers=headers)
                self.assertEqual(prepared.status_code,200)
                proof=headers|{'x-csrf-token':prepared.json()['operation_proof']}
                client.cookies.set(COOKIE,second)
                self.assertEqual((await client.put(PATH,json=body,headers=proof)).status_code,403)
                client.cookies.set(COOKIE,cookie)
                self.assertEqual((await client.put(PATH,json=body,headers=proof)).status_code,200)
                self.assertEqual((await client.put(PATH,json=body,headers=proof)).status_code,409)
                self.assertEqual(registry.machine_digests(),('a'*64,))
