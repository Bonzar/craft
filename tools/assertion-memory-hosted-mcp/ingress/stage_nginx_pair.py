"""Prepare nginx buffers and optional private staging copies; never alter nginx or its guard."""
import argparse,hashlib,json,os,re,stat
from pathlib import Path
PORTS=((b'http://127.0.0.1:8803',b'http://127.0.0.1:8903'),
       (b'http://127.0.0.1:8818',b'http://127.0.0.1:8918'))
OLD_TEST=b'/memory-mcp-test'
NEW_TEST=b'/memory-mcp-test-tools'
ANCHOR=re.compile(rb'(?m)^([ \t]+)location[ \t]+=[ \t]+/memory-mcp-test[ \t]*\{[ \t]*$')
BLOCK=(
 b'# BEGIN isolated hosted TEST memory tool reads',
 b'location = /memory-mcp-test-tools {',
 b'    client_max_body_size 100k;',
 b'    proxy_request_buffering on;',
 b'    proxy_pass http://127.0.0.1:8832;',
 b'    proxy_set_header Authorization $http_x_memory_mcp_test_read_auth;',
 b'    proxy_set_header X-Memory-MCP-Test-Read-Auth "";',
 b'    proxy_set_header Cookie "";',
 b'    proxy_cache off;',
 b'}',
 b'# END isolated hosted TEST memory tool reads',
 b'',
)
def sha(data):return hashlib.sha256(data).hexdigest()
def old_to_new(b):
 for old,new in PORTS:
  if b.count(old)!=1 or new in b:raise ValueError('non-unique approved PROD upstream')
  b=b.replace(old,new)
 return b
def insert_location(data):
 if not isinstance(data,bytes) or len(data)>=128000 or b'\r' in data or NEW_TEST in data or b':8832' in data:
  raise ValueError('unexpected/existing origin')
 hits=list(ANCHOR.finditer(data))
 if len(hits)!=1:raise ValueError('expected one existing exact TEST fixture location')
 h=hits[0];space=h[1]
 fragment=b'\n'.join(space+line if line else b'' for line in BLOCK)+b'\n'
 return data[:h.start()]+fragment+data[h.start():],fragment
def plan(baseline,candidate):
 if old_to_new(baseline)!=candidate:raise ValueError('baseline/candidate not exact approved pair')
 b,br=insert_location(baseline);c,cr=insert_location(candidate)
 if br!=cr or old_to_new(b)!=c:raise ValueError('same-byte addition or rollback relationship failed')
 if b.replace(br,b'',1)!=baseline or c.replace(cr,b'',1)!=candidate:raise ValueError('non-additive change')
 if max(len(b),len(c))>=128000:raise ValueError('guard size threshold exceeded')
 return b,c,br

def read_staged(path):
 with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW),'rb') as f:
  st=os.fstat(f.fileno())
  if not stat.S_ISREG(st.st_mode) or st.st_mode&0o022 or st.st_size>=128000:raise ValueError('unsafe staged copy')
  raw=f.read(128000)
  if len(raw)>=128000:raise ValueError('large staged copy')
  return raw
def stage_file(path,data):
 with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'wb') as f:
  f.write(data);f.flush();os.fsync(f.fileno())
def main():
 p=argparse.ArgumentParser(description=__doc__)
 for arg in ('baseline','approved-candidate','new-stage-dir'):
  p.add_argument('--'+arg,type=Path,required=True)
 a=p.parse_args()
 b,c=read_staged(a.baseline),read_staged(a.approved_candidate)
 updated_b,updated_c,fragment=plan(b,c)
 os.mkdir(a.new_stage_dir,0o700)
 files={'baseline.original':b,'approved.original':c,'baseline.proposed':updated_b,'approved.proposed':updated_c,'test.fragment':fragment}
 for name,data in files.items():stage_file(a.new_stage_dir/name,data)
 print(json.dumps({'sha256':{k:sha(v) for k,v in files.items()},'test_paths_added_per_file':1,'prod_substitutions':2},sort_keys=True))
if __name__=='__main__':main()
