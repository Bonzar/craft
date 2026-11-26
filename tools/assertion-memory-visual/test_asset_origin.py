import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
from threading import Thread
import unittest


def load(filename):
    spec = importlib.util.spec_from_file_location(filename, Path(__file__).with_name(filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


origin = load('asset-origin.py')
TOKEN = '1' * 64
ORIGINAL = {
    'index.html': (b'<html><link rel="stylesheet" href="./assets/index-ab.css">'
                   b'<script type="module" src="./assets/index-ab.js"></script>synthetic</html>'),
    'assets/index-ab.js': b'window.synthetic=true;',
    'assets/index-ab.css': b'.fixture{}',
    'assets/graph.worker-ab.js': b'const fictional=true;',
}


class BatchSnapshotTest(unittest.TestCase):
    def test_nested_members_require_exact_control_free_shape(self):
        result=dict(id='source',status='active',ordering='batch_fifo_then_available_at_then_id',
                    total=1,source_count=2,next_offset=None,items=[
            dict(id='batch:'+'a'*64,status='ready',phase='extract',available_at='synthetic',
                 position=1,member_count=2,members=[{'id':'first','preview_revision':'b'*32},{'id':'second'}])])
        self.assertTrue(origin.snapshot_queues([result],1,0))
        from copy import deepcopy
        for altered in ('count','duplicate','raw','delete','missing','wrong_lane'):
            value=deepcopy(result)
            row=value['items'][0]
            if altered=='count': row['member_count']=3
            if altered=='duplicate': row['members'][1]['id']=row['members'][0]['id']
            if altered=='raw': row['members'][1]['source_preview']='PRIVATE'
            if altered=='delete': row['remove_revision']='c'*32
            if altered=='missing': del value['source_count']
            if altered=='wrong_lane': value['id']='maintenance'
            with self.subTest(altered=altered):
                self.assertFalse(origin.snapshot_queues([value],1,0))


class OriginTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manifest = json.dumps({name: origin.digest(value) for name, value in sorted(ORIGINAL.items())},
                                   separators=(',', ':')).encode()+b'\n'
        self.release = origin.digest(self.manifest)
        self.directory = self.root / 'releases' / self.release
        (self.directory/'assets').mkdir(parents=True)
        (self.directory/'manifest.json').write_bytes(self.manifest)
        (self.root/'current').symlink_to(self.directory)
        for name, data in ORIGINAL.items():
            (self.directory/name).write_bytes(data)
        self.server = origin.make_server(self.root,TOKEN,port=0)
        thread = Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, value=b'{"op":"current"}', token=TOKEN, path=origin.PATH,
                method='POST', mime='application/json'):
        headers = {'Content-Type': mime}
        if token is not None:
            headers['Authorization'] = 'Bearer '+token
        cli = http.client.HTTPConnection(*self.server.server_address,timeout=2)
        try:
            cli.request(method,path,value,headers)
            response = cli.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            cli.close()

    def raw(self, raw):
        with socket.create_connection(self.server.server_address,timeout=2) as stream:
            stream.sendall(raw)
            return stream.makefile('rb').readline()

    def test_exact_route_secrets_and_request_bounds(self):
        for token in [None, '2'*64, TOKEN.upper()+'x']:
            self.assertEqual(self.request(token=token)[0],401)
        for path in [origin.PATH+'?x=1','/'+origin.PATH,'/memory-graph-test-read']:
            self.assertEqual(self.request(path=path)[0],404)
        self.assertEqual(self.request(method='GET')[0],404)
        self.assertEqual(self.request(mime='text/plain')[0],415)
        self.assertEqual(self.request(value=b'x'*513)[0],413)
        self.assertEqual(self.request(value=b'[1]')[0],400)
        self.assertEqual(self.request(value=b'not json')[0],404)
        auth=f'Authorization: Bearer {TOKEN}\r\n'.encode()
        h=b'POST '+origin.PATH.encode()+b' HTTP/1.1\r\n'+auth+b'Content-Type: application/json\r\n'
        self.assertIn(b'401',self.raw(h+auth+b'Content-Length: 2\r\n\r\n{}'))
        self.assertIn(b'400',self.raw(h+b'Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}'))
        self.assertIn(b'400',self.raw(h+b'Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n'))

    def test_hash_mime_exact_bytes_and_tamper(self):
        code, head, body = self.request()
        self.assertEqual((code,head['Content-Type'],json.loads(body)),(200,'application/json',{'digest':self.release}))
        self.assertEqual(head['Cache-Control'],'private, no-store, no-transform')
        self.assertNotIn('Server',head)
        for name, data in ORIGINAL.items():
            selection = json.dumps({'op':'asset','name':name,'digest':self.release})
            code, head, body = self.request(value=selection)
            self.assertEqual((code,body),(200,data))
            self.assertEqual(head['Content-Type'],origin.MIME[Path(name).suffix])
        request = lambda name, dig=self.release: self.request(value=json.dumps({'op':'asset','name':name,'digest':dig}))[0]
        self.assertEqual(request('index.html','F'*64),400)
        self.assertEqual(request('assets/../../secret'),400)
        self.assertEqual(request('index.html','f'*64),404)
        (self.directory/'index.html').write_bytes(b'changed')
        self.assertEqual(request('index.html'),404)
        (self.directory/'manifest.json').write_bytes(self.manifest+b' ')
        self.assertEqual(self.request()[0],404)

    def test_atomic_link_in_scope_only_and_pinned_asset_needs_valid_active_pointer(self):
        current = self.root/'current'
        fixed_asset=json.dumps({'op':'asset','name':'index.html','digest':self.release})
        current.unlink()
        current.write_text(self.release+'\n')  # legacy preview format is not a valid shared pointer
        self.assertEqual(self.request()[0],404)
        current.unlink()
        current.symlink_to(Path('/tmp/another-scope/releases')/self.release)
        self.assertEqual(self.request()[0],404)
        self.assertEqual(self.request(value=fixed_asset)[0],404)
        current.unlink()
        current.symlink_to(Path('releases')/self.release)
        self.assertEqual(self.request()[0],200)


    def test_queue_via_two_real_loopback_http_servers(self):
        observed = []
        queue_token = '3' * 64
        snapshot = {'stage':'test', 'release_version':'assertion-memory-v0.1.16',
                    'release_generation':'4'*64, 'global_order':False,
                    'snapshot_at':'2026-11-25T20:00:00+00:00',
                    'queues':[], 'processing_control':{'paused':False,'revision':'5'*32,'can_change':False}}

        class Base(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                observed.append((self.path, dict(self.headers.items())))
                self.send_response(snapshot.pop('_status',200))
                self.send_header('Content-Type','application/json')
                self.end_headers()
                self.wfile.write(json.dumps(snapshot).encode())

        def start(server):
            Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            return server

        base=start(ThreadingHTTPServer(('127.0.0.1',0),Base))
        addr='http://127.0.0.1:'+str(base.server_port)
        queue={'op':'queue_status','limit':50,'offset':55}
        ask=lambda **kwargs:self.request(value=json.dumps(queue),**kwargs)
        self.assertEqual(ask()[0],404) # dark
        health_key='7'*64
        self.server=start(origin.make_server(self.root,TOKEN,port=0,queue_origin=addr,queue_token=queue_token,
                                            runtime_health=('8'*64,health_key)))
        self.assertEqual(ask(token=None)[0],401)
        queue.update(limit=True)
        self.assertEqual(ask()[0],400)
        queue.update(limit=51)
        self.assertEqual(ask()[0],400)
        queue.update(limit=50,stage='test')
        self.assertEqual(ask()[0],400)
        del queue['stage']
        self.assertEqual(observed,[])
        code,hdr,body=ask()
        self.assertEqual((code,hdr['Content-Type']),(200,'application/json'))
        self.assertEqual(json.loads(body),{**snapshot,'capabilities':{'live':False},'readiness':[]})
        [(path,headers)]=observed
        self.assertEqual(path,'/api/v1/queue-status?limit=50&offset=55')
        self.assertEqual(headers['Authorization'],'Bearer '+queue_token)
        self.assertNotIn(TOKEN,str(headers))
        self.assertNotIn('Cookie',headers)
        snapshot.update(stage='prod')
        self.assertEqual(ask()[0],503)
        snapshot.update(stage='test',_status=302)
        self.assertEqual(ask()[0],503)
        snapshot['processing_control']['can_change']=True
        code,_,data=ask()
        self.assertEqual(code,200)
        legacy=json.loads(data)
        self.assertIs(legacy['processing_control']['can_change'],False)
        self.assertEqual(legacy['capabilities'],{'live':False})
        snapshot['processing_control']['can_change']='yes'
        self.assertEqual(ask()[0],503)
        snapshot['processing_control']['can_change']=False
        snapshot['queues']=[dict(id='source',status='active',ordering='available_at_then_id',
                                 total=56,next_offset=None,items=[dict(
                                     id='fixture',status='ready',phase='extract',position=56,
                                     available_at='2026-11-25T20:00:00+00:00')])]
        self.assertEqual(ask()[0],200)
        preserved=snapshot['queues']
        snapshot['queues']=[]
        cli=http.client.HTTPConnection(*self.server.server_address)
        with self.subTest('runtime health is local and independently authorized'):
            try:
                cli.request('GET','/__visual-runtime-health',headers={'Authorization':'Bearer '+TOKEN})
                response=cli.getresponse()
                self.assertEqual(response.status,401)
                response.read()
                cli.request('GET','/__visual-runtime-health',headers={'Authorization':'Bearer '+health_key})
                response=cli.getresponse()
                self.assertEqual(response.status,200)
                self.assertEqual(json.loads(response.read()),{'stage':'test','runtime_entry_sha':'8'*64,
                     'static_digest':self.release,'queue_ready':True})
            finally:
                cli.close()
        snapshot['queues']=preserved
        snapshot['queues'][0]['items'][0]['unexpected_content']='not part of the contract'
        self.assertEqual(ask()[0],503)
        self.assertEqual(self.request()[0],200) # legacy asset unaffected
        for bad in ('https://127.0.0.1:100','http://localhost:100','http://127.0.0.1:100/path'):
            with self.assertRaises(ValueError):
                origin.make_server(self.root,TOKEN,port=0,queue_origin=bad,queue_token=queue_token)
        with self.assertRaises(ValueError):
            origin.make_server(self.root,TOKEN,port=0,queue_origin=addr,queue_token=TOKEN)
        with self.assertRaises(ValueError):
            origin.make_server(self.root,TOKEN,port=0,environment='prod',queue_origin=addr,queue_token=queue_token)


if __name__ == '__main__':
    unittest.main()
