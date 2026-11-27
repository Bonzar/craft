from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import materialize_035_inputs as m


class Inputs(unittest.TestCase):
    def test_real_pin_counts_and_exact_lock(self):
        wheels,npm=m.exact_hashes()
        self.assertEqual((len(wheels), len(npm)), (30,31))
        observed=set()
        for line in Path(__file__).with_name('base-035-python-full.lock').read_text().splitlines():
            requirement,digest=line.split(' --hash=sha256:')
            pkg,ver=requirement.split('==')
            picked=[(n,p) for n,p in wheels.items() if n.startswith(pkg.replace('-','_')+'-'+ver+'-')]
            self.assertEqual(len(picked),1)
            self.assertEqual(picked[0][1],digest)
            observed.add(picked[0][0])
        self.assertEqual(set(wheels),observed)

    def test_failed_stream_never_leaves_usable_input(self):
        with TemporaryDirectory() as d:
            p=Path(d)/'file.whl'
            with patch.object(m,'read_url',return_value=BytesIO(b'wrong')):
                with self.assertRaisesRegex(ValueError,'hash mismatch'):
                    m.stream_verify('https://example.invalid/file.whl','example.invalid',p,'sha256',
                                    sha256(b'good').digest(),filename='file.whl')
            self.assertEqual(list(Path(d).iterdir()),[])
            with patch.object(m,'read_url',return_value=BytesIO(b'good')):
                m.stream_verify('https://example.invalid/file.whl','example.invalid',p,'sha256',
                                    sha256(b'good').digest(),filename='file.whl')
            self.assertEqual(p.read_bytes(),b'good')
            with self.assertRaises(ValueError):
                m.read_url('https://example.invalid/file.whl','pypi.org')
