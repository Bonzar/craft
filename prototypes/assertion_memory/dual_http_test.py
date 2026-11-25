"""Existing HTTP startup and health can coexist with one neutral tool service."""
import asyncio
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import tempfile
from unittest import TestCase, main
from unittest.mock import patch
from httpx import ASGITransport, AsyncClient
from .http_server import create_http_app
from .mcp_server import ServiceConfig
from .operation_registry import create_operations
from starlette.responses import JSONResponse


class Dual(TestCase):
    def test_actual_database_paths_are_exact_and_lane_bound(self):
        path=Path(__file__).resolve().parents[2]/'tools/assertion-memory-hosted-mcp/private_host.py'
        spec=spec_from_file_location('private_host_scope_test',path)
        host=module_from_spec(spec)
        spec.loader.exec_module(host)
        prod='/var/lib/assertion-memory/memory.sqlite3'
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            state=root/'state'
            state.mkdir()
            test=state/'fictional.sqlite3'
            test.touch()
            host.require_scoped_database('test',root,str(test))
            with patch.object(Path,'resolve',lambda p,strict=False:p), patch.object(Path,'is_file',return_value=True):
                host.require_scoped_database('prod',root,prod)
                for lane,value in [('test',prod),('prod',str(test)),('prod','/var/lib/assertion-memory/./memory.sqlite3'),
                                   ('prod','/var/lib/assertion-memory//memory.sqlite3'),
                                   ('test',str(state/'alternate.sqlite3')),('qa',str(test))]:
                    with self.subTest(lane=lane,value=value), self.assertRaises(ValueError):
                        host.require_scoped_database(lane,root,value)
            with patch.object(Path,'resolve',return_value=Path('/elsewhere/memory.sqlite3')):
                with self.assertRaises(ValueError):
                    host.require_scoped_database('prod',root,prod)
            test.unlink()
            test.symlink_to(state/'alternate.sqlite3')
            (state/'alternate.sqlite3').touch()
            with self.assertRaises(ValueError):
                host.require_scoped_database('test',root,str(test))

    def test_legacy_smoke_and_neutral_share_one_service(self):
        async def test(db):
            legacy='a'*64
            scoped='b'*64
            original=ServiceConfig.service
            seen=[]
            def tracked(config,store):
                service=original(config,store)
                seen.append(service)
                return service
            def neutral(config,store,service,background):
                self.assertIs(service,seen[0])
                ops=create_operations(config,store,background=background,service=service)
                async def api(scope,receive,send):
                    allowed=dict(scope['headers']).get(b'authorization')==('Bearer '+scoped).encode()
                    await JSONResponse({'neutral':len(ops.descriptors())} if allowed else {},
                                       status_code=200 if allowed else 401)(scope,receive,send)
                return frozenset(['/memory-mcp-test-tools']),api
            with patch.object(ServiceConfig,'service',tracked):
                app=create_http_app(ServiceConfig(db=db,provider='none',processing_session_ids=[]),
                                    legacy,'memory.example',background=False,neutral_factory=neutral)
                async with app.lifespan(), AsyncClient(transport=ASGITransport(app=app),
                                                      base_url='https://memory.example') as c:
                    headers={'accept':'application/json, text/event-stream',
                             'mcp-protocol-version':'2025-11-25'}
                    body={'jsonrpc':'2.0','id':1,'method':'tools/list','params':{}}
                    self.assertEqual((await c.post('/memory/mcp',headers=headers,json=body)).status_code,401)
                    r=await c.post('/memory/mcp',headers=headers|{'authorization':'Bearer '+legacy},json=body)
                    self.assertIn('memory_status',{tool['name'] for tool in r.json()['result']['tools']})
                    self.assertEqual((await c.post('/memory-mcp-test-tools',headers={'authorization':'Bearer '+legacy})).status_code,401)
                    self.assertEqual((await c.post('/memory-mcp-test-tools',headers={'authorization':'Bearer '+scoped})).status_code,200)
                    self.assertEqual((await c.post('/memory/mcp',headers=headers|{'authorization':'Bearer '+scoped},json=body)).status_code,401)
                    self.assertEqual(len(seen),1)
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(test(str(Path(folder)/'empty.sqlite3')))


    def test_personal_machine_full_registry_independent_of_sites_key(self):
        import sys
        from hashlib import sha256
        from uuid import uuid4
        from .release_catalog import export
        tool_root=Path(__file__).resolve().parents[2]/'tools/assertion-memory-hosted-mcp'

        async def test(db):
            with patch.object(sys,'path',[str(tool_root),*sys.path]):
                from compile_functional_release import compile_release
                from vm_relay import make_app
            release='assertion-memory-v9.9.9'
            canonical=await export('a'*40)
            pins={k:canonical[k] for k in ('revision','module_sha256','schema_sha256')}
            pins['registry']='base-typed-operations-v1'
            catalog=compile_release(canonical,pins,effects=False)
            profile={'lane':'test','base_release':release,'enabled_capabilities':['baseline_read'],
                     'catalog_sha256':catalog['catalog_sha256'],'require_catalog_identity':True}
            sites,machine,second,legacy=('b'*64,'c'*64,'d'*64,'e'*64)
            def neutral(config,store,service,background):
                ops=create_operations(config,store,background=background,service=service)
                return frozenset(('/memory-api-test','/memory-mcp-test-tools')),make_app(
                    profile,catalog,sites,lambda: (release,'f'*64),operations=ops,machine_digests=tuple(sha256(key.encode('ascii')).hexdigest() for key in (machine,second)))
            app=create_http_app(ServiceConfig(db=db,provider='none',processing_session_ids=[]),
                                legacy,'memory.example',background=False,neutral_factory=neutral)
            async with app.lifespan(), AsyncClient(transport=ASGITransport(app=app),
                                                  base_url='https://memory.example') as client:
                async def send(key, operation='catalog', arguments=None, expected=None, path='/memory-api-test'):
                    body={'request_id':str(uuid4()),'operation':operation,'arguments':arguments or {}}
                    if expected is not None:body['expected']=expected
                    return await client.post(path,headers={'Authorization':'Bearer '+key},json=body)
                for key in (sites,legacy,'0'*64):
                    self.assertEqual((await send(key)).status_code,401)
                self.assertEqual((await send(machine,path='/memory-api-prod')).status_code,401)
                self.assertEqual((await send(machine,path='/memory-mcp-test-tools')).status_code,401)
                read=(await send(sites,path='/memory-mcp-test-tools')).json()
                self.assertNotIn('memory_history',{t['name'] for t in read['tools']})
                value=(await send(machine)).json()
                self.assertEqual(value['capability'],'machine')
                self.assertEqual({t['name'] for t in value['tools']},set(canonical['tools']))
                self.assertEqual((await send(second)).json()['tools'],value['tools'])
                expected={key:value[key] for key in ('release','generation','catalog_sha256')}
                for op,args in [('unknown',{}),('memory_status',{'section':2})]:
                    self.assertEqual((await send(machine,op,args,expected)).status_code,400)
                self.assertEqual((await send(machine,'memory_status')).status_code,400)
                self.assertEqual((await send(machine,'memory_status',expected=expected|{'generation':'0'*64})).status_code,409)
                ok=await send(machine,'memory_status',expected=expected)
                self.assertEqual(ok.status_code,200)
                self.assertEqual(ok.json()['status'],'ok')
                self.assertIsInstance(ok.json()['result'],dict)
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(test(str(Path(folder)/'empty.sqlite3')))


    def test_http_interactive_job_can_be_polled_via_canonical_operation(self):
        import sys
        from threading import Event
        from uuid import uuid4
        from . import query_timing as timing
        from .query_jobs import QueryJobs
        telemetry = []
        from .release_catalog import export
        tool_root=Path(__file__).resolve().parents[2]/'tools/assertion-memory-hosted-mcp'

        async def test(db):
            with patch.object(sys,'path',[str(tool_root),*sys.path]):
                from compile_functional_release import compile_release
                from vm_relay import make_app
            release='assertion-memory-v9.9.9'
            canonical=await export('a'*40)
            pins={k:canonical[k] for k in ('revision','module_sha256','schema_sha256')}
            pins['registry']='base-typed-operations-v1'
            catalog=compile_release(canonical,pins,effects=True)
            profile={'lane':'test','base_release':release,'enabled_capabilities':['baseline_read','intake_process'],
                     'catalog_sha256':catalog['catalog_sha256'],'require_catalog_identity':True,
                     'read_catalog_sha256':compile_release(canonical,pins,effects=False)['catalog_sha256'],
                     'sites_full_catalog':True,'durable_query_jobs':True}
            entered,proceed=Event(),Event()
            calls=[]
            def run(**arguments):
                calls.append(arguments['query'])
                entered.set()
                if not proceed.wait(3):
                    raise RuntimeError('fixture wait expired')
                return {'answer':'Сеанс в 19:30.','answer_accepted':True}
            jobs=QueryJobs(db,run,lambda **_:None,wait_seconds=0.10)
            def neutral(config,store,service,background):
                ops=create_operations(config,store,service=service,query_jobs=jobs)
                app=make_app(profile,catalog,'b'*64,lambda:(release,'f'*64),
                             operations=ops,effect_token='c'*64)
                app.query_jobs=jobs
                return frozenset(('/memory-mcp-test-tools','/memory-mcp-test-effects')),app
            app=create_http_app(ServiceConfig(db=db,provider='none'),'e'*64,'memory.example',
                                background=False,neutral_factory=neutral)
            async with app.lifespan(), AsyncClient(transport=ASGITransport(app=app),
                                                  base_url='https://memory.example') as client:
                path='/memory-mcp-test-effects'
                headers={'Authorization':'Bearer '+'c'*64}
                async def send(operation,arguments,expected=None):
                    body={'request_id':str(uuid4()),'operation':operation,'arguments':arguments}
                    if expected is not None:body['expected']=expected
                    r=await client.post(path,headers=headers,json=body)
                    self.assertEqual(r.status_code,200,r.text)
                    return r.json()
                cat=await send('catalog',{})
                expected={key:cat[key] for key in ('release','generation','catalog_sha256')}
                try:
                    first=(await send('memory_query',{'query':'Когда сеанс?','query_request_id':'fixture'},expected))['result']
                    self.assertIn(first['query_state'],('queued','running'))
                    self.assertTrue(await asyncio.to_thread(entered.wait,1))
                    key=first['query_job_id']
                    pending=(await send('memory_query',{'query_job_id':key},expected))['result']
                    self.assertEqual(pending['query_state'],'running')
                finally:
                    proceed.set()
                for _ in range(30):
                    result=(await send('memory_query',{'query_job_id':key},expected))['result']
                    if result['query_state']=='completed':
                        break
                    await asyncio.sleep(0.03)
                self.assertEqual(result['answer'],'Сеанс в 19:30.')
                self.assertEqual(calls,['Когда сеанс?'])
                submitted = [json.loads(line) for line in telemetry if b'"stage":"vm_http_submission"' in line]
                self.assertEqual(len(submitted),1) # The repeated polls do not produce telemetry rows.
                self.assertEqual(submitted[0]['trace_id'],key)
                self.assertEqual(submitted[0]['status'],'ok')
                self.assertGreaterEqual(submitted[0]['duration_ms'],0)
        with tempfile.TemporaryDirectory() as folder, patch.object(timing,'_write_line',telemetry.append):
            asyncio.run(test(str(Path(folder)/'empty.sqlite3')))


if __name__=='__main__':
    main()
