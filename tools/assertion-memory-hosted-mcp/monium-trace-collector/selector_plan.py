"""Pure TEST Collector operator contract; no privileged backend or entrypoint."""
from dataclasses import dataclass
from hashlib import sha256

UNIT='assertion-monium-traces@test.service'
ASSET_PIN='bba5c6e6a4f7f1b61870bb50db0cc9d239ee66dcaa9d93a97255219c557a1b25'
CONFIG='/etc/assertion-monium/test-versioned/'+ASSET_PIN+'/common.yaml'
SELECTOR='/etc/systemd/system/'+UNIT+'.d/78-memory-trace-common.conf'
OVERLAY='/etc/assertion-monium/traces-test.yaml'
TARGET=('[Service]\nExecStart=\nExecStart=/usr/local/bin/otelcol-contrib --config='+CONFIG+' --config='+OVERLAY+'\n').encode('ascii')
FROZEN=('/etc/assertion-monium/traces-common.yaml',OVERLAY,'/etc/assertion-monium/traces-prod.yaml',
        '/etc/systemd/system/assertion-monium-traces@.service','@resolved-test-other-dropins','@resolved-prod-unit')
class Refuse(Exception): pass
class Restored(Exception): pass
@dataclass(frozen=True)
class Proof:
    lane:str
    asset_digest:str
    baseline:tuple
    old_selector:bytes|None
@dataclass(frozen=True)
class Inverse:
    proof:Proof
    new_selector:bytes=TARGET
def need(condition):
    if not condition: raise Refuse('unexpected lane, provenance or state')
def restore(io, runner, inverse, *, witness_only_baseline=False):
    p=inverse.proof
    need(io.frozen(FROZEN)==p.baseline and io.read(SELECTOR) in (p.old_selector,TARGET)
         and io.read(CONFIG) is not None and sha256(io.read(CONFIG)).hexdigest()==ASSET_PIN)
    if io.read(SELECTOR)==TARGET:
        io.compare_replace(SELECTOR,TARGET,p.old_selector)
    # A previous interrupted inverse may already have restored on-disk bytes,
    # but the runtime still needs verifying; restart remains exact TEST only.
    if not (witness_only_baseline and runner.verify(UNIT,p.old_selector)):
        runner.reload_and_restart(UNIT)
    need(runner.verify(UNIT,p.old_selector) and io.frozen(FROZEN)==p.baseline)
    io.retire_exact(inverse)
def run(io, runner, *, authorize=None, candidate=None, inverse=False):
    # No mode/authority from request data: only the existing trusted caller
    # injects an independently verified TEST operations grant. No default CLI.
    need(callable(authorize))
    p=authorize()
    need(type(p) is Proof and p.lane=='test' and p.asset_digest==ASSET_PIN
         and p.old_selector!=TARGET and (p.old_selector is None or type(p.old_selector) is bytes)
         and type(inverse) is bool)
    with io.lease():
        need(io.frozen(FROZEN)==p.baseline)
        if inverse:
            i=io.recover()
            need(i==Inverse(p))
            restore(io,runner,i,witness_only_baseline=True)
            return 'restored'
        need(type(candidate) is bytes and sha256(candidate).hexdigest()==ASSET_PIN)
        need(io.recover() is None and io.read(SELECTOR)==p.old_selector)
        io.stage_exact(CONFIG,candidate)
        need(io.read(CONFIG)==candidate and io.frozen(FROZEN)==p.baseline)
        i=Inverse(p)
        io.persist_inverse(i)
        need(io.recover()==i and io.frozen(FROZEN)==p.baseline)
        io.compare_replace(SELECTOR,p.old_selector,TARGET)
        try:
            runner.reload_and_restart(UNIT)
            need(runner.verify(UNIT,TARGET) and io.frozen(FROZEN)==p.baseline)
        except Exception as failure:
            restore(io,runner,i)
            raise Restored('TEST startup failed; predecessor restored') from failure
        return 'selected'
