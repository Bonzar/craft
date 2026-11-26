"""Run the exact install/rollback shell logic in an isolated fixture, not on a VM."""
import hashlib
import os
from pathlib import Path
import pty
import re
import subprocess
import tempfile
import unittest


HERE=Path(__file__).resolve().parent
LEGACY=b'toy trusted original entrypoint\n'
UPGRADE=b'toy shared entrypoint\n'
HOOK=b'toy trusted service hook\n'
WRAPPER=b'toy restricted wrapper, called only by a fake runuser\n'


def digest(body):
    return hashlib.sha256(body).hexdigest()


class Adoption(unittest.TestCase):
    def setUp(self):
        fixture=tempfile.TemporaryDirectory(prefix='shared-adopt-fixture-')
        self.addCleanup(fixture.cleanup)
        self.root=Path(fixture.name)
        self.home=self.root/'srv/assertion-release/test'
        self.control=self.root/'etc/assertion-release'
        self.config=self.control/'test'
        self.receiver=self.config/'receive.py'
        self.wrapper=self.config/'receiver-login'
        self.hook=self.config/'service-hook.sh'
        self.keys=self.root/'etc/ssh/assertion-release/test.authorized_keys'
        self.shared=self.control/'shared'
        current=self.home/'releases'/('a'*40+'-'+'b'*64)
        for directory in (self.control,self.config,self.keys.parent,current):
            directory.mkdir(parents=True,exist_ok=True)
            directory.chmod(0o755)
        (current/'.prepared-v1').write_bytes(b'ok\n')
        (self.home/'current').symlink_to(current)
        (self.home/'deploy.lock').touch()
        self.receiver.write_bytes(LEGACY); self.receiver.chmod(0o644)
        self.hook.write_bytes(HOOK); self.hook.chmod(0o755)
        self.wrapper.write_bytes(WRAPPER); self.wrapper.chmod(0o755)
        self.keys.write_bytes(b'fixture key only\n'); self.keys.chmod(0o644)
        self.source=self.root/'upload'
        self.source.mkdir(mode=0o700)
        inputs={'base_payload.py': b'# source fixture\n', 'release_core.py':b'# core fixture\n', 'release_retention.py':b'# retention fixture\n', 'receive.py':UPGRADE}
        for name,body in inputs.items():
            path=self.source/name; path.write_bytes(body); path.chmod(0o644)
        sums=b''.join(f'{digest(body)}  {name}\n'.encode() for name,body in sorted(inputs.items()))
        (self.source/'SHA256SUMS').write_bytes(sums)
        (self.source/'SHA256SUMS').chmod(0o644)
        self.manifest=digest(sums)
        shim=self.root/'bin'
        shim.mkdir()
        st=shim/'stat'
        st.write_text("""#!/bin/bash
case "$2" in
  %u) echo 0;;
  %u:%g:%a) echo "0:0:$(/usr/bin/stat -c %a "${@: -1}")";;
  *) exec /usr/bin/stat "$@";;
esac
""")
        getent=shim/'getent'
        getent.write_text(f"#!/bin/sh\nprintf '%s\\n' 'assertion-test-deploy:x:101:101:Fixture:{self.home}:{self.wrapper}'\n")
        runuser=shim/'runuser'
        runuser.write_text(f"""#!/bin/bash
if [[ "$*" == *'SSH_ORIGINAL_COMMAND=receiver-probe'* ]]; then
    if [[ "${{TAMPER_SOURCE:-0}}" == 1 ]]; then
      printf 'tampered after preflight' > '{self.source}/receive.py'
    fi
    if [[ "${{FAIL_NEW:-0}}" == 1 ]] && cmp -s '{self.receiver}' '{self.source}/receive.py'; then touch '{self.root}/failed-new-receiver-probed'; exit 2; fi
    printf '%s\\n' '{{"status":"rejected","error":"command_denied"}}'
    exit 1
fi
if [[ "$*" == *'-c id' ]]; then exit 126; fi
exit 7
""")
        for path in (st,getent,runuser): path.chmod(0o700)
        text=(HERE/'adopt-live-base-test.sh').read_text()
        # Only host-root metadata, authorized program identities and paths
        # are replaced; no deployment control flow is replaced.
        text=text.replace('export PATH=/usr/sbin:/usr/bin:/sbin:/bin',f'export PATH={shim}:/usr/sbin:/usr/bin:/sbin:/bin')
        text=text.replace('$EUID == 0','0 == 0').replace(' -o root -g root','')
        for path in ('/etc/assertion-release','/etc/ssh/assertion-release','/srv/assertion-release/test'):
            text=text.replace(path,str(self.root)+path)
        text=re.sub(r'legacy_sha=[a-f0-9]{64}',f'legacy_sha={digest(LEGACY)}',text)
        text=re.sub(r'pinned_hook=[a-f0-9]{64}',f'pinned_hook={digest(HOOK)}',text)
        self.script=self.root/'adopt-fixture.sh'
        self.script.write_text(text)

    def run_script(self,mode,*,fail_new=False,tamper_source=False):
        master,slave=pty.openpty()
        try:
            proc=subprocess.Popen(['/bin/bash',str(self.script),mode,str(self.source),self.manifest,digest(WRAPPER)],
                 stdin=slave,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                 env={**os.environ,'FAIL_NEW':str(int(fail_new)),'TAMPER_SOURCE':str(int(tamper_source))})
            os.write(master,b'VERIFIED-ADMIN\n')
            out,err=proc.communicate(timeout=6)
            return proc.returncode,out,err
        finally:
            os.close(master)
            os.close(slave)

    def test_check_only_and_tampered_trusted_hook_fail_without_changes(self):
        rc,out,err=self.run_script('--check')
        self.assertEqual(rc,0,err)
        self.assertIn(b'unchanged receiver',out)
        self.assertFalse(self.shared.exists())
        self.hook.write_bytes(b'same mode but changed hook\n')
        self.assertNotEqual(self.run_script('--check')[0],0)
        self.assertEqual(self.receiver.read_bytes(),LEGACY)

    def test_apply_executable_hook_kept_and_receiver_replaced_in_place(self):
        pointer=(self.home/'current').readlink()
        rc,out,err=self.run_script('--apply')
        self.assertEqual(rc,0,err)
        self.assertEqual(self.receiver.read_bytes(),UPGRADE)
        self.assertEqual(self.receiver.stat().st_mode&0o777,0o644)
        self.assertEqual(self.hook.stat().st_mode&0o777,0o755)
        self.assertEqual((self.home/'current').readlink(),pointer)
        for name in ('release_core.py','base_payload.py','release_retention.py'):
            self.assertEqual((self.shared/name).read_bytes(),(self.source/name).read_bytes())
        self.assertEqual({p.name for p in self.shared.iterdir()},
                         {'release_core.py','base_payload.py','release_retention.py'})
        self.assertEqual([p.read_bytes() for p in self.config.glob('.receive-before-shared.*')],[LEGACY])

    def test_failed_new_receiver_probe_restores_exact_old_entrypoint(self):
        rc,out,err=self.run_script('--apply',fail_new=True)
        self.assertNotEqual(rc,0)
        self.assertTrue((self.root/'failed-new-receiver-probed').exists())
        self.assertEqual(self.receiver.read_bytes(),LEGACY)
        self.assertFalse(self.shared.exists())
        self.assertEqual(self.keys.read_bytes(),b'fixture key only\n')


    def test_incomplete_retention_dependency_is_rejected_before_first_install(self):
        (self.source/'release_retention.py').unlink()
        manifests=(self.source/'SHA256SUMS')
        content=b''.join(line+b'\n' for line in manifests.read_bytes().splitlines()
                         if not line.endswith(b'  release_retention.py'))
        manifests.write_bytes(content)
        self.manifest=digest(content)
        self.assertNotEqual(self.run_script('--check')[0],0)
        self.assertEqual(self.receiver.read_bytes(),LEGACY)
        self.assertFalse(self.shared.exists())

    def test_changed_source_after_preflight_is_never_installed(self):
        rc,out,err=self.run_script('--apply',tamper_source=True)
        self.assertNotEqual(rc,0)
        self.assertEqual((self.source/'receive.py').read_bytes(),b'tampered after preflight')
        self.assertEqual(self.receiver.read_bytes(),LEGACY)
        self.assertFalse(self.shared.exists())


if __name__=='__main__':unittest.main()
