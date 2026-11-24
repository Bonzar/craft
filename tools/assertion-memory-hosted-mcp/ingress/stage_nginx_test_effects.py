"""Stage one TEST effects Nginx location from reviewed current bytes only."""
import argparse, json, os, re
from pathlib import Path
from stage_nginx_pair import BLOCK, PORTS, read_staged, sha, stage_file
READ=b'/memory-mcp-test-tools'
EFFECT=b'/memory-mcp-test-effects'
LOC=re.compile(rb'(?m)^([ \t]+)# BEGIN isolated hosted TEST memory tool reads[ \t]*$')

def plan(raw, approved_sha):
    if not re.fullmatch('[a-f0-9]{64}',approved_sha) or sha(raw)!=approved_sha:
        raise ValueError('unreviewed settled Nginx baseline')
    if b'\r' in raw or EFFECT in raw or b'memory_mcp_test_effects_auth' in raw:
        raise ValueError('unexpected Nginx baseline')
    for old,new in PORTS:
        if old in raw or raw.count(new)!=1:raise ValueError('settled PROD layout drift')
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
