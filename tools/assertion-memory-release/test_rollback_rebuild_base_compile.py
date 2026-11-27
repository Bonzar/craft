"""Pin historical packaging without importing a candidate, using real archives."""
from pathlib import Path
import subprocess
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

    def test_invalid_compile_parameters_fail_before_creating_output(self):
        with tempfile.TemporaryDirectory() as lab:
            root=Path(lab)
            script=Path(__file__).with_name('rollback-rebuild-base-isolated.sh')
            for values in (('no-digest',str(root/'not-source.tar'),self.case.policy.revision),
                           ('registry/test@sha256:'+'a'*64,str(root/'not-source.tar'),'latest'),
                           ('registry/test@sha256:'+'a'*64,str(root/'not-source.tar'),self.case.policy.revision)):
                image,source,revision=values
                run=subprocess.run(['/bin/bash',str(script),image,source,str(root/'out'),revision],
                                   capture_output=True,check=False)
                self.assertEqual(run.returncode,2)
                self.assertFalse((root/'out').exists())

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
