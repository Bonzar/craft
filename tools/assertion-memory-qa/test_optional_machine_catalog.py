"""Device enrollment/revocation changes the same real machine catalog, not Sites."""
from contextlib import nullcontext
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from apps.graph_viewer.server.browser_owner_api import BrowserOwnerAPI
from apps.graph_viewer.tests.backend.test_device_owner_http import COOKIE, ORIGIN, PASSWORD
from apps.graph_viewer.server.auth import OwnerStore
from apps.graph_viewer.server.app import Boundary, make_routes
from prototypes.assertion_memory.machine_device_registry import MachineDeviceRegistry
from prototypes.assertion_memory.release_catalog import export
from starlette.applications import Starlette
from compile_functional_release import compile_release
from private_host import effect_lock
from vm_relay import make_app


class SyntheticMachineCatalog(IsolatedAsyncioTestCase):
    async def test_owner_registration_and_immediate_machine_catalog_revocation(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'deploy.lock').touch(mode=0o600)
            test_release = 'assertion-memory-v9.9.9'
            existing_token, new_token, site_only_token = '1'*64, '2'*64, '3'*64
            fingerprint = lambda token: sha256(token.encode('ascii')).hexdigest()
            fixed = (fingerprint(existing_token),)
            registry = MachineDeviceRegistry.initialize(root/'private-devices.sqlite3','test',fixed)
            canonical = await export('a'*40)
            pins = {k:canonical[k] for k in ('revision','module_sha256','schema_sha256')}
            pins['registry'] = 'base-typed-operations-v1'
            declared = compile_release(canonical,pins,effects=False)
            profile = {'lane':'test','base_release':test_release,'enabled_capabilities':['baseline_read'],
                       'catalog_sha256':declared['catalog_sha256'],'require_catalog_identity':True}
            identity = lambda: (test_release,'b'*64)
            # For the catalog-only request the typed operation service need not
            # be invoked; make_app still uses the standard admission and catalog.
            from prototypes.assertion_memory.operation_registry import create_operations
            from prototypes.assertion_memory.mcp_server import ServiceConfig
            from prototypes.assertion_memory.storage import MemoryStore
            cfg = ServiceConfig(db=str(root/'fictional.sqlite3'),provider='none',processing_session_ids=[])
            with MemoryStore(cfg.db) as store:
                service = cfg.service(store)
                operations = create_operations(cfg,store,background=False,service=service)
                relay = make_app(profile,declared,site_only_token,identity,operations=operations,
                                 machine_digests=fixed,machine_resolver=registry.machine_digests)
                guarded = effect_lock(relay,root,'test',None,fixed,machine_resolver=registry.machine_digests)
                owner = OwnerStore(root/'owner',PASSWORD)
                cookie,csrf = owner.login(PASSWORD,'fictional-operator')
                browser = BrowserOwnerAPI(stage='test',perform=lambda *_:None,
                    queue_snapshot=lambda **_: {'stage':'test','capabilities':{}})
                browser.attach_machine_devices(registry,nullcontext,identity)
                app = Starlette(routes=make_routes(browser))
                app.add_middleware(Boundary)
                app.state.browser_owner_api, app.state.owner, app.state.public_origin = browser,owner,ORIGIN
                async with (AsyncClient(transport=ASGITransport(app=app),base_url=ORIGIN) as ui,
                            AsyncClient(transport=ASGITransport(app=guarded),base_url=ORIGIN) as machine):
                    ui.cookies.set(COOKIE,cookie)

                    async def catalog(token):
                        return await machine.post('/memory-api-test',
                            headers={'Authorization':'Bearer '+token},
                            json={'request_id':str(uuid4()),'operation':'catalog','arguments':{}})

                    stable = await catalog(existing_token)
                    self.assertEqual(stable.status_code,200)
                    self.assertEqual({t['name'] for t in stable.json()['tools']},set(canonical['tools']))
                    self.assertEqual((await catalog(new_token)).status_code,401)
                    self.assertEqual((await catalog(site_only_token)).status_code,401)
                    target = '/api/v1/processing/devices'

                    async def mutate(action, **additional):
                        current = await ui.get(target)
                        self.assertEqual(current.status_code,200,current.text)
                        intent = {'stage':'test','action':action,'fingerprint':fingerprint(new_token),
                                  'expected_revision':current.json()['revision'],**additional}
                        prep = await ui.post(target+'/prepare',json=intent,
                                            headers={'Origin':ORIGIN,'X-CSRF-Token':csrf})
                        self.assertEqual(prep.status_code,200,prep.text)
                        done = await ui.put(target,json=intent,
                            headers={'Origin':ORIGIN,'X-CSRF-Token':prep.json()['operation_proof']})
                        self.assertEqual(done.status_code,200,done.text)
                        self.assertTrue(done.json()['committed'])

                    await mutate('enroll',label='Fictional device')
                    admitted = await catalog(new_token)
                    self.assertEqual(admitted.status_code,200)
                    self.assertEqual(admitted.json()['tools'],stable.json()['tools'])
                    await mutate('revoke')
                    self.assertEqual((await catalog(new_token)).status_code,401)
                    self.assertEqual((await catalog(existing_token)).status_code,200)
