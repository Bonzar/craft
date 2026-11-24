from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase,main
from unittest.mock import Mock, patch
import prod_service_hook_fenced_cold_start as h

class Startup(TestCase):
    policy=SimpleNamespace(db_dev=10,db_inode=33)
    key='LISTEN 0 12 127.0.0.1:8903 0.0.0.0:* users:(("python",pid=92,fd=5))'

    def test_only_absent_port_is_transient(self):
        with self.assertRaises(h.StartupNotListening): h.check_listener('',8903,92)
        h.check_listener(self.key,8903,92)
        for value in (self.key.replace('pid=92','pid=91'),
                      self.key.replace('127.0.0.1','0.0.0.0'),
                      self.key+'\n'+self.key.replace('127.0.0.1','0.0.0.0')):
            with self.assertRaises(ValueError) as e: h.check_listener(value,8903,92)
            self.assertNotIsInstance(e.exception,h.StartupNotListening)

    def test_real_observed_515_second_readiness_with_budget(self):
        t=[0.0]
        checks=[]
        def live(*args):
            checks.append(t[0])
            if t[0]<5.15: raise h.StartupNotListening()
            return (9,10)
        with patch.object(h,'startup_deadline',return_value=30), \
             patch.object(h,'require_legacy_quiesced') as quiesced, \
             patch.object(h,'check_live',side_effect=live):
            self.assertEqual(h.wait_until_listening(self.policy,Path('release'),101,
                clock=lambda:t[0],sleep=lambda dt:t.__setitem__(0,t[0]+dt)),(9,10))
        self.assertEqual(checks,[0,1,2,3,4,5,6])
        self.assertEqual(quiesced.call_count,len(checks))

    def test_hard_failure_immediate_and_missing_port_expires(self):
        for err,expected in ((ValueError('hard invalid listener'),0),
                             (h.StartupNotListening('late'),2)):
            t=[0.0]
            with patch.object(h,'startup_deadline',return_value=2), \
                 patch.object(h,'require_legacy_quiesced'), \
                 patch.object(h,'check_live',side_effect=err),self.assertRaises(ValueError):
                h.wait_until_listening(self.policy,Path('release'),101,clock=lambda:t[0],
                                       sleep=lambda dt:t.__setitem__(0,t[0]+dt))
            self.assertEqual(t[0],expected)

    def test_startup_deadline_capped_by_first_permit_with_reserved_margin(self):
        with patch.object(h,'first_maintenance_complete',return_value=False), \
             patch.object(h,'checked_file',return_value=b'{"generation":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","expiry":151000000000,"inode":[10,33]}'):
            self.assertEqual(h.startup_deadline(self.policy,100),126)
            with self.assertRaisesRegex(ValueError,'Not enough guarded time'):
                h.startup_deadline(self.policy,126)
        with patch.object(h,'first_maintenance_complete',return_value=True):
            self.assertEqual(h.startup_deadline(self.policy,100),130)

if __name__=='__main__': main()
