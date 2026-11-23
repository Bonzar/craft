import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from lifecycle import identify, locate, promote, unpack, ROOT, WORKFLOW
from package import build


class VisualReleaseTest(unittest.TestCase):
    def test_immutable_visual_release_and_origin_required(self):
        rev='a'*40; tag='assertion-memory-visual-v1.0.0'; rid='7'
        rel={'id':7,'draft':False,'immutable':True,'published_at':'now','tag_name':tag}
        artifact_name=f'assertion-memory-visual-{rid}-{rev}'
        artifact={'id':9,'expired':False,'size_in_bytes':1,'name':artifact_name,
                  'digest':'sha256:'+'0'*64,'workflow_run':{'id':5,'head_sha':rev}}
        run={'id':5,'workflow_id':8,'event':'release','conclusion':'success','run_attempt':1,
             'head_sha':rev,'head_repository':{'full_name':'Bonzar/craft'}}
        source={f'{ROOT}/releases/{rid}':rel,
                f'{ROOT}/git/ref/tags/{tag}':{'ref':'refs/tags/'+tag,'object':{'type':'commit','sha':rev}},
                f'{ROOT}/compare/{rev}...main':{'status':'ahead'},
                f'{ROOT}/actions/artifacts?name={artifact_name}&per_page=2':{'total_count':1,'artifacts':[artifact]},
                f'{ROOT}/actions/runs/5':run,f'{ROOT}/actions/workflows/{WORKFLOW}':{'id':8}}
        self.assertEqual(identify(source.__getitem__,rid),(tag,rev))
        self.assertEqual(locate(source.__getitem__,rid,rev),('9','0'*64))
        run['event']='pull_request'
        with self.assertRaisesRegex(ValueError,'trusted original'):
            locate(source.__getitem__,rid,rev)
        rel['immutable']=False
        with self.assertRaisesRegex(ValueError,'immutable'):
            identify(source.__getitem__,rid)

    def test_deterministic_and_pinned_artifact_then_zip_and_promotion_are_exact(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); src=root/'src'; src.mkdir(); (src/'assets').mkdir()
            html=src/'index.html'
            pinned='<script src="./assets/index-a.js"></script><link href="./assets/index-a.css">'
            html.write_text(pinned)
            (src/'assets/index-a.js').write_text('new Worker(new URL("graph.worker-a.js",import.meta.url))')
            (src/'assets/graph.worker-a.js').write_text('postMessage(1)')
            (src/'assets/index-a.css').write_text('.root {}')
            first,second=root/'a.tar',root/'b.tar'
            receipt=build(src,first);self.assertEqual(build(src,second),receipt)
            self.assertEqual(first.read_bytes(),second.read_bytes())
            rev, archive='a'*40,root/'release.zip'
            with zipfile.ZipFile(archive,'w') as zip:
                zip.write(first,'visual.tar')
                zip.writestr('receipt.json',json.dumps({**receipt,'source_commit':rev}))
            decoded=unpack(archive,root,rev)
            self.assertEqual(decoded,(receipt['archive_sha256'],receipt['release_digest']))
            self.assertEqual((root/'visual.tar').read_bytes(),first.read_bytes())
            with self.assertRaisesRegex(ValueError,'TEST'):
                promote({'sha256':decoded[0],'revision':rev},{'sha256':'1'*64,'revision':rev})
            with self.assertRaisesRegex(ValueError,'receipt'):
                unpack(archive,root,'b'*40)
            html.write_text(pinned.replace('./assets','/assets',1))
            with self.assertRaisesRegex(ValueError,'within'):
                build(src,root/'unversioned.tar')


if __name__=='__main__': unittest.main()
