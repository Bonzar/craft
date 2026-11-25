"""Release Gate checks without SSH, systemd calls, deployed data or network."""
from contextlib import contextmanager
from hashlib import sha256
from importlib.util import spec_from_file_location, module_from_spec
import json
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
