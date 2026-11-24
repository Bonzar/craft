from pathlib import Path
import base64,hashlib,sys

import asyncio,json
from mcp.server.fastmcp import FastMCP
from prototypes.assertion_memory import mcp_server as canonical
from vm_relay import make_app
assert hashlib.sha256(Path(canonical.__file__).read_bytes()).hexdigest()=='4eea4c6bf55be51fa972c54ee5fb0a259b5b5766acbd69676e1e6804566fa11c'
if len(sys.argv)==1:
 sys.argv.append(base64.b64encode(Path(__file__).with_name('catalog.v011.review.json').read_bytes()).decode())
CAT=json.loads(base64.b64decode(sys.argv[1]))
token={'test':'1'*64,'prod':'2'*64}
tag='assertion-memory-v0.1.1'
ref='842983c4-2df7-4fac-be9f-125a03aa42aa'
class Synthetic:
 def __init__(self,lane):self.lane=lane;self.calls=[]
 def status(self, *_a, **kw):
  self.calls.append(('status',kw))
  return {'fixture':'status-'+self.lane}
 def provenance(self, ids):
  self.calls.append(('provenance',ids))
  return {'fixture':'provenance-'+self.lane}
services={lane:Synthetic(lane) for lane in token}
def construct(lane):
 svc=services[lane]; server=FastMCP('same canonical registration')
 canonical._register_read_tools(server,svc,object(),None)
 def release():return tag,'b'*64
 def forward(id,name,args):
  res=asyncio.run(server.call_tool(name,args))
  if isinstance(res,tuple): content,structured=res
  else: content,structured=res,None
  return {'jsonrpc':'2.0','id':id,'result':{'content':[x.model_dump(exclude_none=True) for x in content], 'structuredContent':structured}}
 profile={'lane':lane,'base_release':tag,'catalog_sha256':CAT['catalog_sha256'],'enabled_capabilities':['baseline_read']}
 return make_app(profile,CAT,token[lane],release,forward)
apps={lane:construct(lane) for lane in token}
async def direct(lane,pkt,path=None,bearer=None):
 out=[]
 async def receive(): return {'type':'http.request','body':json.dumps(pkt).encode()}
 async def send(msg):out.append(msg)
 scope={'type':'http','method':'POST','path':path or '/memory-mcp-'+lane+'-tools','headers':[
 (b'authorization',('Bearer '+(bearer if bearer is not None else token[lane])).encode()),(b'content-type',b'application/json')]}
 await apps[lane](scope,receive,send)
 return out[0]['status'],json.loads(out[1]['body'])
async def local_tests():
 for lane,other in [('test','prod'),('prod','test')]:
  for tool in ('memory_status','memory_provenance'):
   p={'request_id':ref,'operation':tool,'arguments':{'record_ids':['fixture']} if tool.endswith('provenance') else {}}
   assert (await direct(lane,p,bearer=token[other]))[0]==401
   assert (await direct(lane,p,path='/memory-mcp-'+other+'-tools'))[0]==404
   assert (await direct(lane,dict(p,environment=other)))[0]==400
   status,res=await direct(lane,p)
   assert status==200 and res['environment']==lane and res['release']==tag and res['catalog_sha256']==CAT['catalog_sha256']
   assert '"'+tool.split('_')[1]+'-'+lane+'"' in json.dumps(res).replace('\\"','"')
  for n in ('memory_query','memory_ingest_report','memory_capture_message','memory_process','memory_store_artifact','memory_ingest_register'):
   assert (await direct(lane,{'request_id':ref,'operation':n,'arguments':{}}))[0]==400
  assert (await direct(lane,{'request_id':ref,'operation':'memory_status','arguments':{'section':'model_usage'}}))[0]==400
  assert len(services[lane].calls)==2
if len(sys.argv)==2:
 asyncio.run(local_tests())
 print('Both environment profiles use the same exact FastMCP handlers; 4 positive fixture reads and 20 negative cases passed')
else:
 x=json.loads(sys.argv[2])
 print(json.dumps(asyncio.run(direct(x['lane'],x['body'],x['path'],x['bearer']))))
