import unittest
from nginx_test_apply import compute, digest, exact_change
from test_render_test import NGINX

class OperatorAction(unittest.TestCase):
    def test_permitted_insertion_and_safe_inverse(self):
        before=NGINX.encode()
        wanted=compute(before)
        self.assertEqual(compute(wanted,True),before)
        with self.assertRaisesRegex(ValueError,'differs'):
            compute(wanted.replace(b'proxy_read_timeout 15s',b'proxy_read_timeout 16s'),True)

    def test_stale_plan_never_writes(self):
        calls=[]
        with self.assertRaises(ValueError):
            exact_change(b'a',b'b',digest(b'a'),digest(b'b'),lambda:b'drift',calls.append,calls.append)
        self.assertEqual(calls,[])

    def test_nginx_failure_rolls_back_exact_before_bytes(self):
        current=[b'a']; events=[]
        def write(data):
            current[0]=data
        def verify(name):
            events.append((name,current[0]))
            if name=='validate' and current[0]==b'b':
                raise ValueError('bad candidate')
        with self.assertRaises(ValueError):
            exact_change(b'a',b'b',digest(b'a'),digest(b'b'),lambda:current[0],write,verify)
        self.assertEqual(current[0],b'a')
        self.assertEqual(events,[('validate',b'b'),('validate',b'a'),('reload',b'a')])
