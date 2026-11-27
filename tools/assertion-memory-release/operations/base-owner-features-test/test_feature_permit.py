from dataclasses import replace
import hmac
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import feature_permit as p
import test_transaction as t
from transaction import Refused, digest


def signature(data):
    return hmac.digest(b'fictional isolated verifier fixture only',data,'sha256')


def check(wire, sig, *, protected_run):
    return protected_run=='42' and hmac.compare_digest(signature(wire),sig)


class OwnerFeaturePermit(TestCase):
    def permit(self, request):
        return p.Permit(1,p.PURPOSE,'test',request.feature,request.operation,request.receipt_id,
                        digest(request.canonical()),request.candidate_digest,'42',100,160)

    def execute(self, runner, permit, sig, request, raw, *, now=110):
        return p.apply_with_permit(permit,sig,request,raw,clock=lambda:now,verify_existing=check,runner=runner)

    def rejected(self, runner, expected, permit, sig, req, raw, **options):
        before=runner.read_only_snapshot()
        with self.assertRaisesRegex(Refused,expected):
            self.execute(runner,permit,sig,req,raw,**options)
        self.assertEqual(runner.read_only_snapshot(),before)

    def test_normal_signed_deploy_receipt_is_not_feature_authorization(self):
        req,raw=t.LocalTransactions().request('graph')
        permit=self.permit(req)
        deploy=b'{"environment":"test","status":"ok","revision":"' + b'a'*40 + b'"}'
        with TemporaryDirectory() as root:
            runner=t.RecordingTestRunner(Path(root),req)
            self.rejected(runner,'unauthenticated',{'environment':'test','status':'ok'},signature(deploy),req,raw)
            self.rejected(runner,'signature_rejected',permit,signature(deploy),req,raw)
            wrong=replace(permit,purpose='deploy_release')
            self.rejected(runner,'wrong_or_expired',wrong,signature(wrong.signed_bytes()),req,raw)

    def test_graph_and_registry_cannot_share_signature_or_receipt_purpose(self):
        graph,g=t.LocalTransactions().request('graph')
        device,d=t.LocalTransactions().request('devices')
        for established,other,other_bytes in ((graph,device,d),(device,graph,g)):
            with self.subTest(feature=established.feature), TemporaryDirectory() as root:
                runner=t.RecordingTestRunner(Path(root),established)
                permit=self.permit(established); sig=signature(permit.signed_bytes())
                self.rejected(runner,'unauthenticated',permit,sig,other,other_bytes)
                forged=self.permit(other)
                self.rejected(runner,'signature_rejected',forged,sig,other,other_bytes)
                mismatch=replace(forged,feature=established.feature)
                self.rejected(runner,'wrong_or_expired',mismatch,signature(mismatch.signed_bytes()),other,other_bytes)

    def test_once_pending_and_completed_no_replay_and_action_exact(self):
        for feature in ('graph','devices'):
            req,raw=t.LocalTransactions().request(feature)
            permit=self.permit(req); sig=signature(permit.signed_bytes())
            for success in (True,False):
                with TemporaryDirectory() as root:
                    runner=t.RecordingTestRunner(Path(root),req)
                    runner.smoke_ok=success
                    self.rejected(runner,'wrong_or_expired',permit,sig,req,raw,now=160)
                    wrong=replace(permit,action='inverse')
                    self.rejected(runner,'wrong_or_expired',wrong,signature(wrong.signed_bytes()),req,raw)
                    if success:
                        self.execute(runner,permit,sig,req,raw)
                    else:
                        with self.assertRaisesRegex(Refused,'smoke_uncertain'):
                            self.execute(runner,permit,sig,req,raw)
                    self.rejected(runner,'receipt_already_claimed',permit,sig,req,raw)
