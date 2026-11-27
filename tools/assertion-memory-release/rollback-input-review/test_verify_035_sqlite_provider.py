from dataclasses import replace
import hashlib
import io
import lzma
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import verify_035_sqlite_provider as v


class SignedIndexAndLayer(unittest.TestCase):
    def verify(self, which, *, suite=None, digest=None, package=b"fixture", duplicate=False):
        sha = hashlib.sha256(b"fixture").hexdigest()
        pin = replace(which, sha=sha, size=7)
        index = f"""Package: {pin.package}
Architecture: amd64
Version: {pin.version}
Filename: {pin.pool}/{v.filename(pin)}
Size: 7
SHA256: {sha}
"""
        z = lzma.compress((index + "\n" + index if duplicate else index).encode())
        release = f"""Origin: Ubuntu
Codename: resolute
Suite: {suite or pin.suite}
Date: Wed, 04 Nov 2026 00:00:00 UTC
Valid-Until: Wed, 11 Nov 2026 00:00:00 UTC
SHA256:
 {digest or hashlib.sha256(z).hexdigest()} {len(z)} {v.INDEX}
"""
        v.verify_chain(pin, release, z, package)

    def test_both_pins_and_required_distinct_suites(self):
        for pin in v.PINS:
            self.verify(pin)
            other = next(p for p in v.PINS if p != pin)
            for args in ({"suite": other.suite}, {"digest": "0"*64},
                         {"package": b"changed"}, {"duplicate": True}):
                with self.subTest(pin=pin.package, args=args), self.assertRaises(ValueError):
                    self.verify(pin, **args)

    def providers(self):
        return {p.package: (p.soname + ".7.1", b"fixture") for p in v.PINS}

    def test_exact_members_and_order(self):
        inputs = self.providers()
        layer = v.make_layer(inputs)
        self.assertEqual(layer, v.make_layer(dict(reversed(list(inputs.items())))))
        with tarfile.open(fileobj=io.BytesIO(layer)) as tar:
            self.assertEqual([m.name.rsplit("/", 1)[-1] for m in tar.getmembers()],
                             [f for p in v.PINS for f in (p.soname + ".7.1", p.soname)])
            for m in tar.getmembers():
                self.assertEqual((m.uid, m.gid, m.mtime), (0, 0, 0))
        for bad in ({}, inputs | {"extraneous": ("exec", b"x")},
                    inputs | {v.FFI.package: ("../../escape", b"x")}):
            with self.assertRaises(ValueError):
                v.make_layer(bad)

    def test_wrong_staged_layer_and_link_rejected(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p/"ubuntu-archive-keyring.gpg").write_bytes(b"fixture")
            for pin in v.PINS:
                d = p/pin.suite
                d.mkdir()
                for name in ("InRelease", "Packages.xz", v.filename(pin)):
                    (d/name).write_bytes(b"fixture")
            layer = p/"native-library-layer.tar"
            expected_layer = v.make_layer(self.providers())
            with patch.multiple(v, NATIVE_LAYER_SIZE=len(expected_layer),
                                NATIVE_LAYER_SHA=hashlib.sha256(expected_layer).hexdigest()), \
                 patch.object(v, "consumers", return_value={p.consumer: Path(p.consumer) for p in v.PINS}), \
                 patch.object(v, "verify", side_effect=lambda pin, *args: self.providers()[pin.package]):
                self.assertEqual(v.compose(p, output=layer), v.compose(p))
                layer.write_bytes(b"tampered")
                with self.assertRaises(ValueError):
                    v.compose(p)
                path = p/v.FFI.suite/"InRelease"
                path.unlink()
                path.symlink_to("Packages.xz")
                with self.assertRaises(ValueError):
                    v.compose(p)
