import json
import unittest
from unittest.mock import patch
import compose_035_oci_descriptor as c


class ColdDescriptor(unittest.TestCase):
    def test_deterministic_extension_preserves_base_layers_and_diff_ids(self):
        config = {"rootfs": {"type": "layers", "diff_ids": ["sha256:"+"a"*64]},
                  "history": [{"created_by": "base"}, {"empty_layer": True}]}
        base_layer = {"mediaType": c.IMAGE_LAYER+"+gzip", "digest": "sha256:"+"b"*64, "size": 99}
        manifest = {"layers": [base_layer], "mediaType": c.IMAGE_MANIFEST,
                    "config": {"mediaType": c.IMAGE_CONFIG}, "annotations": {"ignored": "base-only"}}
        layer = b"synthetic-only"
        platform = {"os": "linux", "architecture": "amd64"}
        d = c.descriptor(layer, c.IMAGE_LAYER)
        with patch.multiple(c, LAYER=d["digest"], LAYER_SIZE=d["size"]):
            got = c.metadata(config, manifest, platform, layer)
            self.assertEqual(got, c.metadata(config, manifest, platform, layer))
            blobs, index, md = got
            result = json.loads(blobs[md["digest"]])
            self.assertEqual(result["layers"], [base_layer, d])
            self.assertNotIn("annotations", result)
            self.assertEqual(json.loads(blobs[result["config"]["digest"]])["rootfs"]["diff_ids"],
                             config["rootfs"]["diff_ids"] + [d["digest"]])
            self.assertEqual(json.loads(index)["manifests"], [md | {"platform": platform}])
            with self.assertRaises(ValueError):
                c.metadata(config, manifest, platform, b"tampered-bytes")
            with self.assertRaises(ValueError):
                c.metadata(config | {"history": []}, manifest, platform, layer)


if __name__ == "__main__":
    unittest.main()
