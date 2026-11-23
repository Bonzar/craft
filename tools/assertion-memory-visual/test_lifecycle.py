import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from lifecycle import identify, locate, promote, unpack, test_receipt, ROOT, WORKFLOW
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


    def test_prod_requires_same_private_run_and_authentic_test_receipt(self):
        rev='a'*40
        expected={'revision':rev,'artifact_id':'10','sha256':'d'*64,'release':'e'*64}
        receipt={**expected,'test_run_id':'30','release_id':'7'}
        run={'id':30,'workflow_id':8,'event':'workflow_dispatch','conclusion':'success','run_attempt':1,
             'head_branch':'main','head_repository':{'full_name':'Bonzar/craft'},
             'path':'.github/workflows/'+WORKFLOW}
        job={'name':'Deploy visual TEST','conclusion':'success'}
        with tempfile.TemporaryDirectory() as folder:
            dir=Path(folder)
            zipped=dir/'sample.zip'
            with zipfile.ZipFile(zipped,'w') as z:
                z.writestr('test-proof.json',json.dumps(receipt))
            wire=zipped.read_bytes()
            artifact={'id':31,'name':'visual-test-proof-30','expired':False,'size_in_bytes':len(wire),
                      'digest':'sha256:'+hashlib.sha256(wire).hexdigest()}
            data={f'{ROOT}/actions/workflows/{WORKFLOW}':{'id':8},f'{ROOT}/actions/runs/30':run,
                  f'{ROOT}/actions/runs/30/attempts/1/jobs?per_page=100':{'jobs':[job]},
                  f'{ROOT}/actions/runs/30/artifacts?name=visual-test-proof-30&per_page=2':
                      {'total_count':1,'artifacts':[artifact]}}
            def get(url, output=None):
                if output is None: return data[url]
                self.assertEqual(url, f'{ROOT}/actions/artifacts/31/zip')
                output.write_bytes(wire)
            test_receipt(get,'30','7',expected,dir,unpack_receipt=json.loads)
            with self.assertRaisesRegex(ValueError,'differs'):
                test_receipt(get,'30','7',{**expected,'sha256':'a'*64},dir,unpack_receipt=json.loads)
            job['name']='Deploy visual PROD'
            with self.assertRaisesRegex(ValueError,'TEST job'):
                test_receipt(get,'30','7',expected,dir,unpack_receipt=json.loads)
            job['name']='Deploy visual TEST'
            run['head_branch']='other'
            with self.assertRaisesRegex(ValueError,'on main'):
                test_receipt(get,'30','7',expected,dir,unpack_receipt=json.loads)
            run['head_branch']='main'
            artifact['digest']='sha256:'+'1'*64
            with self.assertRaisesRegex(ValueError,'ZIP digest'):
                test_receipt(get,'30','7',expected,dir,unpack_receipt=json.loads)


if __name__=='__main__': unittest.main()
