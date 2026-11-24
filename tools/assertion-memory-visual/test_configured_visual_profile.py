from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch
import configured_visual_profile as profile

class VisualOperations(TestCase):
    def test_project_format_delegates_and_has_no_outer_check(self):
        with patch.object(profile._operation('lifecycle'), 'unpack', return_value=('b'*64, 'c'*64)) as op:
            p=profile.unpack_verified('/approved.zip','/fresh','a'*40)
            op.assert_called_once_with(Path('/approved.zip'),Path('/fresh'),'a'*40)
        self.assertEqual(p.release_digest,'c'*64)

    def test_visual_scope_and_no_legacy_fallback(self):
        payload = profile.Payload(Path('/fresh'),'a'*40,'b'*64,'c'*64)
        config=dict(account='assertion-cfg-vis-test-deploy',host='fixture',port='22',
                    host_pin='pin',ssh_key='fixture',signing_key='fixture',runner_temp='/tmp')
        with patch.object(profile._operation('visual_send'),'deploy') as sender:
            sender.return_value=dict(environment='test',revision='a'*40,archive_sha256='b'*64,release='c'*64)
            for stage, vals in [('prod', config),('test',config | {'account':'assertion-visual-test-deploy'})]:
                with self.assertRaises(ValueError):
                    profile.activate(stage,payload,vals)
            sender.assert_not_called()
            receipt=profile.activate('test',payload,config)
            self.assertEqual(sender.call_args.kwargs, {'lane':'configured'})
            self.assertEqual(receipt,{'environment':'test','revision':'a'*40,'archive_sha256':'b'*64,'release':'c'*64})

    def test_configured_route_reuses_pinned_builder_without_legacy_edits(self):
        from configured_visual_route import route
        from ssh_transport import ACCOUNT
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            options=dict(host="example.invalid",port="22",host_pin="ssh-ed25519 "+"A"*68,
                         key="fixture-only",runtime_dir=tmp)
            for stage in ("test", "prod"):
                name=f"assertion-cfg-vis-{stage}-deploy"
                self.assertIsNone(ACCOUNT.fullmatch(name))
                with route(account=name, **options) as argv:
                    self.assertEqual(argv[-2:], ["--",name+"@example.invalid"])
                    self.assertIn("StrictHostKeyChecking=yes",argv)
                    self.assertIn("ClearAllForwardings=yes",argv)
            for name in ("assertion-cfg-vis-qa-deploy", "assertion-visual-test-deploy"):
                with self.assertRaises(ValueError):
                    with route(account=name, **options):
                        self.fail("wrong account was admitted")

    def test_sender_and_profile_use_same_exact_account(self):
        from contextlib import contextmanager
        from tempfile import TemporaryDirectory
        import json
        sender = profile._operation("visual_send")
        scoped = profile._operation("configured_visual_route")
        with TemporaryDirectory() as tmp:
            (Path(tmp)/"visual.tar").write_bytes(b"fixture")
            for stage in ("test","prod"):
                receipt = dict(environment=stage,revision="a"*40,archive_sha256="b"*64,release="c"*64)
                seen = []
                @contextmanager
                def route(**kwargs):
                    seen.append(kwargs["account"])
                    yield ["ssh-fixture"]
                with (patch.object(scoped,"route",route), patch.object(sender,"preflight"),
                      patch.object(sender,"sign",return_value=b"fake"),
                      patch.object(sender,"exchange",return_value=(0,json.dumps(receipt,separators=(",",":")).encode()+b"\n"))):
                    sender.deploy(dict(VISUAL_ENV=stage,VISUAL_REVISION="a"*40,VISUAL_SHA="b"*64,
                        VISUAL_DIGEST="c"*64,VISUAL_HOST="example.invalid",VISUAL_PORT="22",VISUAL_HOST_PIN="example.invalid pin",
                        RUNNER_TEMP=tmp,VISUAL_SIGNING_KEY="fixture",VISUAL_SSH_KEY="fixture",VISUAL_DOWNLOAD=tmp),lane="configured")
                self.assertEqual(seen,[profile.TARGETS[stage].receiver_account])

    def test_gateway_smoke_uses_matching_stage(self):
        p = profile.Payload(Path('/fresh'),'a'*40,'b'*64,'c'*64)
        with patch.object(profile._operation('smoke'),'main') as call:
            profile.verify_smoke('prod',p,dict(gateway_host='test-fixture',gateway_token='dummy'))
            self.assertEqual(call.call_args.args[0]['VISUAL_ENV'],'prod')
            self.assertEqual(call.call_args.args[0]['VISUAL_DIGEST'],'c'*64)

if __name__=='__main__':
    main()
