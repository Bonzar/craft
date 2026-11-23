import io,pathlib,subprocess,tempfile,unittest
from visual_auth import sign, verify_frame
from visual_receive import run


class TestVisualAuth(unittest.TestCase):
    def test_receiver_negative_probe_matches_common_transport(self):
        for command in ('receiver-probe','id','deploy-visual prod '+'a'*40+' '+'b'*64):
            self.assertEqual(run('test',command,io.BytesIO()),
                             ({'status':'rejected','error':'command_denied'},1))

    def test_proof_binds_scope_revision_and_byte_digest_and_preserves_stream(self):
        with tempfile.TemporaryDirectory() as path:
            p=pathlib.Path(path)
            key=p/'key'
            subprocess.run(['ssh-keygen','-q','-N','','-t','ed25519','-f',str(key)],check=True)
            signers=p/'signers'
            signers.write_text('visual-test,visual-prod '+key.with_suffix('.pub').read_text())
            good='deploy-visual test '+'a'*40+' '+'b'*64
            signature=sign(good,key.read_text())
            wrapped=io.BytesIO(signature+b'archive')
            verify_frame(signers,good,wrapped)
            self.assertEqual(wrapped.read(),b'archive')
            for altered in ['deploy-visual prod '+'a'*40+' '+'b'*64,
                            'deploy-visual test '+'c'*40+' '+'b'*64,
                            'deploy-visual test '+'a'*40+' '+'c'*64]:
                with self.assertRaisesRegex(ValueError,'signature'):
                    verify_frame(signers,altered,io.BytesIO(signature))


if __name__=='__main__':
    unittest.main()
