import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from receive import _verify_signed_archive


class SignerTest(unittest.TestCase):
    def test_signature_binds_deploy_scope_and_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            key = p / 'key'
            subprocess.run(['ssh-keygen','-q','-N','','-t','ed25519','-f',str(key)], check=True)
            signers = p / 'allowed-signers'
            signers.write_text('visual-test,visual-prod ' + key.with_suffix('.pub').read_text())
            sha = 'a' * 64
            message = p / 'message'
            message.write_text(f'assertion-memory-visual-v1 test {sha}\n')
            subprocess.run(['ssh-keygen','-Y','sign','-f',str(key),'-n','assertion-memory-visual',
                            str(message)], check=True, capture_output=True)
            signature = message.with_suffix('.sig').read_bytes()
            frame = f'{len(signature)}\n'.encode() + signature + b'archive'
            self.assertEqual(_verify_signed_archive(signers, 'test', sha, io.BytesIO(frame)), b'archive')
            for mode, bad_sha in [('prod', sha), ('test', 'b' * 64)]:
                with self.assertRaisesRegex(ValueError, 'signature rejected'):
                    _verify_signed_archive(signers, mode, bad_sha, io.BytesIO(frame))
            with self.assertRaisesRegex(ValueError, 'signature framing'):
                _verify_signed_archive(signers, 'test', sha, io.BytesIO(b'0003\njunk'))

if __name__ == "__main__": unittest.main()
