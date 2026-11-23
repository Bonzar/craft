import http.client
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
    'index.html': b'<html>synthetic</html>',
    'assets/index-ab.js': b'window.synthetic=true;',
    'assets/index-ab.css': b'.fixture{}',
    'assets/graph.worker-ab.js': b'const fictional=true;',
}


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
        (self.root/'current').write_text(self.release+'\n')
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


if __name__ == '__main__':
    unittest.main()
