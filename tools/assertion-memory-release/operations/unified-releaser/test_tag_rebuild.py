from dataclasses import replace
from hashlib import sha256
from unittest import TestCase
from build_releaser import asset
from releaser import LANES
from tag_rebuild import TagSelection, TagRun, qualify, dispatch


class TagRebuild(TestCase):
    def setUp(self):
        raw=asset(); commit='b'*40
        self.s=TagSelection('test','apply','assertion-releaser-v1.0.0',3,commit,sha256(raw).hexdigest())
        self.r=TagRun('Bonzar/craft','workflow_dispatch','refs/heads/main',True,
            LANES['test'].environment,True,self.s.release_id,self.s.tag,commit,commit,commit,True,raw)

    def test_same_verified_digest_and_no_asset_in_both_lanes(self):
        self.assertEqual(qualify(self.s,self.r),self.s.digest)
        prod=replace(self.s,lane='prod',operation='rollback')
        self.assertEqual(qualify(prod,replace(self.r,environment=LANES['prod'].environment)),self.s.digest)

    def test_tag_moved_reused_or_unapproved_head_fails(self):
        for run in (replace(self.r,last_ref_commit='c'*40), replace(self.r,release_id=4),
                    replace(self.r,immutable=False),replace(self.r,checkout_commit='c'*40),
                    replace(self.r,checked_commit_approved=False)):
            with self.assertRaises(ValueError):
                qualify(self.s,run)

    def test_wrong_digest_environment_or_trigger(self):
        for selection, run in (
            (replace(self.s,digest='d'*64),self.r),
            (self.s,replace(self.r,environment=LANES['prod'].environment)),
            (self.s,replace(self.r,trigger='release')),
            (self.s,replace(self.r,control_branch_protected=False)),
        ):
            with self.assertRaises(ValueError):
                qualify(selection,run)

    def test_proven_rebuild_cannot_manufacture_executor(self):
        for configured, io in (('test',None),('prod',object())):
            with self.assertRaisesRegex(ValueError,'executor'):
                dispatch(self.s,self.r,None,configured_lane=configured,verify=lambda *_:True,io=io)
