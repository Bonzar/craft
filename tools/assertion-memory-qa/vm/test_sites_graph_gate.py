"""Release Gate checks without SSH, systemd calls, deployed data or network."""
from contextlib import contextmanager
from hashlib import sha256
from importlib.util import spec_from_file_location, module_from_spec
import json
import os
from pathlib import Path
import tempfile
import unittest

spec=spec_from_file_location('test_sites_graph_gate_impl',Path(__file__).with_name('test-sites-graph-gate.py'))
gate=module_from_spec(spec)
spec.loader.exec_module(gate)
TAG='assertion-memory-v1.2.3'
REV='a'*40
ARCHIVE='b'*64


class GateTests(unittest.TestCase):
    def release(self,root):
        path = root/'releases'/(REV+'-'+ARCHIVE)
        path.mkdir(parents=True)
        manifest=[]
        for name,text in (('BASE_RELEASE_TAG',TAG),('REVISION',REV)):
            raw=(text+'\n').encode('ascii')
            (path/name).write_bytes(raw)
            manifest.append(sha256(raw).hexdigest()+'  ./'+name)
        (path/'SHA256SUMS').write_text('\n'.join(manifest)+'\n')
        (root/'current').symlink_to(path)
        return path

    def payload(self,generation):
        return {'stage':'test','release_version':TAG,'release_generation':generation,
                'global_order':False,'queues':[{'id':v} for v in ('source','maintenance','interactive')],
                'capabilities':{'live':True},'readiness':[]}

    def test_attestation_requires_exact_current_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            path=self.release(root)
            tag,digest=gate.expected_identity(path,root=root)
            self.assertEqual(tag,TAG)
            self.assertEqual(len(digest),64)
            gate.proof(self.payload(digest),(tag,digest))
            (path/'BASE_RELEASE_TAG').write_text('assertion-memory-v1.0.0\n')
            with self.assertRaisesRegex(ValueError,'unverified_active_graph_release'):
                gate.expected_identity(path,root=root)

    def test_missing_capabilities_or_stale_or_cross_lane_fails(self):
        generation='f'*64
        expected=(TAG,generation)
        original=self.payload(generation)
        for value in ({**original,'stage':'prod'}, {**original,'release_generation':'e'*64},
                      {**original,'release_version':'assertion-memory-v2.0.0'},
                      {**original,'queues':[]}, {**original,'readiness':None},
                      {'capabilities':{'live':True}, 'readiness':[]}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                gate.proof(value,expected)

    def test_wire_uses_existing_test_gateway_envelope_exactly(self):
        test_host='fixture.scope.apigw.yandexcloud.net'
        expected=self.payload('f'*64)
        calls=[]
        @contextmanager
        def get(request,timeout):
            calls.append(request)
            self.assertEqual(timeout,5)
            self.assertEqual(request.full_url,'https://'+test_host+gate.PATH)
            self.assertEqual(request.get_method(),'POST')
            self.assertEqual(request.get_header('X-memory-graph-auth'),'Bearer '+'a'*64)
            self.assertEqual(json.loads(request.data),{'path':gate.QUEUE})
            from types import SimpleNamespace as NS
            yield NS(status=200,headers=NS(get_content_type=lambda:'application/json'),
                     read=lambda _:json.dumps(expected).encode())
        self.assertEqual(gate.gateway_check(test_host,'a'*64,get),expected)
        with self.assertRaises(ValueError):
            gate.gateway_check('elsewhere.example','a'*64,get)
        self.assertEqual(len(calls),1)

    def test_exact_service_commands_no_other_unit_or_verb(self):
        self.assertEqual(gate.service_command('restart'),['/usr/bin/sudo','-n','--','/usr/bin/systemctl','restart','memory-site-test-graph.service'])
        self.assertEqual(gate.service_command('stop')[-2:],['stop','memory-site-test-graph.service'])
        for other in ('enable','reload','start','edit','restart memory-site-prod-graph.service'):
            with self.subTest(other=other), self.assertRaises(ValueError):
                gate.service_command(other)

    def socket_tables(self):
        header=b'  sl local_address rem_address st tx_queue tr tm retrnsmt uid timeout inode\n'
        row=b'   0: 0100007F:2273 00000000:0000 0A 00:00 00:00 00 994 0 342123 \n'
        return header,row

    def test_actual_loopback_socket_has_exact_pid_address_and_is_sole_in_both_families(self):
        header,row=self.socket_tables()
        v6=b'   1: 00000000000000000000000000000000:2273 00000000000000000000000000000000:0000 0A 00:00 00:00 00 994 0 342124\n'
        live={b'socket:[342123]'}
        self.assertTrue(gate.exact_listener(header+row,header,live))
        self.assertTrue(gate.exact_listener(header+row,header+v6.replace(b':2273',b':2222'),live))
        self.assertTrue(gate.exact_listener(header+row,header+v6.replace(b' 0A ',b' 01 '),live))
        self.assertFalse(gate.exact_listener(header+row,header,{b'socket:[342124]'}))
        self.assertFalse(gate.exact_listener(header+row+row,header,live))
        self.assertFalse(gate.exact_listener(header+row+row.replace(b'0100007F',b'00000000'),header,live))
        self.assertFalse(gate.exact_listener(header+row+row.replace(b'0100007F',b'0200007F'),header,live))
        self.assertFalse(gate.exact_listener(header+row,header+v6,live))
        self.assertFalse(gate.exact_listener(header+row,b'',live))
        self.assertFalse(gate.exact_listener(header+row,header+b'malformed\n',live))

    def test_serving_identity_checks_exact_physical_process(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as name:
            root=Path(name).resolve()
            release=self.release(root)
            venv=release/'venv'/'bin'
            venv.mkdir(parents=True)
            executable=venv/'python'
            executable.symlink_to(Path(os.sys.executable).resolve())
            proc_root=root/'fake-proc'
            pid=proc_root/'77'
            (pid/'net').mkdir(parents=True)
            (pid/'fd').mkdir()
            (pid/'fd'/'15').symlink_to('socket:[342123]')
            (pid/'exe').symlink_to(executable.resolve())
            (pid/'stat').write_bytes(b'77 (python) S '+b'0 '*18+b'1234 0\n')
            header,row=self.socket_tables()
            (pid/'net'/'tcp').write_bytes(header+row)
            (pid/'net'/'tcp6').write_bytes(header)
            args=b'\0'.join([os.fsencode(executable),b'-s',b'-B',os.fsencode(gate.WRAPPER),b''])
            env=lambda physical: b'\0'.join(os.fsencode(f'{k}={v}') for k,v in {**gate.LOCAL_ENV,'PYTHONPATH':physical}.items())
            (pid/'cmdline').write_bytes(args)
            (pid/'environ').write_bytes(env(release))
            def read_unit(command,**_):
                self.assertEqual(command[-1],gate.UNIT)
                return SimpleNamespace(stdout=f'MainPID=77\nActiveState=active\nUser={gate.ACCOUNT}\n'.encode())
            def identity():
                return gate.serving_identity(release,proc_root=proc_root,read_unit=read_unit)
            self.assertEqual(identity(),('77',b'1234'))
            (pid/'environ').write_bytes(env(root/'current'))
            with self.assertRaisesRegex(ValueError,'process_scope'):
                identity()
            (pid/'environ').write_bytes(env(release))
            (pid/'cmdline').write_bytes(args.replace(os.fsencode(release),os.fsencode(root/'current')))
            with self.assertRaisesRegex(ValueError,'graph_process'):
                identity()
            (pid/'cmdline').write_bytes(args)
            (pid/'net'/'tcp6').unlink()
            with self.assertRaises(FileNotFoundError):
                identity()

    def test_local_network_request_requires_real_read_route_denial(self):
        from urllib.error import HTTPError
        for code in (401,403,404,405,500):
            def fake(req,timeout):
                self.assertEqual(req.full_url,'http://127.0.0.1:8819'+gate.PATH)
                self.assertIsNone(req.get_header('Authorization'))
                self.assertIsNone(req.get_header('X-memory-graph-auth'))
                self.assertEqual(json.loads(req.data),{'path':gate.QUEUE})
                raise HTTPError(req.full_url,code,'fixture',None,None)
            if code in (401,403):
                gate.local_wrapper_denial(fake)
            else:
                with self.assertRaises(ValueError):
                    gate.local_wrapper_denial(fake)

    def test_local_projection_wrong_generation_or_pid_restart_cannot_pass(self):
        generation='f'*64
        expected=(TAG,generation)
        value={'stage':'test','release_version':TAG,'release_generation':generation,'global_order':False,
               'queue_ids':['source','maintenance','interactive']}
        gate.local_qualification('/synthetic',expected,identity=lambda _:('1',b'102'),
                                 denial=lambda:None,project=lambda _:value)
        for change in ({'stage':'prod'},{'release_generation':'e'*64},{'queue_ids':[]},{'text':'private'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                gate.local_qualification('/synthetic',expected,identity=lambda _:('1',b'102'),
                                         denial=lambda:None,project=lambda _:{**value,**change})
        ids=iter((('1',b'102'),('2',b'103')))
        with self.assertRaises(ValueError):
            gate.local_qualification('/synthetic',expected,identity=lambda _:next(ids),denial=lambda:None,
                                     project=lambda _:value)
