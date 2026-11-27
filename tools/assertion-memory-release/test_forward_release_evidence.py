from dataclasses import replace
from pathlib import Path
import unittest

from forward_release_checkpoints import Published
from forward_release_evidence import ProtectedDispatch, ReviewedFailure, fix_approval, fresh_probe, verify_dispatch
from forward_release_receiver import Request
from forward_release_train import Reject
from test_forward_release_train import art,cand,witness


class Evidence(unittest.TestCase):
    def test_signed_existing_auth_is_bound_to_exact_run_stage_and_test_generation(self):
        a=art(cand('1.0.0','a'),6)
        req=Request(a,'prod',12,Path('/owned'))
        auth=ProtectedDispatch(req,Published(a.candidate,6,True),123,True,'a'*64)
        def check(proof, request=req, generation='a'*64):
            return verify_dispatch(proof,request,generation,workflow_ids={('base','prod'):123})
        self.assertTrue(check(auth))
        for proof, request,generation in [
            (auth,replace(req,run_id=11),'a'*64),
            (auth,req,'b'*64),
            (replace(auth,stage_approved=False),req,'a'*64),
            (replace(auth,release_workflow_id=124),req,'a'*64),
            (replace(auth,published=replace(auth.published,release_id=9)),req,'a'*64),
        ]:
            with self.assertRaises(Reject):
                check(proof,request,generation)
        with self.assertRaises(Reject):
            fresh_probe(witness('prod',a,origin='rollback'),'prod',a)

    def test_fix_requires_exact_same_artifact_and_protected_review(self):
        a,b=art(cand('1.0.0','a'),6),art(cand('1.1.0','b'),7)
        review=ReviewedFailure(a,b,80,81,'a'*64,True,True)
        self.assertEqual(fix_approval(review,b).predecessor,a)
        for r in (replace(review,replacement=replace(b,digest='z'*64)),
                  replace(review,independently_reviewed_defect_only=False),
                  replace(review,independently_verified_failure=False),None):
            with self.assertRaises(Reject):
                fix_approval(r,b)
