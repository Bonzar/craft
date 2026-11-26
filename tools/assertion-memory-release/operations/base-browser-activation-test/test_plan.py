from copy import deepcopy
import unittest
from plan import *

SMOKE=SMOKE_V2
LIVE=ROOT+'/releases/'+REVISION+'-'+ARCHIVE
BASE={
 'unit':UNIT,'account':ACCOUNT,'uid':1042,'effective_uid':1042,'active_pid':765,
 'listener':'127.0.0.1:8891','cwd':LIVE,'current':LIVE,
 'effective_argv':[INTERPRETER,'-B','-m','apps.graph_viewer.server',
   '--source',SOURCE,'--state-dir',VIEWER_STATE,'--password-file',PASSWORD,
   '--static-dir',STATIC,'--port','8891','--public-origin',ORIGIN,
   '--synthetic-fixture','--test-direct-events-origin','https://8.8.8.8'],
 'drop_in_absent':True,'installed_dropins':SSE_DROPIN,'smoke_helper_sha256':SMOKE,'dashboard_pair_current':True,
 'baseline_graph_smoke':True,'browser_mode_dark':True,'clear_dark':True,
 'legacy_graph_authority_absent':True,
}

def render(x, sha=None):
    return derive(x,expected_archive=ARCHIVE,expected_uid=1042,
                  expected_before_sha256=sha or digest(spec_bytes(x)),expected_smoke_helper_sha256=SMOKE)

class TestActivation(unittest.TestCase):
    def test_only_appends_required_pair_and_one_adapter_flag(self):
        plan=render(BASE)
        self.assertEqual(plan['expected_argv'],BASE['effective_argv']+[
            '--queue-status-config',PROJECTION,'--queue-status-stage','test','--browser-owner-api'])
        self.assertEqual(plan['rollback_argv'],BASE['effective_argv'])
        self.assertEqual(plan['sha256'],digest(plan['dropin']))
        self.assertTrue(plan['basename'].endswith('.conf'))
        self.assertIn(PASSWORD.encode(),plan['dropin'])
        self.assertNotIn(b'--browser-owner-clear',plan['dropin'])
    def test_exact_existing_pair_preserved_byte_for_byte(self):
        value=deepcopy(BASE); value['effective_argv'] += ['--queue-status-config',PROJECTION,
                                                        '--queue-status-stage','test']
        self.assertEqual(render(value)['expected_argv'],value['effective_argv']+['--browser-owner-api'])
    def test_drift_production_other_owner_wrong_port_wrong_archive_and_missing_smoke_rejected(self):
        for key,value in [('effective_uid',0),('uid',2),('listener','0.0.0.0:8891'),
                          ('current',ROOT+'/current'),('cwd','/tmp'),('active_pid',0),
                          ('smoke_helper_sha256','c'*64),('installed_dropins',{}),('baseline_graph_smoke',False),
                          ('dashboard_pair_current',False),('drop_in_absent',False),
                          ('clear_dark',False),('legacy_graph_authority_absent',False)]:
            with self.subTest(key=key),self.assertRaises(ValueError):render(BASE|{key:value})
        with self.assertRaises(ValueError):render(BASE,'f'*64)
    def test_old_bridge_clear_duplicate_queue_role_or_config_escape_denied(self):
        for flags in (['--bridge-read-token-file','/tmp/token'],['--browser-owner-clear'],
                      ['--queue-status-stage','prod'],['--queue-status-config',PROJECTION],
                      ['--port','8901'],['--host','0.0.0.0'],
                      ['--embedding-model','m','--embedding-cache',ROOT+'/state/../other'],
                      ['--allowed-proxy-host','proxy.example']):
            with self.subTest(flags=flags),self.assertRaises(ValueError):
                render(BASE|{'effective_argv':BASE['effective_argv']+flags})

if __name__=='__main__':
    unittest.main()
