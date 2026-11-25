from io import BytesIO
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import TestCase, main
from zipfile import ZipFile
from types import SimpleNamespace
from hashlib import sha256
import packed_release as p


class PackedRelease(TestCase):
    def test_no_prod_fallback_from_different_test_route(self):
        self.assertFalse(p.mode('test',None))
        self.assertFalse(p.mode('prod','false','false'))
        self.assertTrue(p.mode('prod','true','true'))
        for triple in [('prod','false','true'),('prod','true','false'),('prod','true',None),('test','invalid',None)]:
            with self.assertRaises(ValueError):
                p.mode(*triple)

    def test_requires_exact_same_run_packed_proof_upload(self):
        ctrl=SimpleNamespace(run='123',control_sha='a'*40)
        ok={'name':'Preserve packed Visual TEST evidence','status':'completed','conclusion':'success'}
        job={'name':'configured / TEST','run_id':123,'head_sha':'a'*40,'conclusion':'success','steps':[ok]}
        cases=[{**job,'steps':[]},{**job,'steps':[ok,ok]},{**job,'run_id':124},
               {**job,'steps':[{**ok,'conclusion':'skipped'}]}]
        p.require_packed_test_step(lambda _: {'total_count':1,'jobs':[job]},'/repos/Bonzar/craft',ctrl)
        for change in cases:
            with self.assertRaises(ValueError):
                p.require_packed_test_step(lambda _: {'total_count':1,'jobs':[change]},'/repos/Bonzar/craft',ctrl)

    def test_download_picks_named_exact_artifact_once_and_enforces_provider_run(self):
        inner=b'synthetic packed envelope'
        fixture=BytesIO()
        with ZipFile(fixture,'w') as z:
            z.writestr(p.PACK,inner)
        blob=fixture.getvalue()
        name='assertion-memory-visual-packed-112-'+'a'*40
        root='/repos/Bonzar/craft'
        item={'id':11,'name':name,'expired':False,'digest':'sha256:'+sha256(blob).hexdigest(),
              'workflow_run':{'head_sha':'a'*40,'id':123,'repository_id':1260097465,
                              'head_repository_id':1260097465},'size_in_bytes':len(blob)}
        hits=[]
        def get(url,target=None):
            hits.append(url)
            if target is not None:
                target.write_bytes(blob)
                return None
            return item if '/artifacts/11' in url else {'total_count':1,'artifacts':[item]}
        with TemporaryDirectory() as tmp:
            aid=p.named(get,root+'/actions/runs/123/artifacts',name)
            self.assertEqual(aid,'11')
            self.assertEqual(p.download(get,root,Path(tmp),aid,name,'a'*40,'123',p.PACK,4096)[0],inner)
            self.assertEqual(len(hits),3)
            with self.assertRaises(ValueError):
                p.download(get,root,Path(tmp),aid,name,'a'*40,'456',p.PACK,4096)
            self.assertEqual(len(hits),4)


if __name__=='__main__':
    main()
