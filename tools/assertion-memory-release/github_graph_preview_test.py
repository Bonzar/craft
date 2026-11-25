"""Offline tests; no external endpoints, GitHub secrets, or mutable operations."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request

import github_graph_preview as g


class Graph(unittest.TestCase):
    sha='a'*40
    rid='55'
    run_id='66'
    artifact_id='77'

    def fixture(self,visual=False):
        p=g.PRODUCTS[0] if visual else g.PRODUCTS[1]
        tag=p.tag_prefix+'v0.1.0'
        run=dict(id=int(self.run_id),event='release',status='completed',conclusion='success',workflow_id=8,
                 path='.github/workflows/'+p.workflow,run_attempt=1,head_sha=self.sha,head_branch=tag,
                 repository={'id':g.REPO_ID},head_repository={'id':g.REPO_ID})
        name=p.artifact_prefix+self.rid+'-'+self.sha
        item=dict(id=int(self.artifact_id),name=name,expired=False,size_in_bytes=100,digest='sha256:'+'b'*64,
           workflow_run=dict(id=int(self.run_id),head_sha=self.sha,repository_id=g.REPO_ID,head_repository_id=g.REPO_ID))
        categorized={'total_count':1,'artifacts':[item]}
        published=dict(id=int(self.rid),draft=False,immutable=True,published_at='now',tag_name=tag)
        data={
            f'/actions/runs/{self.run_id}':run,
            f'/actions/workflows/{p.workflow}':{'id':8},
            f'/actions/runs/{self.run_id}/artifacts?per_page=2&name='+name:categorized,
            '/actions/artifacts?per_page=2&name='+name:categorized,
            '/releases/tags/'+tag:published,
            '/releases/'+self.rid:published,
            '/git/ref/tags/'+tag:{'ref':'refs/tags/'+tag,'object':{'type':'commit','sha':self.sha}},
            '/compare/'+self.sha+'...main':{'status':'ahead'},
        }
        return tag,run,item,published,data,lambda route:data[route.removeprefix(g.ROOT)]

    def test_existing_tag_and_automatic_build_event_select_same_bytes_for_both(self):
        for visual in (False,True):
            tag,run,item,published,data,get=self.fixture(visual)
            manual=g.from_tag(get,tag)
            self.assertEqual(g.from_completed_build(get,run),manual)
            self.assertEqual(manual,dict(tag=tag,revision=self.sha,release_id=self.rid,build_run_id=self.run_id,
                                        artifact_id=self.artifact_id,archive_sha='b'*64))

    def test_automatic_base_ignores_an_unrelated_sites_archive_in_the_same_run(self):
        tag,run,item,_,data,_=self.fixture()
        route=f'/actions/runs/{self.run_id}/artifacts'
        data[route+'?per_page=2']={'total_count':2,'artifacts':[
            dict(item,id=99,name='assertion-memory-native-sites-release'),item,
        ]}
        get=lambda path:data[path.removeprefix(g.ROOT)]
        self.assertEqual(g.from_completed_build(get,run),g.from_tag(get,tag))

    def test_other_archive_cannot_be_used_when_expected_name_is_absent_or_wrong(self):
        tag,run,item,_,data,get=self.fixture()
        route=f'/actions/runs/{self.run_id}/artifacts?per_page=2&name='+item['name']
        data[route]={'total_count':0,'artifacts':[]}
        with self.assertRaisesRegex(ValueError,'exactly one'):
            g.from_completed_build(get,run)
        data[route]={'total_count':1,'artifacts':[dict(item,name='assertion-memory-native-sites-release')]}
        with self.assertRaisesRegex(ValueError,'origin'):
            g.from_completed_build(get,run)

    def test_rejects_untrusted_source_and_version_drift(self):
        tag,run,item,published,data,get=self.fixture()
        variants=[(run,'event','pull_request'),(run,'path','.github/workflows/unrelated.yml'),
            (run,'status','in_progress'),(run,'run_attempt',2),(run,'head_repository',{'id':1}),
            (published,'immutable',False),(published,'draft',True),(published,'published_at',''),
            (item,'expired',True),(item,'digest','sha256:unknown'),
            (item['workflow_run'],'id',99),(item['workflow_run'],'head_repository_id',1)]
        for obj,key,changed in variants:
            old=obj[key]; obj[key]=changed
            with self.subTest(key=key),self.assertRaises((ValueError,KeyError)):
                g.from_completed_build(get,run)
            obj[key]=old

    def test_rejects_event_and_canonical_tag_mismatch(self):
        tag,run,item,published,data,get=self.fixture()
        wrong=dict(run,head_branch=tag+'+unexpected')
        with self.assertRaisesRegex(ValueError,'release version tag'):
            g.from_completed_build(get,wrong)
        data[f'/actions/runs/{self.run_id}']=wrong
        with self.assertRaisesRegex(ValueError,'original release build'):
            g.from_completed_build(get,run)
        with self.assertRaisesRegex(ValueError,'original release build'):
            g.from_tag(get,tag)

    def test_rejects_ambiguous_artifact_or_non_main_revision(self):
        tag,run,item,published,data,get=self.fixture()
        artifacts=data[f'/actions/runs/{self.run_id}/artifacts?per_page=2&name='+item['name']]
        artifacts['total_count']=2
        with self.assertRaisesRegex(ValueError,'exactly one'):
            g.from_completed_build(get,run)
        artifacts['total_count']=1
        data['/compare/'+self.sha+'...main']['status']='diverged'
        with self.assertRaisesRegex(ValueError,'main history'):
            g.from_tag(get,tag)

    def test_native_main_entrypoint_has_no_numeric_inputs(self):
        tag,run,item,published,data,get=self.fixture()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            event=root/'event'
            event.write_text(json.dumps({'workflow_run':run}))
            env=dict(GITHUB_REPOSITORY=g.REPO,GITHUB_REF=g.MAIN,GITHUB_WORKFLOW_REF=g.CONTROL,
                     GITHUB_RUN_ATTEMPT='1',GITHUB_EVENT_NAME='workflow_run',GITHUB_EVENT_PATH=str(event),
                     GITHUB_OUTPUT=str(root/'outputs'),GITHUB_STEP_SUMMARY=str(root/'summary'),GH_TOKEN='fictional')
            with patch.object(g,'api',side_effect=lambda p, _:get(p)),patch.object(g.sys,'argv',['preview','resolve']):
                g.main(env)
                self.assertIn('artifact_id=77',(root/'outputs').read_text())
                self.assertIn('No deployment',(root/'summary').read_text())
                for name,bad in (('GITHUB_REF','refs/heads/other'),('GITHUB_RUN_ATTEMPT','2'),('GITHUB_WORKFLOW_REF','fake')):
                    with self.subTest(name=name),self.assertRaisesRegex(ValueError,'trusted main'):
                        g.main({**env,name:bad})

    def test_preview_hash_must_be_same_for_promotion(self):
        env=dict(GITHUB_REPOSITORY=g.REPO,GITHUB_REF=g.MAIN,GITHUB_WORKFLOW_REF=g.CONTROL,
                 GITHUB_RUN_ATTEMPT='1',GITHUB_EVENT_NAME='workflow_dispatch',GH_TOKEN='fictional',
                 ARCHIVE_SHA='b'*64,VERIFIED_SHA='b'*64)
        with patch.object(g.sys,'argv',['preview','compare']):
            g.main(env)
            with self.assertRaisesRegex(ValueError,'changed immutable bytes'):
                g.main({**env,'VERIFIED_SHA':'c'*64})

    def test_signed_archive_redirect_never_carries_authorization_and_cannot_chain(self):
        handler=g.ArtifactRedirect()
        req=Request('https://api.github.com/repos/Bonzar/craft/actions/artifacts/77/zip',
                    headers={'Authorization':'Bearer fixture'})
        dst=handler.redirect_request(req,None,302,'Found',{},'https://fixture.blob.core.windows.net/one?signature=fixture')
        self.assertIsNone(dst.get_header('Authorization'))
        with self.assertRaisesRegex(ValueError,'Unexpected artifact storage'):
            handler.redirect_request(dst,None,302,'Found',{},'https://elsewhere.blob.core.windows.net/two')
        for url in ('http://fixture.blob.core.windows.net/one','https://other.invalid/one',
                    'https://user@fixture.blob.core.windows.net/one'):
            with self.subTest(url=url),self.assertRaisesRegex(ValueError,'Unexpected artifact storage'):
                handler.redirect_request(req,None,302,'Found',{},url)

    def test_unapproved_tags_never_resolve(self):
        for value in ('main','refs/tags/assertion-memory-v0.1.0','assertion-memory-v0.1.0\nINJECT=value','v1.2.3'):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'version tag'):
                g.from_tag(lambda _: self.fail('Unapproved tag caused network read'),value)


if __name__=='__main__':
    unittest.main()
