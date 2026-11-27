"""Fixture archives only; no container engine."""
import gzip
import io
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import docker_035_verified_archive as a


class Archive(unittest.TestCase):
    def fixture(self):
        layers=[b"fixture-base-one",b"fixture-base-two",b"fixture-overlay"]
        config=a.encode({"architecture":"amd64","os":"linux",
                         "rootfs":{"type":"layers","diff_ids":[a.sha(x) for x in layers]}})
        return config,layers

    def test_deterministic_archive_and_qualified_repack(self):
        config,layers=self.fixture()
        with TemporaryDirectory() as d, patch.dict(a.COMPOSED,config=(a.sha(config),len(config))), \
             patch.object(a,"clayout",return_value=(config,[gzip.compress(x,mtime=0) for x in layers])):
            root=Path(d)
            first,second=root/"first.tar",root/"second.tar"
            a.write_archive(root,first)
            a.write_archive(root,second)
            self.assertEqual(first.read_bytes(),second.read_bytes())
            self.assertEqual(a.verify_archive(first)["diff_ids"],[a.sha(x) for x in layers])
            with tarfile.open(first) as t:
                self.assertEqual(t.getmembers()[0].name,"manifest.json")

    def test_pinned_config_and_ordered_diffid_fail_independently(self):
        config,layers=self.fixture()
        with patch.dict(a.COMPOSED,config=(a.sha(config),len(config))):
            self.assertEqual(a.verify_payload(config,layers),[a.sha(l) for l in layers])
            for c,ls in ((config+b" ",layers),(config,layers[::-1]),(config,layers[:2]+[b"changed"])):
                with self.assertRaises(ValueError):
                    a.verify_payload(c,ls)
