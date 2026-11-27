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

    def test_inspect_reports_exact_fields_and_censors_non_digest_data(self):
        p={"id":"sha256:"+"a"*64,"diff_ids":["sha256:"+"b"*64,"sha256:"+"c"*64]}
        good={"Id":p["id"],"Os":"linux","Architecture":"amd64","RootFS":{"Layers":p["diff_ids"]}}
        a.check_inspect(good,p)
        manifest="sha256:"+"d"*64
        wrong=good|{"Id":manifest,"Descriptor":{"digest":manifest},"Os":"linux",
                    "Architecture":"arm64","RootFS":{"Layers":["https://untrusted/?token=fixture","sha256:"+"e"*64]}}
        with self.assertRaises(ValueError) as failed:
            a.check_inspect(wrong,p)
        diag=str(failed.exception)
        for token in ("Id: expected","Descriptor.digest (diagnostic only)","Architecture",
                      "RootFS.Layers[0]","RootFS.Layers[1]",manifest,p["id"]):
            self.assertIn(token,diag)
        self.assertNotIn("token=fixture",diag)
        self.assertNotIn("https://",diag)

    def test_modern_store_needs_reachable_exact_chain_and_legacy_remains_exact(self):
        config,layers=self.fixture()
        ct="application/vnd.oci.image.config.v1+json"
        lt="application/vnd.oci.image.layer.v1.tar"
        mt="application/vnd.oci.image.manifest.v1+json"
        it="application/vnd.oci.image.index.v1+json"
        def desc(data,mt):
            return {"digest":a.sha(data),"size":len(data),"mediaType":mt}
        ledger={d["digest"]:b for d,b in [(desc(config,ct),config)]+[(desc(x,lt),x) for x in layers]}
        manifest=a.encode({"schemaVersion":2,"mediaType":mt,"config":desc(config,ct),
                           "layers":[desc(x,lt) for x in layers]})
        target=desc(manifest,mt)
        ledger[target["digest"]]=manifest
        index=a.encode({"schemaVersion":2,"mediaType":it,"manifests":[target|{"platform":{
                        "os":"linux","architecture":"amd64"}}]})
        p={"id":a.sha(config),"diff_ids":[a.sha(x) for x in layers]}
        good={"Id":target["digest"],"Descriptor":target,"Os":"linux","Architecture":"amd64",
              "RootFS":{"Layers":p["diff_ids"]}}
        with TemporaryDirectory() as d, patch.dict(a.COMPOSED,config=(a.sha(config),len(config))):
            def save(name, root=index, blobs=ledger):
                path=Path(d)/name
                with tarfile.open(path,"w") as t:
                    def add(name,data):
                        info=tarfile.TarInfo(name); info.size=len(data); t.addfile(info,io.BytesIO(data))
                    if root is not None: add("index.json",root)
                    for sha,b in blobs.items(): add("blobs/sha256/"+sha[7:],b)
                return path
            valid=save("valid")
            self.assertEqual(a.check_inspect(good,p,valid),target["digest"])
            index_id=desc(index,it)
            self.assertEqual(a.check_inspect(good|{"Id":index_id["digest"],"Descriptor":index_id},p,valid),
                             index_id["digest"])
            with self.assertRaisesRegex(ValueError,"saved target binding failed"):
                a.check_inspect(good,p,save("tampered",blobs=ledger|{target["digest"]:manifest+b" "}))
            with self.assertRaisesRegex(ValueError,"saved target binding failed"):
                a.check_inspect(good,p,save("no-index",root=None))
            badplatform=a.encode({"schemaVersion":2,"mediaType":it,"manifests":[target|{
                "platform":{"os":"linux","architecture":"arm64"}}]})
            with self.assertRaisesRegex(ValueError,"saved target binding failed"):
                a.check_inspect(good,p,save("bad-platform",root=badplatform))
            bogus=target|{"digest":"sha256:"+"a"*64}
            with self.assertRaisesRegex(ValueError,"saved target binding failed"):
                a.check_inspect(good|{"Id":bogus["digest"],"Descriptor":bogus},p,valid)
            with self.assertRaisesRegex(ValueError,"Architecture"):
                a.check_inspect(good|{"Architecture":"arm64"},p,valid)
            self.assertEqual(a.check_inspect(good|{"Id":p["id"]},p,save("legacy",root=None)),p["id"])
