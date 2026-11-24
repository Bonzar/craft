from io import BytesIO
from pathlib import Path
import re
from unittest import TestCase, main
from unittest.mock import Mock
from receive import receive
from render import recipe

class Scope(TestCase):
    def test_two_fixed_recipes(self):
        for stage, opposite in [('test','prod'),('prod','test')]:
            with self.subTest(stage=stage):
                sshd,sudo=recipe(stage)
                self.assertIn(f'Match User assertion-cfg-vis-{stage}-deploy',sshd)
                self.assertIn(f'/usr/bin/python3 -I -B /etc/assertion-memory-visual/configured/receive.py {stage}', sudo)
                self.assertIn(f'ALL=(assertion-visual-{stage}-deploy)',sudo)
                self.assertNotIn(f'assertion-visual-{opposite}-deploy',sshd+sudo)
                self.assertIn('!use_pty, !log_input, !log_output',sudo)
        with self.assertRaises(ValueError):
            recipe('qa')

    def test_routes_and_credentials_are_not_interchangeable(self):
        pattern = re.compile(r'deploy-visual (test|prod) ([a-f0-9]{40}) ([a-f0-9]{64})\Z')
        for stage, opposite in [('test','prod'),('prod','test')]:
            with self.subTest(stage=stage):
                verify=Mock()
                deploy=Mock(return_value='expected-manifest')
                signers=Path(f'/etc/assertion-memory-visual/configured/{stage}/allowed-signers')
                state=Path(f'/var/lib/assertion-memory-visual/{stage}')
                stream=BytesIO(b'fixture')
                command=lambda target: 'deploy-visual '+target+' '+'a'*40+' '+'b'*64
                kw=dict(verify=verify,deploy=deploy,command_pattern=pattern,signers=signers,state=state)
                with self.assertRaises(ValueError):
                    receive(stage,command(opposite),stream,**kw)
                verify.assert_not_called()
                verify.side_effect=ValueError('wrong signer')
                with self.assertRaises(ValueError):
                    receive(stage,command(stage),stream,**kw)
                deploy.assert_not_called()
                verify.side_effect=None
                result=receive(stage,command(stage),stream,**kw)
                verify.assert_called_with(signers,command(stage),stream)
                deploy.assert_called_once_with(stream,'b'*64,root=state)
                self.assertEqual(result['environment'],stage)

if __name__=='__main__':
    main()
