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


    def test_only_documented_cloudfront_blob_host_and_no_credentials(self):
        documented="https://production.cloudfront.docker.com/blob?fixture=signature"
        req=Request("https://registry-1.docker.io/v2/library/ubuntu/blobs/sha256:fixture",
                    headers={"Authorization":"Bearer fixture"})
        allowed=m.PublicRedirect(m.CDN)
        result=allowed.redirect_request(req,None,307,"",{},documented)
        self.assertIsNone(result.get_header("Authorization"))
        for url in (documented.replace("https://","http://"),
                    documented.replace(".com/",".com.invalid/"),
                    documented.replace(".com/",".com:444/")):
            with self.subTest(url=url),self.assertRaises(m.PublicIngressOriginError):
                allowed.redirect_request(req,None,307,"",{},url)
        with self.assertRaises(m.PublicIngressOriginError):
            m.PublicRedirect({m.REGISTRY}).redirect_request(req,None,307,"",{},documented)

    def test_materializer_reports_fixed_blob_pin_without_signed_url_or_token(self):
        import json
        pin=json.loads(m.PIN.read_bytes())
        last=[]
        secret="fixture-secret-token"
        def registry(url, allowed, output=None, **kwargs):
            if output is None:
                self.assertEqual(allowed,{"auth.docker.io"})
                return json.dumps({"token":secret}).encode()
            last.append(url)
            if "/manifests/" in url:
                self.assertEqual(allowed,{m.REGISTRY})
                return
            self.assertEqual(allowed,m.CDN)
            self.assertEqual(kwargs["digest"],pin["config"][7:])
            self.assertEqual(kwargs["headers"]["Authorization"],"Bearer "+secret)
            raise m.PublicIngressOriginError("https://not-approved.invalid/redirect?signature="+secret)
        with TemporaryDirectory() as folder:
            stage=Path(folder)
            with self.assertRaises(m.PublicIngressOriginError) as caught:
                m.materialize(stage,get=registry,
                              stage_keyring=lambda *_:self.fail("unexpected later side effect"))
            self.assertEqual(str(caught.exception),
                             "Unapproved public ingress origin for Docker Hub config "+pin["config"])
            self.assertTrue(caught.exception.__suppress_context__)
            self.assertNotIn(secret,str(caught.exception))
            self.assertNotIn("not-approved.invalid",str(caught.exception))
            self.assertEqual(len(last),3)
            self.assertFalse((stage/"native-libraries").exists())
