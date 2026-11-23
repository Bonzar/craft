from dataclasses import asdict
import io
import hashlib
import json
import unittest

from receiver_gate import handle
from reusable_proof import Intent


class TestReceiverGate(unittest.TestCase):
    def test_exact_intent_and_archive_route(self):
        archive=b'fictional archive bytes'
        digest=hashlib.sha256(archive).hexdigest()
        intent=Intent('11','12','13','14','15','16','a'*40,digest)
        value={'intent':asdict(intent),'nonce':'d'*64,'token':'synthetic fixture only'}
        allowed={'status':'ok','environment':'production','revision':intent.revision,'sha256':digest}
        command=f'deploy-release production {intent.revision} {digest}'
        seen=[]
        def deploy(revision,sha,stream):
            self.assertEqual((revision,sha),(intent.revision,digest))
            self.assertEqual(stream.read(),archive)
            seen.append(True)
            return 'ok'
        def wire():
            return io.BytesIO(json.dumps(value).encode()+b'\n'+archive)
        for receive in ({'status':'rejected'},{**allowed,'sha256':'b'*64}):
            with self.assertRaisesRegex(ValueError,'denied'):
                handle(command,wire(),deploy,authorize=lambda p:receive)
        with self.assertRaisesRegex(ValueError,'disagree'):
            handle(command[:-1]+'f' if command[-1]!='f' else command[:-1]+'e',wire(),deploy,
                   authorize=lambda p: self.fail('Mismatched command reached authorizer'))
        for invalid in ('id','sh','deploy-release test '+intent.revision+' '+digest):
            with self.assertRaisesRegex(ValueError,'command_denied'):
                handle(invalid,wire(),deploy,authorize=lambda p: self.fail('Nonpromotion command reached authorizer'))
        self.assertEqual(seen,[])
        self.assertEqual(handle(command,wire(),deploy,authorize=lambda p:allowed),'ok')
        self.assertEqual(seen,[True])
