"""Standard Visual Release replay, entirely offline and without VM access."""
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from zipfile import ZipFile

import configured_provider_checks as configured
import github_graph_preview as resolver
import github_graph_preview_test as github_fixtures
from configured_visual_profile import PROFILE, content, unpack_verified
from visual_policy import archive_spec, validate
import release_core


def fixture_zip(revision):
    assets = {
        'index.html': b'<html>fictional older release</html>',
        'assets/index-a.css': b'body{}',
        'assets/index-b.js': b'void 0;',
        'assets/graph.worker-c.js': b'void 0;',
    }
    manifest=(json.dumps({n:sha256(b).hexdigest() for n,b in assets.items()},
                         sort_keys=True,separators=(',',':'))+'\n').encode()
    payload=BytesIO()
    with tarfile.open(fileobj=payload,mode='w',format=tarfile.USTAR_FORMAT) as archive:
        for name,raw in sorted({**assets,'manifest.json':manifest}.items()):
            entry=tarfile.TarInfo(name)
            entry.size=len(raw)
            archive.addfile(entry,BytesIO(raw))
    package=payload.getvalue()
    receipt=dict(source_commit=revision,archive_sha256=sha256(package).hexdigest(),
                 archive_bytes=len(package),release_digest=sha256(manifest).hexdigest())
    output=BytesIO()
    with ZipFile(output,'w') as ar:
        ar.writestr('receipt.json',json.dumps(receipt))
        ar.writestr('visual.tar',package)
    return output.getvalue(),package,receipt


class ConfiguredVisualReplay(TestCase):
    def setUp(self):
        fixture=github_fixtures.Graph()
        self.tag,self.run,self.item,self.published,self.data,_=fixture.fixture(visual=True)
        self.archive,self.payload,self.receipt=fixture_zip(fixture.sha)
        self.item.update(digest='sha256:'+sha256(self.archive).hexdigest(),size_in_bytes=len(self.archive))
        self.calls=[]
        self.env={'GITHUB_REPOSITORY':resolver.REPO,'GITHUB_REF':resolver.MAIN,'GITHUB_REF_PROTECTED':'true',
                  'GITHUB_WORKFLOW_REF':PROFILE.separate_caller,'GITHUB_RUN_ATTEMPT':'1',
                  'GITHUB_JOB':'build','GITHUB_RUN_ID':'999','GITHUB_SHA':'b'*40,
                  'GITHUB_EVENT_NAME':'workflow_dispatch'}
        self.event={'inputs':{'tag':self.tag}}
        self.artifact_index='/actions/artifacts'+resolver.archive_filter(resolver.product_of_tag(self.tag),
                                                                        str(self.published['id']),fixture.sha)

    def get(self,route):
        self.calls.append(route)
        return self.data[route.removeprefix(resolver.ROOT)]

    def selection(self):
        profile,identity=configured.build_selection(self.get,(PROFILE,),self.env,self.event)
        self.assertIs(profile,PROFILE)
        return identity

    def unpack(self,zip_file,out,identity):
        return configured.unpack_verified(zip_file,out,identity,
                                           lambda z,d,r:content(unpack_verified(z,d,r)),
                                           payload_name='visual.tar')

    def test_fresh_runner_download_binds_original_zip_content_and_manifest(self):
        chosen=self.selection()
        self.assertEqual(chosen.tag,self.tag)
        self.assertEqual(chosen.archive_sha,sha256(self.archive).hexdigest())
        for _ in range(2):
            # A fresh artifact-only runner: no previous releases or application
            # state in either temporary directory.
            with TemporaryDirectory() as d:
                root=Path(d); archive=root/'fresh.zip'; archive.write_bytes(self.archive)
                out=root/'output'; out.mkdir()
                result=self.unpack(archive,out,chosen)
                self.assertEqual(result.sha256,self.receipt['archive_sha256'])
                self.assertEqual(result.manifest_sha256,self.receipt['release_digest'])
                self.assertEqual((out/'visual.tar').read_bytes(),self.payload)
                staged=root/'staged'; staged.mkdir()
                with (out/'visual.tar').open('rb') as source:
                    extracted=release_core.unpack(source,staged,result.sha256,archive_spec())
                self.assertEqual(validate(extracted),result.manifest_sha256)

    def test_expired_or_absent_artifact_refuses_before_artifact_download_or_activation(self):
        available=self.data[self.artifact_index]
        for state in ('expired','absent'):
            with self.subTest(state=state):
                self.calls.clear()
                self.item['expired']=state=='expired'
                self.data[self.artifact_index]=available if state=='expired' else {'total_count':0,'artifacts':[]}
                with self.assertRaisesRegex(ValueError,'Artifact origin|exactly one'):
                    self.selection()
                self.assertEqual(self.calls[-1],resolver.ROOT+self.artifact_index)
                self.assertFalse(any(route.endswith('/zip') for route in self.calls))

    def test_changed_zip_does_not_stage_or_fall_back_to_cached_vm_build(self):
        chosen=self.selection()
        with TemporaryDirectory() as d:
            root=Path(d); archive=root/'changed.zip'; archive.write_bytes(self.archive+b'changed')
            out=root/'output'; out.mkdir()
            with self.assertRaisesRegex(ValueError,'original ZIP hash mismatch'):
                self.unpack(archive,out,chosen)
            self.assertEqual(list(out.iterdir()),[])


if __name__=='__main__':
    main()
