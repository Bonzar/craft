"""Offline release transport checks; no external HTTPS or real SSH."""
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
import zipfile

TOOLS=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('release_ci', TOOLS/'release-ci.py')
release_ci=importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_ci)
REVISION='a'*40
INNER=b'fictional bytes only'
DIGEST=hashlib.sha256(INNER).hexdigest()

class ReleaseTest(unittest.TestCase):
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
                     GITHUB_REF='refs/heads/codex/assertion-memory-release',GITHUB_SHA=REVISION,
                     GITHUB_EVENT_NAME='push', RELEASE_ARCHIVE_SHA256=DIGEST, ARTIFACT_ID='123',
                     RELEASE_DOWNLOAD_DIR=str(d), SSH_PRIVATE_KEY='not-a-credential',SSH_HOST_PIN='not-a-pin',
                     TESTED_ARTIFACT_ID='123', TESTED_REVISION=REVISION,TESTED_SHA256='0'*64)
            for override, expected in [({},'exact green TEST artifact'),({'RELEASE_ENABLED':''},'not provisioned'),
                                       ({'SSH_PRIVATE_KEY':''},'credentials')]:
                r=subprocess.run([TOOLS/'send-release.sh','production'],env={**env,**override},
                                 text=True,capture_output=True)
                self.assertNotEqual(r.returncode,0)
                self.assertIn(expected,r.stderr)
                self.assertFalse(marker.exists())

if __name__=='__main__':
    unittest.main()
