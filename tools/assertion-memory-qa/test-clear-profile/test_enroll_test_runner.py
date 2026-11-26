from contextlib import nullcontext
import hashlib
import json
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import enroll_test_runner as e

DOC=dict(capabilities=['baseline_read'],read_token_file=e.EXPECTED_PATHS['read_token_file'],effect_token_file=None)
RAW=json.dumps(DOC).encode()
ORIGINAL=hashlib.sha256(RAW).hexdigest()

class EnrollTest(unittest.TestCase):
    def test_only_one_known_fingerprint_will_be_added(self):
        digest='f'*64
        new=json.loads(e.enroll_payload(DOC,{},digest))
        self.assertEqual(new,DOC|{'machine_clients':{e.CLIENT:digest}})
        for registry in ({e.CLIENT:digest},{'other':digest}):
            with self.assertRaises(ValueError):e.enroll_payload(DOC,registry,digest)
        with self.assertRaises(ValueError):e.enroll_payload(DOC,{},'not-a-digest')

    def test_unknown_paths_rejected_before_opening(self):
        with patch.object(e.os,'open') as io:
            with self.assertRaises(ValueError):e.assign_root_file(e.DIR/'other',b'',0o600,0,absent_only=True)
            with self.assertRaises(ValueError):e.assign_root_file(e.KEY,b'',0o640,0,absent_only=True)
            io.assert_not_called()

    def test_pin_and_existing_key_prevent_second_mint(self):
        st=SimpleNamespace(st_mode=stat.S_IFREG|0o644,st_gid=0)
        edge=b'fixture edge bytes'
        with patch.object(e,'SHIELDED_EDGE_SHA',hashlib.sha256(edge).hexdigest()), \
             patch.object(e,'guard_root'),patch.object(e,'trusted_dir'), \
             patch.object(e.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=994)), \
             patch.object(e,'activation_lock',return_value=nullcontext()), \
             patch.object(e,'ensure_current',return_value=['exact-test-prefix']), \
             patch.object(e,'existing',return_value=(RAW,st,DOC,{})) as prior, \
             patch.object(e,'absent',return_value=True) as empty, \
             patch.object(e.secrets,'token_hex',return_value='a'*64) as mint, \
             patch.object(e,'assign_root_file') as record, \
             patch.object(e,'regular_bytes') as read, \
             patch.object(e,'credential',return_value='a'*64), \
             patch.object(e.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run:
            read.return_value=b'wrong edge'
            with self.assertRaisesRegex(ValueError,'isolated TEST neutral edge'):
                e.enroll(ORIGINAL)
            read.side_effect=lambda path,**opts:edge if path==e.EDGE_FILE else e.enroll_payload(DOC,{},hashlib.sha256(b'a'*64).hexdigest())
            with self.assertRaises(ValueError): e.enroll('b'*64)
            empty.return_value=False
            with self.assertRaises(ValueError): e.enroll(ORIGINAL)
            mint.assert_not_called();record.assert_not_called();run.assert_not_called()
            empty.return_value=True
            out=e.enroll(ORIGINAL)
            self.assertEqual(out['stage'],'test')
            self.assertNotIn('a'*64,str(out))
            self.assertTrue(out['fingerprint_registered'])
            mint.assert_called_once_with(32)
            self.assertEqual(record.call_count,2)
            self.assertEqual(run.call_args.args[0],['exact-test-prefix','restart','assertion-test-deploy-mcp.service'])

if __name__=='__main__':unittest.main()
