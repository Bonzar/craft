"""Pin historical packaging without importing a candidate, using real archives."""
from pathlib import Path
import tempfile
import unittest

from rollback_rebuild_base_compile import assemble
import test_rollback_rebuild_base_input as fixtures


class CompileTest(unittest.TestCase):
    def setUp(self):
        source=fixtures.BaseInputTest()
        source.setUp()
        self.case=source

    def build(self, folder, name):
        case=self.case
        return assemble(case.policy,folder/'dist',folder/'schema',folder/name,
                        lambda rev,roots:case.tracked,lambda rev,path:case.blobs[path])

    def test_explicit_tag_without_github_event_and_stable_bytes(self):
        with tempfile.TemporaryDirectory() as lab:
            folder=Path(lab)
            (folder/'dist').mkdir()
            (folder/'dist'/'index.html').write_bytes(b'<main>test</main>')
            (folder/'schema').write_bytes(b'{}\n')
            one=self.build(folder,'one.tgz')
            two=self.build(folder,'two.tgz')
            self.assertEqual(one,two)
            self.assertEqual((folder/'one.tgz').read_bytes(),(folder/'two.tgz').read_bytes())

    def test_rejects_untrusted_dist_link_and_does_not_publish_invalid_archive(self):
        with tempfile.TemporaryDirectory() as lab:
            folder=Path(lab)
            (folder/'dist').mkdir()
            (folder/'schema').write_bytes(b'{}\n')
            (folder/'dist'/'index.html').symlink_to(folder/'schema')
            with self.assertRaises(ValueError):self.build(folder,'linked.tgz')
            self.assertFalse((folder/'linked.tgz').exists())
            (folder/'dist'/'index.html').unlink()
            with self.assertRaises(ValueError):self.build(folder,'missing.tgz')
            self.assertFalse((folder/'missing.tgz').exists())


if __name__=='__main__':
    unittest.main()
