"""Use only the currently authorized TEST cloud operator; no IAM changes."""
from argparse import ArgumentParser
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from render_test import apigw,PUBLIC_PATH

PIN=re.compile(r'[a-f0-9]{64}\Z')
GATEWAY=re.compile(r'[a-z0-9]{20}\Z')
DOMAIN=re.compile(r'[a-z0-9]+(?:\.[a-z0-9]+)?\.apigw\.yandexcloud\.net\Z')
MAX=1024*1024

def fingerprint(spec):
    return sha256(json.dumps(spec,sort_keys=True,ensure_ascii=True,separators=(',',':'),allow_nan=False)
                  .encode('ascii')).hexdigest()

def compute(spec,reverse):
    if not reverse:
        return apigw(spec)
    original=deepcopy(spec)
    if PUBLIC_PATH not in original.get('paths',{}):
        raise ValueError('no installed TEST SSE route')
    original['paths'].pop(PUBLIC_PATH)
    if apigw(original)!=spec:
        raise ValueError('existing route differs from exact TEST insertion')
    return original

def require_exact(before,wanted,prior_pin,next_pin,read,write):
    if (not PIN.fullmatch(prior_pin) or not PIN.fullmatch(next_pin)
        or fingerprint(before)!=prior_pin or fingerprint(wanted)!=next_pin or read()!=before
        or before==wanted):
        raise ValueError('reviewed Gateway spec drifted')
    # Vendor update has no conditional revision. An owner must serialize all
    # config edits. If post-verification fails never restore a full stale spec.
    write(wanted)
    if read()!=wanted:
        raise ValueError('Gateway readback differs; stop and reconcile')

def decode(raw):
    import yaml
    class Loader(yaml.SafeLoader):
        def construct_mapping(self,node,deep=False):
            seen=set()
            for key,_ in node.value:
                name=self.construct_object(key,deep=deep)
                if type(name) is not str or name in seen:
                    raise ValueError('noncanonical or duplicate YAML mapping key')
                seen.add(name)
            return super().construct_mapping(node,deep=deep)
    if len(raw)>MAX:
        raise ValueError('overlarge gateway spec')
    spec=yaml.load(raw,Loader=Loader)
    if type(spec) is not dict:
        raise ValueError('expected full current OpenAPI spec')
    return spec

def main():
    import yaml
    p=ArgumentParser()
    p.add_argument('mode',choices=('plan','apply','plan-remove','remove'))
    p.add_argument('--test-gateway',required=True)
    p.add_argument('--test-domain',required=True)
    p.add_argument('--direct-test-bundle',action='store_true')
    p.add_argument('--before')
    p.add_argument('--after')
    a=p.parse_args()
    if not GATEWAY.fullmatch(a.test_gateway) or not DOMAIN.fullmatch(a.test_domain):
        raise ValueError('malformed fixed TEST gateway/operator binding')
    executable=shutil.which('yc')
    if not executable:
        raise ValueError('existing Cloud Shell CLI unavailable')
    def run(verb,*args):
        return subprocess.run([executable,'serverless','api-gateway',verb,'--id',a.test_gateway,*args],
            check=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL,
            timeout=65).stdout
    def read():
        meta=json.loads(run('get','--format','json'))
        if meta.get('id')!=a.test_gateway or meta.get('domain')!=a.test_domain:
            raise ValueError('the authorized TEST gateway identity changed')
        return decode(run('get-spec'))
    def write(spec):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'spec.yaml'
            path.write_text(yaml.safe_dump(spec,sort_keys=False,allow_unicode=True))
            run('update','--spec',str(path))
    before=read()
    if a.direct_test_bundle:
        from direct_test_bundle import render_gateway
        wanted=render_gateway(before,a.mode.endswith('remove'))
    else:
        wanted=compute(before,a.mode.endswith('remove'))
    if a.mode.startswith('plan'):
        if a.before or a.after:
            raise ValueError('plan takes no proof arguments')
        print('test_gateway_before_sha256='+fingerprint(before))
        print('test_gateway_after_sha256='+fingerprint(wanted))
    else:
        if not a.before or not a.after:
            raise ValueError('both reviewed proof arguments are required')
        require_exact(before,wanted,a.before,a.after,read,write)
        print('test_gateway_spec_readback_exact')

if __name__=='__main__':
    main()
