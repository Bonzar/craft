from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import configured_visual as subject

class Configured(unittest.TestCase):
    def test_send_never_uses_legacy_lane_or_default(self):
        with patch.object(subject, 'require_context', return_value='test') as gate:
            with patch('visual_send.deploy') as send:
                with self.assertRaises(ValueError):
                    subject.run('send', {'VISUAL_DEPLOY_ENABLED':'true'})
                gate.assert_called_with({'VISUAL_DEPLOY_ENABLED':'true'}, subject.CONFIGURED_VISUAL)
                send.assert_not_called()
                env=dict(CONFIGURED_VISUAL_DEPLOY_ENABLED='true', VISUAL_HOST_PIN='x', VISUAL_SSH_KEY='x')
                with self.assertRaises(ValueError):
                    subject.run('send',env)
                send.assert_not_called()
                env['VISUAL_SIGNING_KEY']='x'
                subject.run('send',env)
                send.assert_called_once_with(env,lane='configured')

    def test_legacy_receipt_cannot_be_written(self):
        with tempfile.TemporaryDirectory() as folder:
            env=dict(VISUAL_DOWNLOAD=folder,NATIVE_VISUAL_TEST_PROOF='{"release":"fictional"}')
            with patch.object(subject,'require_context',return_value='test'):
                with self.assertRaises(ValueError):
                    subject.run('receipt',env)
                self.assertEqual(list(Path(folder).iterdir()),[])
