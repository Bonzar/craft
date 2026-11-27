import types,sys,json,hashlib
from pathlib import Path
here=Path(__file__).resolve().parent
def load(s):
 m=types.ModuleType('receipt')
 exec(compile(s,'receipt.py','exec'),m.__dict__)
 return m
m=load((here/'source-evidence-receipt.py').read_text())
fx=(here/'source-evidence-fixtures.json').read_bytes()
assert hashlib.sha256(fx).hexdigest()==m.FIXTURE_CATALOG_SHA
sys.path.insert(0,str(here.parents[1]))
from prototypes.assertion_memory.model import ModelRequest,format_prompt
for case in m.EXPECTED:
 d=next(r for r in json.loads(fx) if r['id']==case)
 shared={'fragment_texts':[r['text'] for r in d['rows']]}
 records=[{'source':f'source_{i+1}','records':[{'role':'source','origin':{'role':r['role']},
       'fragments':[{'option':0,'text_index':i}]}]} for i,r in enumerate(d['rows'])]
 out={r['source']:{'thoughts':[{'text':'FIXTURE_DRAFT_SENTINEL','support_fragments':[0]}],
                   'needs_detail':None} for r in records}
 def packet(task,rows,schema=None):
  return format_prompt(ModelRequest(task,[shared,*rows],'synthetic test',response_schema=schema)).encode()
 cq=packet('Construct thoughts for multiple source messages',records)
 ca=json.dumps(out).encode()
 gq=packet('Verify source thought batch',[{**r,'proposed_thoughts':out[r['source']]} for r in records],{'type':'object'})
 ga=json.dumps({'verdict':'reject','text':'FIXTURE_REASON_SENTINEL','discard_drafts':[]}).encode()
 names=('Construct thoughts for multiple source messages','Verify source thought batch')
 def joined(ca):
  return [(names[0],'succeeded',m.sha(cq),m.sha(ca),cq,ca),
          (names[1],'succeeded',m.sha(gq),m.sha(ga),gq,ga)]
 x=m.validate_worker_receipt(case,fx,joined(ca),ModelRequest,format_prompt)
 assert x['exact_replay_validated'] and 'SENTINEL' not in json.dumps(x)
 try:m.validate_worker_receipt(case,fx,joined(ca.replace(b'SENTINEL',b'SUBSTITUTED')),ModelRequest,format_prompt)
 except ValueError:pass
 else:raise AssertionError('altered generated drafts reached wrong gate')
 try:m.validate_worker_receipt(case,fx+b' ',joined(ca),ModelRequest,format_prompt)
 except ValueError:pass
 else:raise AssertionError('same contents but wrong fixture bytes accepted')
 saved=[]
 with m.ephemeral_receipt((cq,ca,gq,ga)) as fp:
  saved.append(fp); assert not fp.closed
 assert saved.pop().closed
 try:
  with m.ephemeral_receipt((cq,ca,gq,ga)) as fp:
   saved.append(fp)
   raise RuntimeError('forced error while receipt open')
 except RuntimeError:pass
 assert saved.pop().closed
print('two allowlisted stage links, rehashed mismatch rejected, fixture pin, private buffer closed normal/error; zero model')
