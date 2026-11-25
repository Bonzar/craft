from contextlib import contextmanager
from email.message import Message
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

import sites_smoke as smoke

ASSETS = {
    "index.html": b"<script src='./assets/index-proof.js'></script>",
    "assets/index-proof.js": b"void 0;",
    "assets/index-proof.css": b"html{background:#000}",
    "assets/graph.worker-proof.js": b"void 1;",
}
OLD = "e" * 64


class Response(BytesIO):
    def __init__(self, url, status, headers, body=b""):
        super().__init__(body)
        self.url = url
        self.status = status
        self.headers = Message()
        for name, value in headers:
            self.headers[name] = value

    def geturl(self):
        return self.url


class SitesSmokeTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.archive = Path(directory.name) / "visual.tar"
        blob = (json.dumps({name: sha256(data).hexdigest() for name, data in ASSETS.items()}) + "\n").encode()
        self.expected = sha256(blob).hexdigest()
        with tarfile.open(self.archive, "w") as tar:
            for name, data in {"manifest.json": blob, **ASSETS}.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                tar.addfile(member, BytesIO(data))
        self.prefix = "/visual/" + self.expected + "/"
        self.calls = []
        self.lane = "test"
        self.alter = lambda path, response: response

    @contextmanager
    def get(self, url, *, timeout):
        self.assertEqual(timeout, smoke.TIMEOUT)
        self.assertTrue(url.startswith(smoke.ORIGINS[self.lane] + "/"))
        path = url.removeprefix(smoke.ORIGINS[self.lane])
        self.calls.append(path)
        headers = [("Cache-Control", "private, no-store, no-transform"),
                   ("x-memory-visual-digest", self.expected)]
        if path == "/":
            response = Response(url, 302, headers + [("Location", self.prefix)])
        else:
            self.assertTrue(path.startswith(self.prefix))
            name = path.removeprefix(self.prefix) or "index.html"
            media = {"js": "text/javascript", "css": "text/css", "html": "text/html"}[name.rsplit(".", 1)[1]]
            response = Response(url, 200, headers + [("Content-Type", media + "; charset=utf-8")], ASSETS[name])
        with self.alter(path, response) as response:
            yield response

    def run_probe(self):
        return smoke.verify_sites_release(self.lane, self.archive, self.expected, self.get)

    def test_exact_current_html_and_assets_and_not_history(self):
        for self.lane in ("test", "prod"):
            self.calls.clear()
            self.assertEqual(self.run_probe(), {"stage": self.lane, "manifest_sha256": self.expected, "verified_assets": 4})
            self.assertEqual(self.calls, ["/", self.prefix] + [self.prefix + name for name in sorted(ASSETS)
                             if name != "index.html"] + ["/"])
            self.assertFalse(any(OLD in path for path in self.calls))

    def test_rejects_fallback_even_if_digest_matches(self):
        def fallback(_path, response):
            response.headers["x-memory-visual-source"] = "pinned-ci"
            return response

        self.alter = fallback
        with self.assertRaisesRegex(smoke.SmokeError, "fallback"):
            self.run_probe()
        self.assertEqual(self.calls, ["/"])

    def test_old_root_redirect_and_wrong_stage_are_not_followed(self):
        for destination in ["/visual/" + OLD + "/", smoke.ORIGINS["prod"] + self.prefix, "/login"]:
            with self.subTest(destination=destination):
                def move(path, response):
                    if path == "/":
                        response.headers.replace_header("location", destination)
                    return response

                self.calls.clear()
                self.alter = move
                with self.assertRaisesRegex(smoke.SmokeError, "different release"):
                    self.run_probe()
                self.assertEqual(self.calls, ["/"])

    def test_rejects_mismatched_body_html_and_static_files(self):
        for bad in [self.prefix] + [self.prefix + name for name in ASSETS if name != "index.html"]:
            with self.subTest(bad=bad):
                self.alter = lambda path, response: (Response(response.url, response.status, list(response.headers.items()),
                                                     b"old file") if path == bad else response)
                with self.assertRaisesRegex(smoke.SmokeError, "approved archive"):
                    self.run_probe()

    def test_rejects_pointer_drift_during_asset_checks(self):
        def moved(path, response):
            if path == "/" and self.calls.count("/") == 2:
                response.headers.replace_header("x-memory-visual-digest", OLD)
            return response
        self.alter = moved
        with self.assertRaisesRegex(smoke.SmokeError, "digest"):
            self.run_probe()

    def test_rejects_false_success_and_ambiguous_status(self):
        for headers in [[("x-memory-visual-error", "gateway_status")],
                        [("x-memory-visual-digest", self.expected)],
                        [("Cache-Control", "public")]]:
            with self.subTest(headers=headers):
                self.alter = lambda _path, response: Response(response.url, response.status,
                                                              list(response.headers.items()) + headers)
                with self.assertRaises(smoke.SmokeError):
                    self.run_probe()

    def test_manifest_mismatch_does_not_use_transport(self):
        with self.assertRaisesRegex(smoke.SmokeError, "Manifest digest"):
            smoke.verify_sites_release("test", self.archive, OLD, self.get)
        self.assertEqual(self.calls, [])

    def test_transport_credentials_are_scope_bound_and_redirect_never_followed(self):
        for bad in [{}, {"oai-authenticated-user-id": "fake"}, {"Cookie": "a=b\r\nRange: hello"}]:
            with self.assertRaises(smoke.SmokeError):
                smoke.scoped_sites_get("test", bad)
        with patch.object(smoke, "build_opener") as builder:
            get = smoke.scoped_sites_get("test", {"OAI-Sites-Authorization": "test-only-example"})
            opener = builder.return_value
            for url in [smoke.ORIGINS["prod"] + "/", smoke.ORIGINS["test"] + "/mcp",
                        smoke.ORIGINS["test"] + "/?external=true"]:
                with self.assertRaises(smoke.SmokeError), get(url, timeout=smoke.TIMEOUT):
                    self.fail("Credential used outside the visual release probe")
            self.assertFalse(opener.open.called)
            origin = smoke.ORIGINS["test"]
            redirect = Message()
            redirect["Location"] = smoke.ORIGINS["prod"] + "/"
            opener.open.side_effect = HTTPError(origin + "/", 302, "redirect", redirect, BytesIO())
            with get(origin + "/", timeout=smoke.TIMEOUT) as response:
                self.assertEqual(response.status, 302)
            self.assertIsInstance(builder.call_args.args[1], smoke.NoRedirect)
            self.assertEqual(opener.open.call_count, 1)


if __name__ == "__main__":
    unittest.main()
