"""Offline release transport checks; no external HTTPS or real SSH."""
import hashlib
import json
from types import SimpleNamespace
import send_test_release
import importlib.util
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

TOOLS=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('release_ci', TOOLS/'release-ci.py')
release_ci=importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_ci)
REVISION='a'*40
INNER=b'fictional bytes only'
DIGEST=hashlib.sha256(INNER).hexdigest()

class ReleaseTest(unittest.TestCase):
    def test_accepts_punctuated_opaque_bearer_without_putting_it_in_argv(self):
        fictional = 'ghs_example-opaque.jwt~more+parts/even_more=='
        with patch.dict(os.environ, GH_TOKEN=fictional), patch.object(
                release_ci.subprocess, 'run',
                return_value=subprocess.CompletedProcess([], 0, stdout=b'{}')) as curl:
            self.assertEqual(release_ci.api('/repos/Bonzar/craft/actions/artifacts/123'), {})
            argv=curl.call_args.args[0]
            self.assertNotIn(fictional,' '.join(argv))
            self.assertEqual(curl.call_args.kwargs['input'],
                             f'header = "Authorization: Bearer {fictional}"\n'.encode())
        for invalid in ('safe"\nurl = "wrong', 'safe\\continuation', 'spaces in token', 'new\rline'):
            with self.subTest(invalid=invalid), patch.dict(os.environ, GH_TOKEN=invalid), patch.object(
                    release_ci.subprocess, 'run') as curl:
                with self.assertRaises(ValueError):
                    release_ci.api('/repos/Bonzar/craft/actions/artifacts/123')
                curl.assert_not_called()

    def test_approval_fails_closed_without_human_approval_for_prod(self):
        target={'name':'assertion-memory-production'}
        approved=lambda env,person='User',state='approved': {'state':state,'user':{'type':person},'environments':[env]}
        for payload in ([],[approved({'name':'assertion-memory-test'})], [approved(target,person='Bot')], [approved(target,state='rejected')]):
            self.assertFalse(release_ci.tested_approval(payload))
        self.assertTrue(release_ci.tested_approval([approved(target)]))

    def test_inner_archive_matches_exact_artifact_filename_and_digest(self):
        with TemporaryDirectory() as tmp:
            d=Path(tmp)
            name=f'assertion-memory-qa-{REVISION}.tar.gz'
            z=d/'artifact.zip'
            with zipfile.ZipFile(z,'w') as output:
                output.writestr(name, INNER)
                output.writestr(name+'.sha256', f'{DIGEST}  {name}\n')
            self.assertEqual(release_ci.unpack_zip(z,d,REVISION),DIGEST)
            self.assertEqual((d/'release.tar.gz').read_bytes(),INNER)
            with self.assertRaisesRegex(ValueError,'Unexpected'):
                release_ci.unpack_zip(z,d,'b'*40)

    def test_sender_refuses_untested_production_and_missing_config_before_ssh(self):
        with TemporaryDirectory() as tmp:
            d=Path(tmp)
            (d/'release.tar.gz').write_bytes(INNER)
            marker=d/'ssh_called'
            for command,script in {'ssh':f'printf ok > "{marker}"\nexit 99', 'ssh-keygen':'exit 0'}.items():
                p=d/command
                p.write_text('#!/bin/sh\n'+script+'\n')
                p.chmod(0o700)
            env=dict(os.environ, PATH=f'{d}:{os.environ["PATH"]}',
                     RELEASE_ENABLED='true', RELEASE_USER='assertion-production-deploy',
                     RELEASE_HOST='qa.example.test', RELEASE_PORT='22', GITHUB_REPOSITORY='Bonzar/craft',
                     GITHUB_REF='refs/heads/main',GITHUB_SHA=REVISION,
                     GITHUB_WORKFLOW_REF='Bonzar/craft/.github/workflows/assertion-memory-base.yml@refs/heads/main',
                     GITHUB_RUN_ATTEMPT='1',RELEASE_REVISION=REVISION,
                     GITHUB_EVENT_NAME='workflow_dispatch', RELEASE_ARCHIVE_SHA256=DIGEST, ARTIFACT_ID='123',
                     RELEASE_DOWNLOAD_DIR=str(d), SSH_PRIVATE_KEY='not-a-credential',SSH_HOST_PIN='not-a-pin',
                     TESTED_ARTIFACT_ID='123', TESTED_REVISION=REVISION,TESTED_SHA256='0'*64)
            for override in ({}, {'PRODUCTION_READY':'true','HOST_PROOF':'true'},
                             {'RELEASE_ENABLED':''}, {'SSH_PRIVATE_KEY':''}):
                r=subprocess.run([TOOLS/'send-release.sh','production'],env={**env,**override},
                                 text=True,capture_output=True)
                self.assertNotEqual(r.returncode,0)
                self.assertIn('host proof bridge is not integrated',r.stderr)
                self.assertFalse(marker.exists())

    def test_test_sender_requires_restricted_preflight_and_persists_receiver_receipt(self):
        with TemporaryDirectory() as tmp:
            d=Path(tmp)
            (d/'release.tar.gz').write_bytes(INNER)
            env=dict(RELEASE_ENABLED='true',RELEASE_USER='assertion-test-deploy',
                     RELEASE_HOST='qa.example.test',RELEASE_PORT='22',GITHUB_REPOSITORY='Bonzar/craft',
                     GITHUB_REF='refs/heads/main',GITHUB_SHA=REVISION,
                     GITHUB_WORKFLOW_REF=send_test_release.BASE,GITHUB_RUN_ATTEMPT='1',
                     RELEASE_REVISION=REVISION,GITHUB_EVENT_NAME='workflow_dispatch',
                     RELEASE_ARCHIVE_SHA256=DIGEST,ARTIFACT_ID='123',RELEASE_DOWNLOAD_DIR=str(d),
                     SSH_PRIVATE_KEY='fictional',SSH_HOST_PIN='qa.example.test ssh-ed25519 '+'A'*68,
                     GITHUB_OUTPUT=str(d/'output'))
            commands=[]
            denied=False
            def run(args, *, stdin, stdout, **options):
                self.assertEqual(args[0],'/usr/bin/ssh')
                self.assertIn('assertion-test-deploy@qa.example.test',args)
                self.assertNotIn('SSH_PRIVATE_KEY',options['env'])
                command=args[-1]
                commands.append(command)
                if command in ('receiver-probe','id'):
                    if denied:
                        return SimpleNamespace(returncode=0)
                    stdout.write(send_test_release.ssh_transport.DENIED)
                    return SimpleNamespace(returncode=1)
                self.assertEqual(stdin.read(),INNER)
                stdout.write(json.dumps(dict(status='ok',environment='test',revision=REVISION,sha256=DIGEST)).encode())
                return SimpleNamespace(returncode=0)
            with patch.object(send_test_release.ssh_transport.subprocess,'run',side_effect=run):
                denied=True
                with self.assertRaisesRegex(ValueError,'preflight'):
                    send_test_release.send(env)
                self.assertEqual(commands,['receiver-probe'])
                self.assertFalse((d/'receiver-receipt.json').exists())
                commands.clear()
                denied=False
                send_test_release.send(env)
                self.assertEqual(commands,['receiver-probe','id',f'deploy-release test {REVISION} {DIGEST}'])
                self.assertTrue((d/'receiver-receipt.json').is_file())

if __name__=='__main__':
    unittest.main()
