"""Fixture-only transport: digest gate and safe public redirect."""
import hashlib
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.request import Request

import materialize_035_public_runtime as m


class PublicRuntime(unittest.TestCase):
    def test_public_blob_must_match_exact_pin_before_commit(self):
        with TemporaryDirectory() as folder:
            output = Path(folder)/"data"
            with patch.object(m, "build_opener") as opener:
                opener.return_value.open.side_effect = lambda *a, **k: BytesIO(b"exact")
                opts = {"limit":5, "size":5, "digest":"0"*64}
                with self.assertRaisesRegex(ValueError, "digest"):
                    m.fetch("https://nodejs.org/data", {"nodejs.org"}, output, **opts)
                self.assertEqual(list(output.parent.iterdir()), [])
                m.fetch("https://nodejs.org/data", {"nodejs.org"}, output,
                        **(opts | {"digest":hashlib.sha256(b"exact").hexdigest()}))
                self.assertEqual(output.read_bytes(), b"exact")

    def test_redirect_allowlist_and_no_auth_forwarding(self):
        redirect = m.PublicRedirect(m.CDN)
        req = Request("https://registry-1.docker.io/v2/blob", headers={"Authorization":"Bearer public"})
        with self.assertRaises(ValueError):
            redirect.redirect_request(req,None,302,None,{},"https://unexpected.invalid/blob")
        result = redirect.redirect_request(req,None,302,None,{},"https://production.cloudflare.docker.com/blob")
        self.assertIsNone(result.get_header("Authorization"))
