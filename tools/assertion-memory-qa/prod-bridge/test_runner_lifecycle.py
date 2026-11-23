"""Exercise the exact pinned mode with no Environments API access."""
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import hashlib
import zipfile


lc=SourceFileLoader('base_release_lifecycle',str(Path(__file__).resolve().parent.parent/'lifecycle.py')).load_module()


class PinnedLifecycle(unittest.TestCase):
    def test_tests_exact_bytes_through_dispatch_with_no_environments_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            tmp=Path(d)
            sha='a'*40
            revision='b'*40
            build_path=tmp/'build.zip'
            archive=b'fictional-release'
            inner=hashlib.sha256(archive).hexdigest()
            name=f'assertion-memory-qa-{revision}.tar.gz'
            with zipfile.ZipFile(build_path,'w') as z:
                z.writestr(name,archive)
                z.writestr(name+'.sha256',f'{inner}  {name}\n')
            event_file=tmp/'event.json'
            event=dict(action='production',release_id='5',test_run_id='6')
            event_file.write_text(json.dumps({'inputs':event}))
            env=dict(
                GITHUB_REPOSITORY=lc.ci.REPO,GITHUB_RUN_ATTEMPT='1',GITHUB_EVENT_NAME='workflow_dispatch',
                GITHUB_REF='refs/heads/main',GITHUB_WORKFLOW_REF=lc.WORKFLOW_REF,GITHUB_SHA=sha,
                GITHUB_REF_PROTECTED='true',GITHUB_JOB='deploy-production',GITHUB_RUN_ID='7',
                GITHUB_ACTOR_ID='8',GITHUB_EVENT_PATH=str(event_file),PRODUCTION_READY='true',
                AUDITED_REUSABLE_SHA='c'*40,RELEASE_ID='5',TEST_RUN_ID='6',
                RELEASE_DOWNLOAD_DIR=str(tmp/'release'),GITHUB_OUTPUT=str(tmp/'out'))
            calls=[]
            def api(route,dest=None):
                calls.append(route)
                if '/environments/' in route:
                    raise AssertionError('Runner must not require Environments read capability')
                if route.endswith('/actions/runs/7'):
                    return {'id':7,'actor':{'id':8},'triggering_actor':{'id':8},'event':'workflow_dispatch',
                            'run_attempt':1,'head_branch':'main','head_sha':sha,'path':'.github/workflows/'+lc.WORKFLOW,
                            'repository':{'full_name':lc.ci.REPO},'head_repository':{'full_name':lc.ci.REPO}}
                self.assertEqual(route,lc.ROOT+'/actions/artifacts/2/zip')
                dest.write_bytes(build_path.read_bytes())

            def receipt(get, run, control, identity, path):
                self.assertEqual((run,control,identity['sha256']),('6',sha,inner))
                (path/'test-receipt-id').write_text('3\n')
                return {**identity,'test_run_id':run}

            with patch.object(lc.ci,'api',api), patch.object(lc,'identify',return_value=('tag',revision)), \
                 patch.object(lc,'build_identity',return_value={'artifact_id':'2','build_run_id':'4','build_attempt':'1',
                   'zip_sha256':hashlib.sha256(build_path.read_bytes()).hexdigest()}), \
                 patch.object(lc,'read_test_receipt',side_effect=receipt), \
                 patch.dict(lc.os.environ,env,clear=True),patch.object(sys,'argv',['lifecycle.py','production-pinned-proof']):
                lc.main()
                for key,value in (('GITHUB_ACTOR_ID','9'),('GITHUB_REF_PROTECTED','false'),
                                  ('GITHUB_RUN_ATTEMPT','2'),('PRODUCTION_READY','false')):
                    with patch.dict(lc.os.environ,{key:value}),self.assertRaises(ValueError):
                        lc.main()

            intent=json.loads((tmp/'release'/'prod-intent.json').read_text())
            self.assertEqual(intent,dict(run_id='7',test_run_id='6',test_receipt_id='3',release_id='5',
                                         build_run_id='4',artifact_id='2',revision=revision,sha256=inner))
            self.assertFalse(any('/environments/' in call for call in calls))
