"""Compile a checked canonical artifact catalog; does not contact VM or start a service."""
import argparse
import json
import os
from pathlib import Path
import re
import stat
from policy import compile_catalog

def read(path):
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW),'rb') as f:
        meta=os.fstat(f.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_mode&0o022:raise ValueError('unsafe manifest')
        raw=f.read(100001)
    if len(raw)>100000:raise ValueError('manifest too large')
    def unique(pairs):
        result={}
        for name,value in pairs:
            if name in result:raise ValueError('duplicate manifest field')
            result[name]=value
        return result
    return json.loads(raw,object_pairs_hook=unique)

def compile_release(doc, pins, *, effects):
    if (doc.get('version')!=2 or doc.get('schema_sha256')!=pins['schema_sha256']
            or pins['registry']!='base-typed-operations-v1' or set(doc.get('descriptions',{}))!=set(doc.get('tools',{}))):
        raise ValueError('missing approved manifest identity')
    return compile_catalog(doc,pins,require_functional=True,include_effects=effects)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--schema-file',type=Path,required=True)
    p.add_argument('--revision',required=True)
    p.add_argument('--module-sha256',required=True)
    p.add_argument('--schema-sha256',required=True)
    p.add_argument('--mode',choices=['read','full'],required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    for value,n in ((a.revision,40),(a.module_sha256,64),(a.schema_sha256,64)):
        if not re.fullmatch('[0-9a-f]{'+str(n)+'}',value):raise ValueError('invalid approved pin')
    pins=dict(revision=a.revision,module_sha256=a.module_sha256,schema_sha256=a.schema_sha256,registry='base-typed-operations-v1')
    result=compile_release(read(a.schema_file),pins,effects=a.mode=='full')
    with os.fdopen(os.open(a.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'wb') as out:
        out.write(json.dumps(result,sort_keys=True,ensure_ascii=True,separators=(',',':')).encode()+b'\n')
    print(result['catalog_sha256'])
if __name__=='__main__':main()
