from contextlib import nullcontext
from hashlib import sha256
from unittest import TestCase
from unittest.mock import patch

import sealed_host_install_bundle as b
import dark_install_transaction as txn

class IO:
    def __init__(self,stock):
        self.stock=dict(stock);self.anchor=None;self.mount=None;self.fail_at=None
        self.count=0;self.backup=None;self.marker=True
    def lease(self):return nullcontext()
    def native_idle(self):return True
    def marker_absent(self):return self.marker
    def recovery(self):return self.anchor
    def originals(self,_):return self.backup
    def read_exact(self,p,m):return self.stock[p]
    def check_namespace(self,path,rows,allow_missing=False,require_absent=False):
        if self.mount is None and (allow_missing or require_absent):return
        if self.mount is None or require_absent or set(self.mount)!=set(rows):
            raise ValueError('foreign protected namespace')
        if any(txn._sha(raw)!=rows[k]['after'] for k,raw in self.mount.items()):
            raise ValueError('foreign module source')
    def stage_inverse(self,digest,stock):
        self.backup=stock;self.anchor=digest
    def publish_namespace(self,root,rows,value):
        assert self.anchor is not None
        self.mount=value
    def discard_namespace(self,root,rows):
        self.check_namespace(root,rows,allow_missing=True);self.mount=None
    def replace_exact(self,path,mode,prior,after):
        assert txn._sha(self.stock[path])==prior and self.anchor is not None
        self.count+=1
        if self.count==self.fail_at:raise OSError('simulated crash before rename')
        self.stock[path]=after
    def finish_inverse(self,_):self.anchor=None

class DarkTransaction(TestCase):
    def test_crash_intermediate_requires_exact_inverse_then_retried_release(self):
        inputs=b.STAGED|b.MOUNTED
        raw={k:(f'# new {k}'+chr(10)).encode() for _,k in inputs.values()}
        pins={k:sha256(v).hexdigest() for k,v in raw.items() if k not in b.OUTPUT_PINS}
        old={k:(f'# original {k}'+chr(10)).encode() for k in b.STAGED}
        with patch.dict(b.STOCK_PINS,{p:txn._sha(raw) for p,raw in old.items()}), patch.dict(b.OUTPUT_PINS,{k:txn._sha(raw[k]) for k in b.OUTPUT_PINS}):
            kwargs=dict(source_commit='1'*40, reviewed_pins=pins, images=raw)
            kwargs['manifest']=b.make_manifest(source_commit=kwargs['source_commit'],exact_source_digest_map=pins)
            fixture=IO(old);fixture.fail_at=3
            with self.assertRaises(OSError):txn.run(**kwargs,protected_io=fixture)
            self.assertNotEqual(fixture.stock,old)
            self.assertEqual(fixture.anchor,txn._sha(kwargs['manifest']))
            with self.assertRaises(ValueError):txn.run(**kwargs,protected_io=fixture)
            fixture.marker=False
            with self.assertRaises(ValueError):txn.run(**kwargs,protected_io=fixture,inverse=True)
            fixture.marker=True
            self.assertEqual(txn.run(**kwargs,protected_io=fixture,inverse=True),'stock-restored')
            self.assertEqual(fixture.stock,old)
            self.assertIsNone(fixture.mount)
            self.assertEqual(txn.run(**kwargs,protected_io=fixture),'source-dark-ready')
            self.assertEqual(txn.run(**kwargs,protected_io=fixture,inverse=True),'stock-restored')
