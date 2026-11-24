import hashlib
import stat
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import prod_service_hook_fenced_codex_route as hook

class PinnedDropIn(unittest.TestCase):
    content=b'[Service]\nEnvironment=SAFE_FIXTURE=yes\n'
    pin=hashlib.sha256(content).hexdigest().encode()+b'\n'
    def check(self, raw, *, unit=None, pin=pin, content=content, directory_mode=0o755):
        def read(path, **options):
            self.assertEqual(options['owner'],0)
            if path==hook.MCP_DROPIN_PIN:
                self.assertTrue(options['private'])
                if pin is None: raise FileNotFoundError()
                if isinstance(pin,Exception): raise pin
                return pin
            self.assertEqual(path,hook.MCP_APPROVED_DROPIN)
            if isinstance(content,Exception): raise content
            return content
        with patch.object(hook,'checked_file',side_effect=read), patch.object(Path,'lstat',return_value=SimpleNamespace(st_uid=0,st_mode=stat.S_IFDIR|directory_mode)), patch.object(Path,'resolve',lambda path,strict=False:path):
            hook.trusted_unit_dropins(unit or hook.UNITS[0],raw)
    def test_exact_and_default(self):
        self.check(str(hook.MCP_APPROVED_DROPIN))
        self.check('',pin=None)
        self.check('',unit=hook.UNITS[1])
    def test_extra_missing_changed_or_unsafe_refused(self):
        exact=str(hook.MCP_APPROVED_DROPIN)
        for raw,opts in [(exact+' /etc/extra.conf',{}), ('',{}), ('/tmp/other.conf',{}),
                         (exact,{'pin':None}), (exact,{'pin':b'bad'}), (exact,{'pin':ValueError()}),
                         (exact,{'content':b'replaced'}), (exact,{'content':ValueError()}),
                         (exact,{'directory_mode':0o777}), (exact,{'unit':hook.UNITS[1]})]:
            with self.subTest(raw=raw,opts=opts),self.assertRaises(hook.UntrustedUnitDropIn):
                self.check(raw,**opts)
    def test_smoke_propagates_dropin_failure_without_retry(self):
        with patch.object(hook,'startup_deadline',return_value=30), patch.object(hook,'require_legacy_quiesced'), patch.object(hook,'check_live',side_effect=hook.UntrustedUnitDropIn('fixed')) as check:
            with self.assertRaises(hook.UntrustedUnitDropIn):
                hook.wait_until_listening(None,None,1,clock=lambda:0,sleep=lambda _:self.fail('should not retry'))
            check.assert_called_once()
if __name__=='__main__':unittest.main()
