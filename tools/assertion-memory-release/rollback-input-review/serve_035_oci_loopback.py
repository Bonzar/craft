"""Fixed read-only OCI Distribution endpoint for an ephemeral build runner."""
from hashlib import file_digest
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import shutil
import stat

from compose_035_oci_descriptor import COMPOSED, IMAGE_MANIFEST
from verify_035_oci_root import PIN, unique

REPO = "/v2/rollback-base035"
API = "/v2/"
BIND = "127.0.0.1"
BLOB_TYPE = "application/octet-stream"


def prove(layout):
    """Return only the preverified six response paths, all exact and finite."""
    pin = json.loads(PIN.read_bytes(), object_pairs_hook=unique)
    md, msize = COMPOSED["manifest"]
    cd, csize = COMPOSED["config"]
    root = layout / "blobs" / "sha256"

    def verified(digest, size):
        p = root / digest.removeprefix("sha256:")
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as f:
            st = os.fstat(f.fileno())
            if (not stat.S_ISREG(st.st_mode) or st.st_size != size
                    or file_digest(f, "sha256").hexdigest() != digest[7:]):
                raise ValueError("Invalid pinned OCI response")
        return p

    manifest = verified(md, msize)
    doc = json.loads(manifest.read_bytes(), object_pairs_hook=unique)
    from compose_035_oci_descriptor import LAYER
    if (doc["config"]["digest"] != cd or doc["config"]["size"] != csize
            or [d["digest"] for d in doc["layers"]] != pin["layers"] + [LAYER]):
        raise ValueError("Wrong exact OCI graph")
    out = {REPO + "/manifests/" + md: (manifest, md, msize, IMAGE_MANIFEST)}
    for d in (doc["config"], *doc["layers"]):
        digest, size = d["digest"], d["size"]
        out[REPO + "/blobs/" + digest] = (verified(digest, size), digest, size, BLOB_TYPE)
    return out


def handler(routes):
    class FixedRegistry(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.respond(True)

        def do_HEAD(self):
            self.respond(False)

        def refuse(self):
            self.send_response(405)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_PUT = do_POST = do_DELETE = do_PATCH = do_OPTIONS = do_CONNECT = refuse

        def respond(self, body):
            if self.path == API:
                self.send_response(200)
                self.send_header("Docker-Distribution-API-Version", "registry/2.0")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "2")
                self.end_headers()
                if body:
                    self.wfile.write(b"{}")
                return
            if self.path not in routes:
                self.send_error(404)
                return
            p, digest, size, media = routes[self.path]
            # Refuse ranges to avoid reporting partial bytes as the full digest.
            if self.headers.get("Range") is not None:
                self.send_error(416)
                return
            fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as source:
                st = os.fstat(source.fileno())
                if (not stat.S_ISREG(st.st_mode) or st.st_size != size
                        or file_digest(source, "sha256").hexdigest() != digest[7:]):
                    self.send_error(503)
                    return
                source.seek(0)
                self.send_response(200)
                self.send_header("Docker-Distribution-API-Version", "registry/2.0")
                self.send_header("Docker-Content-Digest", digest)
                self.send_header("Content-Type", media)
                self.send_header("Content-Length", str(size))
                self.end_headers()
                if body:
                    shutil.copyfileobj(source, self.wfile, 1024 * 1024)
    return FixedRegistry


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("layout", type=Path)
    args = parser.parse_args()
    with HTTPServer((BIND, 0), handler(prove(args.layout))) as server:
        print(server.server_port, flush=True)
        server.serve_forever()
