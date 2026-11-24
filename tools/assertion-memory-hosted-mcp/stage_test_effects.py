"""Exact published TEST adapter candidate only. Does not contact Sites or a VM."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from policy import compile_catalog

HERE=Path(__file__).resolve().parent
EXPECTED_ENTRY='1e86c7d90ebbf83368e8b5a5416b90efe11970fa0535942ad2c7d695310b5808'
OLD='0315d8a2e1ffe977d9c99bbf39f65aef5523adb929d9d3fe5260dca8e45bb151'
NEW='af97145d37be8e016403417521310c5fafa71bb2062beab0d674d263708101aa'

def catalog():
    old=json.loads((HERE/'catalog.v011.review.json').read_bytes())
    base=old['catalog']
    a={'revision':base['canonical_revision'],'schema_sha256':base['canonical_schema_hash'],
      'sdk':'1.30.0','module_sha256':'4eea4c6bf55be51fa972c54ee5fb0a259b5b5766acbd69676e1e6804566fa11c'}
    s={'revision':a['revision'],'module_sha256':a['module_sha256'],'runtime_mcp_sdk':a['sdk'],
       'tools':{name:tool['canonicalInputSchema'] for name,tool in base['tools'].items()}}
    new=compile_catalog(s,a,include_test_effects=True)
    if old['catalog_sha256']!=OLD or new['catalog_sha256']!=NEW:
        raise ValueError('unexpected catalog identity')
    return new

def patch(raw):
    if hashlib.sha256(raw).hexdigest()!=EXPECTED_ENTRY:
        raise ValueError('exact published TEST adapter required')
    src=raw.decode('utf-8')
    src,count=re.subn(r"^ gateway_url:'(https://[^']+)/memory-mcp-test-tools',$",
        lambda m:m.group(0)+"\n effect_gateway_url:'"+m.group(1)+"/memory-mcp-test-effects',",src,flags=re.M)
    if count!=1:raise ValueError('fixed TEST gateway origin unavailable')
    pairs={
      "catalog_sha256:'"+OLD+"'":"catalog_sha256:'"+NEW+"'",
      "enabled_capabilities:['baseline_read']":"enabled_capabilities:['baseline_read','intake_process']",
      "'MEMORY_VISUAL_PROD_TOKEN'].some":"'MEMORY_VISUAL_PROD_TOKEN','HOSTED_MEMORY_PROD_EFFECTS_TOKEN'].some",
      "env[k]!==undefined&&env[k]===env.HOSTED_MEMORY_TEST_READ_TOKEN)":
      "env[k]!==undefined&&(env[k]===env.HOSTED_MEMORY_TEST_READ_TOKEN||env[k]===env.HOSTED_MEMORY_TEST_EFFECTS_TOKEN))",
    }
    for before,after in pairs.items():
        if src.count(before)!=1:raise ValueError('exact TEST owner guard unavailable')
        src=src.replace(before,after)
    return src

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--published-entry',type=Path,required=True)
    p.add_argument('--new-stage-dir',type=Path,required=True)
    a=p.parse_args()
    files={'test-hosted-mcp-entry.mjs':patch(a.published_entry.read_bytes()),
           'shared-hosted-mcp-worker.mjs':(HERE/'worker.mjs').read_text(),
           'hosted-mcp-release-catalog.mjs':'export const CATALOG='+json.dumps(catalog(),ensure_ascii=True)+ ';\n'}
    a.new_stage_dir.mkdir(mode=0o700)
    for name,value in files.items():
        with (a.new_stage_dir/name).open('x',encoding='utf-8') as out:out.write(value)
    print(json.dumps({n:hashlib.sha256(s.encode()).hexdigest() for n,s in files.items()},sort_keys=True))

if __name__=='__main__':main()
