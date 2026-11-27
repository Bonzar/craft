from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import package_035_rebuild as p


class OutputProof(unittest.TestCase):
    def run_pair(self, same=True):
        with TemporaryDirectory() as t:
            r=Path(t)
            for side in ("a","b"):
                src=r/side
                d=src/"web"
                d.mkdir(parents=True)
                (d/"index.html").write_text("fixture"+(side if not same else ""))
                (d/"app.js").write_text("console.log('fixture');")
                catalog=src/"catalog.json"
                catalog.write_text('{"fixture":true}')
                p.shell_pack(d,catalog,src/"proof")
            try:
                return p.compare(r/"a/proof",r/"b/proof")
            except ValueError as e:
                return str(e)

    def test_two_fresh_identical_trees_match_at_manifests_and_tar_bytes(self):
        summary=self.run_pair()
        self.assertEqual(summary["revision"],p.REVISION)
        self.assertEqual(list(summary["files"]),["dist/app.js","dist/index.html",p.CATALOG])

    def test_independently_changed_payload_rejected(self):
        self.assertEqual(self.run_pair(False),"Independent build outputs diverged")

    def test_symlink_output_fails_closed(self):
        with TemporaryDirectory() as t:
            r=Path(t)
            d=r/"web";d.mkdir()
            file=r/"outside";file.write_text("fixture")
            (d/"index.html").symlink_to(file)
            with self.assertRaisesRegex(ValueError,"Unexpected historical build output"):
                p.shell_pack(d,file,r/"proof")
