"""Stage-only Yandex API Gateway spec compiler; no network or cloud writes."""
import argparse, copy, hashlib, json, os, re, stat, sys
from pathlib import Path
from urllib.parse import urlsplit
import yaml

OLD='/memory-mcp-test-tools'
NEW='/memory-mcp-test-effects'
INTEGRATION='x-yc-apigateway-integration'
AUTH='X-Memory-MCP-Test-Effects-Auth'
MAX=256000
class StrictLoader(yaml.SafeLoader):
    pass
def unique(loader,node):
    if not isinstance(node,yaml.MappingNode):raise ValueError('unexpected mapping')
    out={};seen=set()
    for kn,vn in node.value:
        key=loader.construct_object(kn)
        if type(key) is not str and not (type(key) is int and 100<=key<=599):
            raise ValueError('unsupported key')
        if str(key) in seen or key=='<<':raise ValueError('duplicate/unsupported key')
        seen.add(str(key))
        out[key]=loader.construct_object(vn)
    return out
StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,unique)
def parse(data):
    if type(data) is not bytes or len(data)>MAX:raise ValueError('spec size')
    value=data.decode('utf-8')
    if any(isinstance(t,(yaml.AliasToken,yaml.AnchorToken,yaml.TagToken)) for t in yaml.scan(value)):
        raise ValueError('YAML aliases or explicit tags are not approved')
    out=yaml.load(value,Loader=StrictLoader)
    if type(out) is not dict or type(out.get('paths')) is not dict:raise ValueError('missing paths')
    return out
def candidate(data,origin):
    old=parse(data)
    if type(origin) is not str or not re.fullmatch(r'https://[A-Za-z0-9.-]+',origin):
        raise ValueError('expected independently verified HTTPS origin')
    parsed=urlsplit(origin)
    if not parsed.hostname or parsed.username or parsed.password or parsed.port is not None:
        raise ValueError('invalid fixed origin')
    paths=old['paths']
    if len(paths)!=8 or NEW in paths or any(type(x) is not str or not re.fullmatch(r'/[A-Za-z0-9_/-]+',x) for x in paths):
        raise ValueError('expected exactly eight explicit baseline paths')
    if any(type(v) is not dict or 'x-yc-apigateway-any-method' in v for v in paths.values()):
        raise ValueError('baseline path contains unrestricted method')
    prior=paths.get(OLD,{}).get('post')
    previous=prior.get(INTEGRATION) if type(prior) is dict else None
    if type(previous) is not dict or previous.get('type')!='http' or previous.get('method','POST')!='POST' or \
       previous.get('url')!=origin+OLD or previous.get('headers')!={
         'Content-Type':'{Content-Type}','X-Memory-MCP-Test-Read-Auth':'{X-Memory-MCP-Test-Read-Auth}'}:
        raise ValueError('baseline proof origin drift')
    def declared(k):
        return {'name':k,'in':'header','required':False,'schema':{'type':'string'}}
    out=copy.deepcopy(old)
    out['paths'][NEW]={'post':{
        'parameters':[declared('Content-Type'),declared(AUTH)],
        INTEGRATION:{'type':'http','url':origin+NEW,'method':'POST',
          'headers':{'Content-Type':'{Content-Type}',AUTH:'{'+AUTH+'}'},
          'omitEmptyHeaders':True,'timeouts':{'connect':2,'read':6}},
        'responses':{'200':{'description':'Scoped TEST intake and processing'},'401':{'description':'Denied'},'default':{'description':'Unavailable'}}
    }}
    rendered=yaml.safe_dump(out,sort_keys=False,allow_unicode=True).encode()
    checked=parse(rendered)
    proposed=checked['paths'].pop(NEW,None)
    if checked!=old or proposed!=out['paths'][NEW]:
        raise ValueError('candidate not an exact semantic one-path addition')
    return rendered

def stage_file(path,data):
    with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'wb') as f:
        f.write(data);f.flush();os.fsync(f.fileno())
def read_staged(path):
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW),'rb') as f:
        s=os.fstat(f.fileno())
        if not stat.S_ISREG(s.st_mode) or s.st_mode&0o022 or s.st_size>MAX:raise ValueError('unsafe staged baseline')
        data=f.read(MAX+1)
        if len(data)>MAX:raise ValueError('oversized baseline')
        return data
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--expected-baseline-sha256',required=True)
    p.add_argument('--expected-origin',required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    data=read_staged(a.baseline)
    if not re.fullmatch('[a-f0-9]{64}',a.expected_baseline_sha256) or hashlib.sha256(data).hexdigest()!=a.expected_baseline_sha256:
        raise ValueError('unreviewed Gateway baseline')
    out=candidate(data,a.expected_origin)
    stage_file(a.output,out)
    print(json.dumps({'baseline_sha256':hashlib.sha256(data).hexdigest(),
       'candidate_sha256':hashlib.sha256(out).hexdigest(),'added_paths':1,'removed_paths':0}))
if __name__=='__main__':
    try: main()
    except (OSError,ValueError,yaml.YAMLError):
        print('Gateway staging refused; inspect the private input separately.',file=sys.stderr)
        raise SystemExit(2) from None
