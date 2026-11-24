from dataclasses import asdict,replace
import hashlib
import io
import zipfile
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase,main
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from configured_base import BASE
import configured_provider_checks as b

class BaseProvider(TestCase):
    tag='assertion-memory-v0.1.1'
    revision='a'*40
    run_id='21'
    control_sha='b'*40
    tar_sha='c'*64
    release=b.Original(tag,revision,'22','23','24','d'*64)

    def stage(self,name):
        return b.Control(name,self.run_id,self.control_sha,self.tag,BASE)

    def test_canonical_original_provenance_rejects_alternate_product_or_archive(self):
        graph=b.canonical
        root=graph.ROOT
        product=graph.product_of_tag(self.tag)
        repo={'id':graph.REPO_ID}
        release={'id':22,'tag_name':self.tag,'immutable':True,'draft':False,'published_at':'fixture'}
        data={
          root+'/releases/tags/'+self.tag:{'id':22},
          root+'/releases/22':release,
          root+'/git/ref/tags/'+self.tag:{'ref':'refs/tags/'+self.tag,'object':{'type':'commit','sha':self.revision}},
          root+'/compare/'+self.revision+'...main':{'status':'ahead'},
          root+'/actions/artifacts?per_page=2&name='+product.artifact_prefix+'22-'+self.revision:
            {'total_count':1,'artifacts':[{'name':product.artifact_prefix+'22-'+self.revision,'id':24,'expired':False,
              'size_in_bytes':100,'digest':'sha256:'+'d'*64,'workflow_run':{'id':23,'head_sha':self.revision,
              'repository_id':graph.REPO_ID,'head_repository_id':graph.REPO_ID}}]},
          root+'/actions/runs/23':{'id':23,'workflow_id':25,'path':'.github/workflows/assertion-memory-base.yml',
            'event':'release','run_attempt':1,'status':'completed','conclusion':'success','head_sha':self.revision,
            'head_branch':self.tag,'repository':repo,'head_repository':repo},
          root+'/actions/workflows/assertion-memory-base.yml':{'id':25},
        }
        self.assertEqual(b.original(data.__getitem__,self.stage('test'),asdict(self.release)),self.release)
        for update in ({'tag':'assertion-memory-visual-v0.1.0'}, {'archive_sha':'e'*64}, {'artifact_id':'42'}):
            with self.subTest(update=update),self.assertRaises(ValueError):
                b.original(data.__getitem__,self.stage('test'),asdict(self.release)|update)
        data[root+'/actions/runs/23']['run_attempt']=2
        with self.assertRaises(ValueError):b.original(data.__getitem__,self.stage('test'),asdict(self.release))

    def test_control_cannot_borrow_visual_legacy_or_unprotected_context(self):
        env={'GITHUB_REPOSITORY':b.REPO,'GITHUB_REF':b.MAIN,'GITHUB_REF_PROTECTED':'true',
             'GITHUB_WORKFLOW_REF':BASE.caller,'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_RUN_ATTEMPT':'1',
             'GITHUB_JOB':'prod','PROFILE':'base','GRAPH_TAG':self.tag,'GITHUB_RUN_ID':self.run_id,'GITHUB_SHA':self.control_sha}
        event={'inputs':{'profile':'base','tag':self.tag}}
        self.assertEqual(b.control(BASE,env,'prod',event),self.stage('prod'))
        for update in ({'PROFILE':'visual'},{'GITHUB_WORKFLOW_REF':'legacy'},{'GITHUB_REF_PROTECTED':'false'},
                       {'GITHUB_RUN_ATTEMPT':'2'},{'GITHUB_JOB':'test'}):
            with self.subTest(update=update),self.assertRaises(ValueError):
                b.control(BASE,env|update,'prod',event)

    def test_zip_sealed_before_canonical_unpack(self):
        raw=b'originalZIP'; tar=b'innerTAR'
        rel=replace(self.release,archive_sha=hashlib.sha256(raw).hexdigest())
        with TemporaryDirectory() as d:
            out=Path(d); source=out/'source.zip'; source.write_bytes(raw)
            def unpack(sealed,output,revision):
                self.assertNotEqual(sealed,source)
                source.write_bytes(b'swapped input')
                self.assertEqual(sealed.read_bytes(),raw)
                self.assertEqual(revision,self.revision)
                (output/'release.tar.gz').write_bytes(tar)
                return hashlib.sha256(tar).hexdigest()
            self.assertEqual(b.unpack_verified(source,out,rel,unpack).sha256,hashlib.sha256(tar).hexdigest())
            with self.assertRaisesRegex(ValueError,'hash mismatch'):
                b.unpack_verified(source,out,rel,unpack)

    def test_same_run_verified_receipt_tampering_and_domain_separation(self):
        test=self.stage('test'); prod=self.stage('prod'); rel=self.release
        receiver={'status':'ok','environment':'test','revision':self.revision,'sha256':self.tar_sha}
        content=b.Content(self.tar_sha)
        proof=b.seal_test(test,rel,content,receiver)
        with self.assertRaises(ValueError): b.seal_test(prod,rel,content,receiver)
        with self.assertRaises(ValueError): b.seal_test(test,rel,content,receiver|{'environment':'production'})
        branch=b.canonical.ROOT
        path=branch+'/actions/runs/21'
        repo={'id':b.canonical.REPO_ID}
        blob=io.BytesIO()
        with zipfile.ZipFile(blob,'w') as z: z.writestr(b.TEST_RECEIPT,json.dumps(proof))
        raw=blob.getvalue()
        job={'name':b.TEST_JOB,'run_id':21,'head_sha':self.control_sha,'status':'completed','conclusion':'success',
            'steps':[{'name':name,'status':'completed','conclusion':'success'} for name in b.TEST_REQUIRED_STEPS]}
        receipt={'id':25,'name':'configured-release-test-21-attempt-1','expired':False,'size_in_bytes':len(raw),
                 'digest':'sha256:'+hashlib.sha256(raw).hexdigest(),'workflow_run':{'id':21,'head_sha':self.control_sha}}
        data={
            path:{'id':21,'workflow_id':702,'path':'.github/workflows/configured-release.yml','event':'workflow_dispatch',
                 'head_branch':'main','head_sha':self.control_sha,'run_attempt':1,'status':'in_progress',
                 'repository':repo,'head_repository':repo},
            branch+'/actions/workflows/configured-release.yml':{'id':702},
            path+'/attempts/1/jobs?per_page=100':{'jobs':[job],'total_count':1},
            path+'/artifacts?per_page=100':{'artifacts':[receipt],'total_count':1},
        }
        def attest(url,file=None):
            if file:
                self.assertEqual(url,branch+'/actions/artifacts/25/zip')
                file.write_bytes(raw)
            else: return data[url]
        with TemporaryDirectory() as tmp:
            output=Path(tmp)
            good=b.candidate_promotion(prod,rel,content,json.dumps(proof),attest,output)
            args={'vm_id':'fiction','environment_node_id':'EN_SYNTHETIC','reusable_sha':'e'*40,'nonce':'f'*64}
            aud=b.signed_audience(good,**args)
            self.assertTrue(aud.startswith('urn:assertion-memory:configured:base:promote:v1:'))
            for update in ({'nonce':'e'*64},{'environment_node_id':'EN_ANOTHER'},{'reusable_sha':'f'*40}):
                self.assertNotEqual(aud,b.signed_audience(good,**(args|update)))
            for key,value in (('run_id','88'),('profile','visual'),('environment','test'),('payload_sha256','b'*64)):
                bad=proof|{key:value}
                with self.assertRaises(ValueError):
                    b.candidate_promotion(prod,rel,content,json.dumps(bad),attest,output)
            job['steps'][2]['conclusion']='skipped'
            with self.assertRaisesRegex(ValueError,'TEST activation'):
                b.candidate_promotion(prod,rel,content,json.dumps(proof),attest,output)

    def test_each_selected_stage_requires_its_own_confirmed_approval(self):
        root=b.canonical.ROOT; ctx=self.stage('prod'); name=BASE.prod.environment
        path=f'{root}/environments/{name}'
        settings={'name':name,'id':27,'can_admins_bypass':False,
          'deployment_branch_policy':{'protected_branches':False,'custom_branch_policies':True},
          'protection_rules':[{'type':'required_reviewers','reviewers':[{'type':'User','reviewer':{'id':28}}]}]}
        target={'name':name,'id':27}
        approval={'state':'approved','user':{'type':'User','id':28},'environments':[target]}
        meta={
            path:settings,root+'/branches/main':{'protected':True},
            path+'/deployment-branch-policies?per_page=2':{'total_count':1,'branch_policies':[{'type':'branch','name':'main'}]},
            f'{root}/actions/runs/{ctx.run}/approvals':[approval],
        }
        self.assertTrue(b.verify_approval(meta.__getitem__,ctx,'27','28'))
        target['name']=BASE.test.environment
        with self.assertRaisesRegex(ValueError,'stage approval'):
            b.verify_approval(meta.__getitem__,ctx,'27','28')
        settings['can_admins_bypass']=True
        with self.assertRaisesRegex(ValueError,'protected release environment'):
            b.verify_approval(meta.__getitem__,ctx,'27','28')

    def test_visual_receipt_uses_same_verifier_with_only_layout_diff(self):
        target=b.Target('configured-release-visual-test','test','restricted-test-deploy','deploy-visual')
        profile=replace(BASE,name='visual',original_workflow='assertion-memory-visual.yml',test=target,
                        receipt=b.ReceiptLayout('archive_sha256','release'))
        tag='assertion-memory-visual-v0.1.0'
        visual=replace(self.release,tag=tag)
        ctx=b.Control('test',self.run_id,self.control_sha,tag,profile)
        content=b.Content(self.tar_sha,'f'*64)
        receipt={'environment':'test','revision':self.revision,'archive_sha256':self.tar_sha,'release':'f'*64}
        common=b.seal_test(ctx,visual,content,receipt)
        self.assertEqual(common['profile'],'visual')
        self.assertEqual(common['manifest_sha256'],'f'*64)
        with self.assertRaises(ValueError):b.seal_test(ctx,visual,b.Content(self.tar_sha),receipt)

if __name__=='__main__':main()
