import json
from hashlib import sha256
from unittest import TestCase
from unittest.mock import patch
import sealed_host_install_bundle as b

class BundleManifest(TestCase):
    def test_closed_source_and_output_pins_bind_all_exact_paths(self):
        inputs=b.STAGED|b.MOUNTED
        raw={k:(f'# {k}'+chr(10)).encode() for mode,k in inputs.values()}
        pins={k:sha256(v).hexdigest() for k,v in raw.items() if k not in b.OUTPUT_PINS}
        with patch.dict(b.OUTPUT_PINS,{k:sha256(raw[k]).hexdigest() for k in b.OUTPUT_PINS}):
            manifest=b.make_manifest(source_commit='1'*40,exact_source_digest_map=pins)
            proof=b.verify_source_set(manifest,source_commit='1'*40,exact_source_digest_map=pins,candidate_images=raw)
            self.assertEqual({x['destination'] for x in proof},set(inputs))
            self.assertEqual({x['destination'] for x in proof if x['before']},set(b.STOCK_PINS))
            self.assertEqual(json.loads(manifest)['marker'],'must-remain-dark')
            with self.assertRaises(ValueError):
                b.verify_source_set(manifest,source_commit='2'*40,exact_source_digest_map=pins,candidate_images=raw)
            for changed in (raw|{'../unexpected.py':b'no'}, raw|{'stock-compiled-guard':b'wrong'}):
                with self.assertRaises(ValueError):
                    b.verify_source_set(manifest,source_commit='1'*40,exact_source_digest_map=pins,candidate_images=changed)
