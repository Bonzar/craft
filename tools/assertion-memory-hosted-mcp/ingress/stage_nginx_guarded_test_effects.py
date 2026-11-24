"""Stage one TEST effects Nginx location from reviewed current bytes only."""
import argparse, json, os, re
from pathlib import Path
from stage_nginx_pair import BLOCK, read_staged, sha, stage_file
READ=b'/memory-mcp-test-tools'
EFFECT=b'/memory-mcp-test-effects'
LOC=re.compile(rb'(?m)^([ \t]+)# BEGIN isolated hosted TEST memory tool reads[ \t]*$')

ROUTES={b'/mcp':(b'8803',b'8903'),b'/memory-graph-read':(b'8818',b'8918')}

def prod_block(data,path):
    selector=rb'(?m)^(?P<indent>[ \t]*)location[ \t]+=[ \t]+'+re.escape(path)+rb'[ \t]*\{[ \t]*\n(?P<body>.*?)^(?P=indent)\}'
    matches=list(re.finditer(selector,data,re.S))
    if len(matches)!=1:raise ValueError('expected unique reviewed named PROD location')
    sel=matches[0]
    if b'{' in sel['body'] or b'}' in sel['body']:raise ValueError('nested PROD block needs independent review')
    return sel

def derive(data,variant):
    for path,(old,new) in ROUTES.items():
        block=prod_block(data,path)
        body=block['body']
        old_re=rb'(?m)^([ \t]*)proxy_pass[ \t]+http://127\.0\.0\.1:'+old+rb';[ \t]*$'
        if variant=='approved':
            swapped,n=re.subn(old_re,lambda m:m[0].replace(b':'+old+b';',b':'+new+b';'),body)
        elif variant=='frozen':
            swapped,n=re.subn(old_re,rb'\g<1>return 503;',body)
        else:raise ValueError('unrecognized variant')
        if n!=1:raise ValueError('expected one exact baseline upstream')
        data=data[:block.start('body')]+swapped+data[block.end('body'):]
    return data

def guard_relation(live,baseline,approved,frozen,review):
    expected={'live','baseline','approved','frozen','live_prod_routes'}
    if type(review) is not dict or set(review)!=expected:raise ValueError('independent review shape invalid')
    for k,v in dict(live=live,baseline=baseline,approved=approved,frozen=frozen).items():
        if not isinstance(review[k],str) or not re.fullmatch('[a-f0-9]{64}',review[k]) or sha(v)!=review[k]:
            raise ValueError('unreviewed ingress file bytes')
    if derive(baseline,'approved')!=approved or derive(baseline,'frozen')!=frozen:
        raise ValueError('guard witnesses disagree with exact transitions')
    modes=review['live_prod_routes']
    if not isinstance(modes,dict) or set(modes)!={p.decode('ascii') for p in ROUTES}:
        raise ValueError('provide two reviewed named route states')
    # Frozen is not a usable production state for an independent TEST change.
    for path in ROUTES:
        mode=modes[path.decode('ascii')]
        if mode not in ('baseline','approved'):
            raise ValueError('cannot stage during a frozen or unknown route state')
        if prod_block(live,path)[0]!=prod_block(baseline if mode=='baseline' else approved,path)[0]:
            raise ValueError('live named PROD route differs from reviewed guard state')

def plan(raw, baseline, approved, frozen, review):
    guard_relation(raw,baseline,approved,frozen,review)
    if b'\r' in raw or EFFECT in raw or b'memory_mcp_test_effects_auth' in raw:
        raise ValueError('unexpected Nginx baseline')
    hits=list(LOC.finditer(raw))
    if len(hits)!=1 or raw.count(READ)!=1:raise ValueError('existing TEST read scope not unique')
    at=hits[0].start(); indent=hits[0][1]
    old=b'\n'.join(indent+line if line else b'' for line in BLOCK)+b'\n'
    if raw[at:at+len(old)]!=old:raise ValueError('approved TEST read block differs')
    frag=(old.replace(b'memory-mcp-test-tools',b'memory-mcp-test-effects')
       .replace(b'isolated hosted TEST memory tool reads',b'isolated hosted TEST memory effects')
       .replace(b'http://127.0.0.1:8832;',b'http://127.0.0.1:8834;')
       .replace(b'memory_mcp_test_read_auth',b'memory_mcp_test_effects_auth')
       .replace(b'X-Memory-MCP-Test-Read-Auth',b'X-Memory-MCP-Test-Effects-Auth'))
    frag=frag.replace(indent+b'    client_max_body_size',indent+b'    if ($request_method != POST) { return 405; }\n'+indent+b'    client_max_body_size')
    out=raw[:at]+frag+raw[at:]
    if len(out)>=128000 or out.count(frag)!=1 or out.replace(frag,b'',1)!=raw:
        raise ValueError('not a single additive effects location')
    return out,frag

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--expected-baseline-sha256',required=True)
    p.add_argument('--new-stage-dir',type=Path,required=True)
    a=p.parse_args()
    prior=read_staged(a.baseline)
    out,frag=plan(prior,a.expected_baseline_sha256)
    os.mkdir(a.new_stage_dir,0o700)
    files={'baseline.original':prior,'baseline.proposed':out,'effects.fragment':frag}
    for name,data in files.items():stage_file(a.new_stage_dir/name,data)
    print(json.dumps({'sha256':{n:sha(d) for n,d in files.items()},'paths_added':1,'existing_paths_changed':0},sort_keys=True))
if __name__=='__main__':main()
