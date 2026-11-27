from hashlib import sha256
from http.client import HTTPConnection
from http.server import HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

import serve_035_oci_loopback as s


class LoopbackDistribution(unittest.TestCase):
    def test_only_exact_digest_get_head_and_no_mutation(self):
        with TemporaryDirectory() as t:
            p = Path(t)/"blob"
            p.write_bytes(b"fixture")
            digest = "sha256:" + sha256(b"fixture").hexdigest()
            route = s.REPO + "/blobs/" + digest
            with HTTPServer((s.BIND, 0), s.handler({route:(p,digest,7,s.BLOB_TYPE)})) as srv:
                self.assertEqual(srv.server_address[0], "127.0.0.1")
                thread = Thread(target=srv.serve_forever)
                thread.start()
                def request(method, path, headers=None):
                    with_client = HTTPConnection(s.BIND, srv.server_port, timeout=3)
                    try:
                        with_client.request(method, path, headers=headers or {})
                        result = with_client.getresponse()
                        return result.status, result.getheader("Docker-Content-Digest"), result.read()
                    finally:
                        with_client.close()
                try:
                    self.assertEqual(request("GET", "/v2/"), (200, None, b"{}"))
                    self.assertEqual(request("HEAD", route), (200, digest, b""))
                    self.assertEqual(request("GET", route), (200, digest, b"fixture"))
                    for invalid in (route+"0", route+"?extra", "https://example.invalid/", s.REPO+"/tags/list"):
                        self.assertEqual(request("GET", invalid)[0], 404)
                    for method in ("POST","PUT","PATCH","DELETE","CONNECT"):
                        self.assertEqual(request(method,route)[0], 405)
                    self.assertEqual(request("GET", route, {"Range":"bytes=0-1"})[0],416)
                    p.write_bytes(b"changed")
                    self.assertEqual(request("GET",route)[0],503)
                finally:
                    srv.shutdown()
                    thread.join()
