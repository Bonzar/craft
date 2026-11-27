import json
from pathlib import Path
import tempfile
import unittest

from lock_graph_npm import BLOB, plan, supported, validate_downloads


class TestNpmMaterial(unittest.TestCase):
    def test_exact_upstream_blob_and_platform(self):
        blob=Path(__file__).with_name('graph-package-lock.json').read_bytes()
        p=plan(blob)
        self.assertEqual(p['git_blob'],BLOB)
        self.assertEqual(len(p['packages']),31)
        self.assertEqual(p,json.loads(Path(__file__).with_name('npm-linux-x64-glibc.json').read_bytes()))
        self.assertFalse(supported(['!linux'],'linux'))
        self.assertTrue(supported(['!arm'],'x64'))
        with self.assertRaises(ValueError):
            plan(blob+b' ')
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                validate_downloads(p,Path(d))
