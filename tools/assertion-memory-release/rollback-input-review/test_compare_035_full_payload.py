import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import compare_035_full_payload as c
import package_035_rebuild as q
import test_rollback_rebuild_base_input as fixtures
from rollback_rebuild_origin import Policy


class FullPayload(unittest.TestCase):
    def make(self, base):
        for side in ("a","b"):
            root=base/side
            dist=root/"dist"
            dist.mkdir(parents=True)
            (dist/"index.html").write_bytes(b"<main>test</main>")
            catalog=root/"catalog"
            catalog.write_text(json.dumps({"version":2,"revision":q.REVISION,"tools":{"memory_fixture":{}}}))
            q.shell_pack(dist,catalog,root/"proof")
        return base/"a/proof",base/"b/proof"

    def test_exact_existing_assembler_two_full_archives_equal(self):
        f=fixtures.BaseInputTest()
        f.setUp()
        from rollback_rebuild_base_input import MARKER_SOURCE
        f.blobs[MARKER_SOURCE]=b"assertion-memory-base-browser-listener-v1\n"
        policy=Policy(21,"a"*40,c.TAG,q.REVISION,c.RELEASE_ID,"base","base-v1")
        with TemporaryDirectory() as t:
            a,b=self.make(Path(t))
            result=c.compare_full(a,b,policy,lambda rev,roots:f.tracked,
                                  lambda rev,path:f.blobs[path])
            self.assertEqual(result["schema"],c.TYPE)
            self.assertGreater(result["full_member_count"],len(q.compare(a,b)["files"]))
            self.assertFalse(result["archive_published"])
            self.assertNotIn("full_file_manifest",result)
            # Even identically compiled Graph+catalog is insufficient if the
            # trusted full runtime source selection changes between readers.
            calls=0
            def source(rev,path):
                nonlocal calls
                if path==f.app:
                    calls+=1
                    return b"first controlled fixture" if calls<=2 else b"different controlled fixture"
                return f.blobs[path]
            with self.assertRaisesRegex(ValueError,"Full Base manifest or archive digest diverged"):
                c.compare_full(a,b,policy,lambda rev,roots:f.tracked,source)
            f.blobs[MARKER_SOURCE]=b"changed"
            with self.assertRaisesRegex(ValueError,"listener contract marker missing"):
                c.compare_full(a,b,policy,lambda rev,roots:f.tracked,lambda rev,path:f.blobs[path])
