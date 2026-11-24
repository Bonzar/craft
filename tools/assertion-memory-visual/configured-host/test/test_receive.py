from io import BytesIO
from pathlib import Path
import re
from unittest import TestCase, main
from unittest.mock import Mock
from receive import receive

class Scope(TestCase):
    def test_stage_signature_target_and_no_legacy_key_lookup(self):
        pattern = re.compile(r'deploy-visual (test|prod) ([a-f0-9]{40}) ([a-f0-9]{64})\Z')
        verify = Mock()
        deploy = Mock(return_value='expected-manifest')
        signers = Path('/configured/allowlist')
        state = Path('/canonical/test')
        stream = BytesIO(b'mock byte stream')
        cmd = lambda stage: 'deploy-visual '+stage+' '+'a'*40+' '+'b'*64
        kwargs = dict(verify=verify,deploy=deploy,command_pattern=pattern,signers=signers,state=state)
        with self.assertRaises(ValueError):
            receive(cmd('prod'),stream,**kwargs)
        verify.assert_not_called()
        verify.side_effect=ValueError('legacy signer rejected')
        with self.assertRaises(ValueError):
            receive(cmd('test'),stream,**kwargs)
        deploy.assert_not_called()
        verify.side_effect=None
        result=receive(cmd('test'),stream,**kwargs)
        verify.assert_called_with(signers,cmd('test'),stream)
        deploy.assert_called_once_with(stream,'b'*64,root=state)
        self.assertEqual(result['release'],'expected-manifest')

if __name__=='__main__':
    main()
