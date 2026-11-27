import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from verify_035_oci_root import verify


class RootInputs(unittest.TestCase):
    def test_independent_descriptor_bytes_and_platform(self):
        with tempfile.TemporaryDirectory() as t:
            d=Path(t)
            def blob(data):
                if isinstance(data,dict):
                    data=json.dumps(data,sort_keys=True).encode()
                key=hashlib.sha256(data).hexdigest()
                (d/key).write_bytes(data)
                return {'digest':'sha256:'+key,'size':len(data)}
            platform={'os':'linux','architecture':'amd64'}
            config=blob(platform)
            layers=[blob(b'rootfs fixture'),blob(b'canonical labels fixture')]
            image=blob({'schemaVersion':2,'config':config,'layers':layers})
            index=blob({'schemaVersion':2,'mediaType':'application/vnd.oci.image.index.v1+json',
                        'manifests':[image|{'platform':platform}]})
            pin={'platform':platform,'config':config['digest'],'manifest':image['digest'],
                 'index':index['digest'],'layers':[l['digest'] for l in layers]}
            self.assertEqual(verify(d,pin)['verified_data_only'],True)
            self.assertEqual(verify(d,pin)['build_approved'],False)
            with self.assertRaises(ValueError):
                verify(d,pin|{'platform':{'os':'linux','architecture':'arm64'}})
            target=d/layers[0]['digest'].split(':',1)[1]
            target.write_bytes(b'x'*target.stat().st_size)
            with self.assertRaisesRegex(ValueError,'Unverified OCI raw bytes'):
                verify(d,pin)
