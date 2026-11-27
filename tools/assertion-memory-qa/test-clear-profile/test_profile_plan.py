from copy import deepcopy
from hashlib import sha256
import json
import unittest
from profile_plan import *

REV='a'*40
ARCHIVE='b'*64
TAG='assertion-memory-v0.1.36'
UID=994
TARGET=TEST_BASE+'/'+REV+'-'+ARCHIVE
SCOPE='c'*64
MANIFEST='f'*64
EVIDENCE=dict(unit=TEST_UNIT,account='assertion-test-deploy',stage='test',database=TEST_DB,origin=TEST_ORIGIN,
              current=TARGET,loaded_cwd=TARGET,release_generation=release_generation(REV,ARCHIVE,TAG,'test'),
              release_version=TAG,activated_same_origin=True,signed_members_verified=True,marker_absent=True,
              journal_absent=True,old_alias_denied=True,graph_usable=True,base_mcp_healthy=True,
              profile_code_present=True,manifest_sha256=MANIFEST,uid=UID,effective_uid=UID,configured_uid=UID,database_scope=SCOPE)

def demand(e,**kwargs):
    return plan(e,revision=REV,archive=ARCHIVE,tag=TAG,expected_evidence_sha256=sha256(compact(e)).hexdigest(),expected_manifest_sha256=MANIFEST,**kwargs)

class TestProfilePlan(unittest.TestCase):
    def test_exact_reusable_and_one_shot_bytes(self):
        for scope in (None,SCOPE):
            p=demand(EVIDENCE,database_scope=scope)
            self.assertEqual((p['marker'],p['unit'],p['mode'],p['owner']),(TEST_MARKER,TEST_UNIT,0o644,0))
            self.assertFalse(p['writes_performed'])
            self.assertFalse(p['deletion_performed'])
            self.assertEqual(sha256(p['profile']).hexdigest(),p['marker_sha256'])
            self.assertEqual(json.loads(p['profile']),dict(version=1,stage='test',
                release_generation=EVIDENCE['release_generation'],database_scope=scope))

    def test_reject_scope_mode_drift(self):
        for scope in ('b'*64,'none',True):
            with self.assertRaises(ValueError):demand(EVIDENCE,database_scope=scope)

    def test_release_generation_matches_signed_runtime_algorithm(self):
        from prototypes.assertion_memory.dashboard_projection import release_generation as reference
        self.assertEqual(release_generation(REV,ARCHIVE,TAG,'test'),reference(REV,ARCHIVE,TAG,'test'))
    def test_wrong_release_or_lane_fails_closed(self):
        for name,value in dict(stage='prod',origin='https://different.example',database='/tmp/no',
            uid=0,effective_uid=True,loaded_cwd=TARGET+'/other',release_generation='d'*64,
            release_version='assertion-memory-v0.1.35',profile_code_present=False,
            old_alias_denied=False,marker_absent=False,journal_absent=False,signed_members_verified=False,
            activated_same_origin=False,manifest_sha256='e'*64,graph_usable=False,base_mcp_healthy=False).items():
            with self.subTest(name=name),self.assertRaises(ValueError):demand(EVIDENCE|{name:value})

    def test_no_unauthenticated_written_evidence_fields(self):
        with self.assertRaises(ValueError):demand(EVIDENCE|{'credential':'fake'})
        with self.assertRaises(ValueError):
            plan(EVIDENCE,revision=REV,archive=ARCHIVE,tag=TAG,expected_evidence_sha256='0'*64,expected_manifest_sha256=MANIFEST)

if __name__=='__main__':
    unittest.main()
