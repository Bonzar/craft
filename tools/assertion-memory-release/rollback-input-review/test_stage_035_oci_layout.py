from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import compose_035_oci_descriptor as c
import stage_035_oci_layout as s


def d(x):
    return "sha256:" + sha256(x).hexdigest()


class Ingress(unittest.TestCase):
    def test_stage_four_related_objects_and_refuse_changed_layer(self):
        with TemporaryDirectory() as t:
            root = Path(t)
            base = root / "base"
            base.mkdir()
            original = b"fixture base layer"
            native = b"fixture native layer"
            (base / d(original)[7:]).write_bytes(original)
            conf = b'{"fixture":"config"}'
            man = c.encode({"layers": [{"digest": d(x), "size": len(x)} for x in (original, native)]})
            objects = {d(conf): conf, d(man): man, d(native): native}
            pin = {"layers": [d(original)]}
            report = {"manifest": {"digest": d(man)}}
            with patch.object(s, "compose", return_value=(objects, b"fixture index", report)):
                layout = root / "out"
                self.assertEqual(s.stage(base, root / "native", layout, pin), report)
                self.assertEqual({p.name for p in (layout / "blobs/sha256").iterdir()},
                                 {key[7:] for key in (*objects, d(original))})
                self.assertEqual(json.loads((layout / "oci-layout").read_bytes()),
                                 {"imageLayoutVersion": "1.0.0"})
                with self.assertRaises(ValueError):
                    s.stage(base, root / "native", layout, pin)
                (base / d(original)[7:]).write_bytes(b"X" + original[1:])
                with self.assertRaisesRegex(ValueError, "changed"):
                    s.stage(base, root / "native", root / "bad", pin)
                self.assertFalse((root / "bad").exists())

    def test_ingress_accepts_only_exact_local_manifest_repo_digest(self):
        manifest = "example.invalid/base@" + c.COMPOSED["manifest"][0]
        s.check_repo_digest(manifest, "unrelated\n" + manifest + "\n")
        for incorrect in ("example.invalid/base:" + "test",
                          "example.invalid/base@" + c.COMPOSED["config"][0],
                          "example.invalid/base@" + c.COMPOSED["index"][0],
                          "elsewhere.invalid/base@" + c.COMPOSED["manifest"][0], ""):
            with self.subTest(incorrect=incorrect), self.assertRaises(ValueError):
                s.check_repo_digest(manifest, incorrect)
        with self.assertRaises(ValueError):
            s.check_repo_digest("example.invalid/base:latest", "example.invalid/base:latest")
