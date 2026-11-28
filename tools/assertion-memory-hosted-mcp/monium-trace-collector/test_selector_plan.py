"""Offline fixtures; no subprocesses, files modified or network."""
from unittest import TestCase
from dataclasses import replace
t=TestCase()
from pathlib import Path
from contextlib import nullcontext
from hashlib import sha256
from selector_plan import (ASSET_PIN, CONFIG, FROZEN, SELECTOR, TARGET, UNIT, Proof, Refuse, Restored, run)
from package_validation import FOLDER,PINS,qualify
candidate=Path(__file__).with_name('common.yaml').read_bytes()
class Memory:
    def __init__(self,old=None):
        self.files={name:b'untouched' for name in FROZEN}
        self.files[SELECTOR]=old
        self.receipt=None
    def lease(self): return nullcontext()
    def frozen(self,paths): return tuple(sha256(self.files[p]).hexdigest() for p in paths)
    def read(self,p): return self.files.get(p)
    def stage_exact(self,p,b):
        assert p==CONFIG and self.files.get(p) in (None,b)
        self.files[p]=b
    def compare_replace(self,p,old,new):
        assert p==SELECTOR and self.read(p)==old and self.receipt is not None
        self.files[p]=new
    def recover(self): return self.receipt
    def persist_inverse(self,i): assert self.receipt is None; self.receipt=i
    def retire_exact(self,i): assert self.receipt==i; self.receipt=None
class FakeRunner:
    def __init__(self,fs,fail=False): self.fs=fs; self.fail=fail; self.calls=[]; self.active=fs.read(SELECTOR)
    def reload_and_restart(self,unit):
        assert unit==UNIT; self.calls.append(unit)
        if self.fail:
            self.fail=False
            raise OSError('synthetic startup fault')
        self.active=self.fs.read(SELECTOR)
    def verify(self,unit,wanted):
        assert unit==UNIT
        return self.active==wanted
def test_selector_fixture():
    for old in (None,b'exact prior TEST selector'):
        io=Memory(old); runner=FakeRunner(io)
        p=Proof('test',ASSET_PIN,io.frozen(FROZEN),old)
        t.assertEqual(run(io,runner,authorize=lambda:p,candidate=candidate),'selected')
        t.assertEqual(io.read(SELECTOR),TARGET)
        t.assertEqual(run(io,runner,authorize=lambda:p,inverse=True),'restored')
        t.assertEqual(io.read(SELECTOR),old)
        t.assertEqual(io.frozen(FROZEN),p.baseline)
        t.assertEqual(runner.calls,[UNIT,UNIT])
    io=Memory(b'predecessor'); runner=FakeRunner(io,True)
    p=Proof('test',ASSET_PIN,io.frozen(FROZEN),io.read(SELECTOR))
    t.assertRaises(Restored,run,io,runner,authorize=lambda:p,candidate=candidate)
    t.assertEqual((io.read(SELECTOR),io.recover(),runner.active),(b'predecessor',None,b'predecessor'))
    t.assertEqual(io.frozen(FROZEN),p.baseline)
    io=Memory(); runner=FakeRunner(io)
    p=Proof('test',ASSET_PIN,io.frozen(FROZEN),None)
    for auth, b in [(None,candidate),(lambda:Proof('prod',ASSET_PIN,p.baseline,None),candidate),(lambda:p,candidate+b'changed')]:
        t.assertRaises(Refuse,run,io,runner,authorize=auth,candidate=b)
    t.assertEqual(runner.calls,[])
    run(io,runner,authorize=lambda:p,candidate=candidate)
    io.files[FROZEN[-1]]=b'prod changed outside'
    t.assertRaises(Refuse,run,io,runner,authorize=lambda:p,inverse=True)
    t.assertEqual((runner.calls,io.read(SELECTOR)),([UNIT],TARGET))

def test_exact_package_fixture():
    assets={FOLDER/name: Path(__file__).with_name(name).read_bytes() for name in PINS}
    manifest={path:sha256(raw).hexdigest() for path,raw in assets.items()}
    revision='063a16ffa36fbe532a3de8e2c73f135fe4c14277'
    args=dict(lane='test',reviewed_revision=revision,actual_revision=revision,
              authorized_archive_sha='a'*64,verified_archive_sha='a'*64,manifest=manifest,assets=assets)
    t.assertEqual(qualify(**args),(FOLDER/'common.yaml',PINS['common.yaml']))
    for key,value in [('lane','prod'),('actual_revision','c'*40),('verified_archive_sha','b'*64),
                      ('manifest',manifest|{FOLDER/'common.yaml':'c'*64}),
                      ('assets',assets|{FOLDER/'prod.yaml':b'drift'}),
                      ('assets',assets|{FOLDER/'test-traces.env':b'not a credential'})]:
        t.assertRaises(ValueError,qualify,**(args|{key:value}))

if __name__=='__main__':
    test_exact_package_fixture()
    test_selector_fixture()
    print('TEST Collector package and selector fixtures passed')
