import hashlib
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from ops_readonly_preflight import classify_predecessor,trusted_pid,BASE
from transition_witness import Release

class Preflight(TestCase):
    def test_distinguishes_capability_from_running_legacy_mode(self):
        legacy=b'1  ./base.py\n'
        capable=legacy+b'2  ./BASE_BROWSER_LISTENER_CONTRACT\n'
        def prior(raw):return Release('a'*40,'b'*64,hashlib.sha256(raw).hexdigest(),'legacy')
        self.assertTrue(classify_predecessor(legacy,prior(legacy)))
        for manifest,pin in [(capable,prior(capable)),(legacy,prior(capable))]:
            with self.assertRaises(ValueError):classify_predecessor(manifest,pin)
    def test_query_only_native_fixed_unit_no_controllable_arguments(self):
        with patch('pathlib.Path.resolve',lambda path,strict:path), patch('pathlib.Path.lstat',return_value=SimpleNamespace(st_uid=986,st_mode=0o140660)), patch('ops_readonly_preflight.subprocess.run',return_value=SimpleNamespace(stdout=b'MainPID=42\n')) as call:
            self.assertEqual(trusted_pid(BASE),42)
            with self.assertRaises(ValueError):trusted_pid('foreign.service')
        self.assertEqual(call.call_count,1)
        self.assertEqual(call.call_args.args[0][-5:],['/usr/bin/systemctl','--user','show','--property=MainPID',BASE])
