from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch
import configured_visual_profile as profile

class VisualOperations(TestCase):
    def test_project_format_delegates_and_has_no_outer_check(self):
        with patch.object(profile.lifecycle, 'unpack', return_value=('b'*64, 'c'*64)) as op:
            p=profile.unpack_verified('/approved.zip','/fresh','a'*40)
            op.assert_called_once_with(Path('/approved.zip'),Path('/fresh'),'a'*40)
        self.assertEqual(p.release_digest,'c'*64)

    def test_visual_scope_and_no_legacy_fallback(self):
        payload = profile.Payload(Path('/fresh'),'a'*40,'b'*64,'c'*64)
        config=dict(account='assertion-configured-visual-test-deploy',host='fixture',port='22',
                    host_pin='pin',ssh_key='fixture',signing_key='fixture',runner_temp='/tmp')
        with patch.object(profile.visual_send,'deploy') as sender:
            sender.return_value=dict(environment='test',revision='a'*40,archive_sha256='b'*64,release='c'*64)
            for stage, vals in [('prod', config),('test',config | {'account':'assertion-visual-test-deploy'})]:
                with self.assertRaises(ValueError):
                    profile.activate(stage,payload,vals)
            sender.assert_not_called()
            receipt=profile.activate('test',payload,config)
            self.assertEqual(sender.call_args.kwargs, {'lane':'configured'})
            self.assertEqual(receipt,{'environment':'test','revision':'a'*40,'archive_sha256':'b'*64,'release':'c'*64})

    def test_transport_account_allowlist_is_exact(self):
        from ssh_transport import ACCOUNT
        for suffix in ("test", "prod"):
            self.assertIsNotNone(ACCOUNT.fullmatch(f"assertion-configured-visual-{suffix}-deploy"))
        for suffix in ("qa", "test-prod", "production"):
            self.assertIsNone(ACCOUNT.fullmatch(f"assertion-configured-visual-{suffix}-deploy"))

    def test_gateway_smoke_uses_matching_stage(self):
        p = profile.Payload(Path('/fresh'),'a'*40,'b'*64,'c'*64)
        with patch.object(profile.smoke,'main') as call:
            profile.verify_smoke('prod',p,dict(gateway_host='test-fixture',gateway_token='dummy'))
            self.assertEqual(call.call_args.args[0]['VISUAL_ENV'],'prod')
            self.assertEqual(call.call_args.args[0]['VISUAL_DIGEST'],'c'*64)

if __name__=='__main__':
    main()
