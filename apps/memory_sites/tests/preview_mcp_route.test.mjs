import assert from 'node:assert/strict';
import test from 'node:test';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {readFileSync} from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import {admitRawCapture} from '../raw_capture.mjs';
import {createPreviewMcpRoute} from '../preview_mcp_route.mjs';

const endpoint='https://fixture.invalid/mcp';
const idName='oai-authenticated-user-id';
const ownerId='site-owner-fixture';
const signature={message_id:'fixture-1',position:0,scope:'synthetic-assertion-smoke',
  source:'fixture://assertion-smoke/v1',text:'The imaginary locker has a blue label.',actor_id:'user',role:'user'};
const common=(label)=>({...signature,session_id:'synthetic-assertion-smoke-v1-'+label});
const names=['memory_sessions','memory_session_messages'];
function testDb(t,{marker=true}={}){
  const raw=new DatabaseSync(':memory:');
  t.after(()=>raw.close());
  const load=(name,dir='drizzle')=>raw.exec(readFileSync(new URL('../'+dir+'/'+name+'.sql',import.meta.url),'utf8'));
  for(const name of ['0002_queue','0004_raw_capture'])load(name);
  if(marker)load('0001_preview_marker','smoke');
  const sql=[];
  const read=(text,args)=>{
    sql.push(text);
    return {success:true,results:raw.prepare(text).all(...args)};
  };
  const db={
    prepare(text){
      const bound=(...args)=>({text,args,all:async()=>read(text,args)});
      return {...bound(),bind:bound};
    },
    async batch(batch){
      raw.exec('BEGIN IMMEDIATE');
      try{
        const results=batch.map(stmt=>read(stmt.text,stmt.args));
        raw.exec('COMMIT');return results;
      }catch(err){raw.exec('ROLLBACK');throw err;}
    },
  };
  return {raw,db,sql};
}
const defaultRoute=createPreviewMcpRoute();
const call=(method,params,id=7)=>({jsonrpc:'2.0',id,method,params});
async function request(route,env,{value,body=JSON.stringify(value),httpMethod='POST',identity,headers={}}={}){
  return route(new Request(endpoint,{method:httpMethod,body:httpMethod==='POST'?body:undefined,
    headers:{'content-type':'application/json',...(identity?{[idName]:identity}:{}),...headers}}),env);
}
const data=rpc=>JSON.parse(rpc.result.content[0].text);

test('Sites discovery and exact canonical tool schemas, without DB or authentication for metadata only',async()=>{
  const discover=await (await request(defaultRoute,{},{value:call('server/discover')})).json();
  assert.deepEqual(discover,{jsonrpc:'2.0',id:7,result:{supportedVersions:['2026-07-28'],capabilities:{tools:{}}}});
  const listed=await (await request(defaultRoute,{},{value:call('tools/list')})).json();
  assert.deepEqual(listed.result.tools.map(t=>t.name),names);
  const tools=JSON.parse(execFileSync('python',['-m','apps.memory_sites.mcp_parity','--emit-original'],{
    cwd:fileURLToPath(new URL('../../../',import.meta.url)),encoding:'utf8',
    stdio:['ignore','pipe','ignore'],
  })).tools.filter(t=>names.includes(t.name));
  assert.deepEqual(Object.fromEntries(listed.result.tools.map(t=>[t.name,t.inputSchema])),
    Object.fromEntries(tools.map(t=>[t.name,t.inputSchema])));
  const init=await (await request(defaultRoute,{},{value:call('initialize')})).json();
  assert.equal(init.result.protocolVersion,'2026-07-28');
});

test('strict owner check; other, absent, cross-site or bypass-only principals make no reads',async t=>{
  const {db,sql}=testDb(t);
  const value=call('tools/call',{name:'memory_sessions'});
  for(const [env,details] of [
    [{DB:db,MEMORY_OWNER_ID:ownerId},{identity:'other'}],
    [{DB:db},{identity:ownerId}],
    [{DB:db,MEMORY_OWNER_ID:ownerId},{headers:{'oai-sites-authorization':'Bearer synthetic-bypass'}}],
    [{DB:db,MEMORY_OWNER_ID:ownerId},{identity:ownerId,headers:{'sec-fetch-site':'cross-site'}}],
  ]){
    const reply=await (await request(defaultRoute,env,{value,...details})).json();
    assert.equal(reply.result.isError,true);
    assert.match(reply.result.content[0].text,/Owner authorization required/);
  }
  const custom=createPreviewMcpRoute({isOwner:()=>{throw Error('do not leak');}});
  const failed=await (await request(custom,{DB:db},{value,identity:ownerId})).json();
  assert.match(failed.result.content[0].text,/Owner authorization required/);
  assert.deepEqual(sql,[]);
});

test('pinned read methods expose only exact admitted v1 fixture metadata, never other bodies or rows',async t=>{
  const {db,sql}=testDb(t);
  await admitRawCapture(db,{session_id:'foreign-private-session',message_id:'hidden',role:'user',
    actor_id:'external-person',text:'foreign-data-never-return',scope:'private',source:'fixture://private'}, {now:100});
  await admitRawCapture(db,{...common('third'),text:'same label but not a sanctioned fixture'}, {now:200});
  const accepted=await admitRawCapture(db,common('accepted'),{now:300});
  const rejected=await admitRawCapture(db,common('rejected'),{now:400});
  const start=sql.length;

  const action=async (name,args={})=>await (await request(defaultRoute,{DB:db,MEMORY_OWNER_ID:ownerId},{
    identity:ownerId,value:call('tools/call',{name,arguments:args}),
  })).json();
  const page=data(await action(names[0],{limit:1}));
  assert.deepEqual([page.total,page.snapshot_id,page.next_offset],[2,2,1]);
  assert.equal(page.sessions[0].session_id,common('accepted').session_id);
  const next=data(await action(names[0],{offset:page.next_offset,snapshot_id:page.snapshot_id,limit:1}));
  assert.equal(next.sessions[0].session_id,common('rejected').session_id);
  assert.equal(next.next_offset,null);
  const events=[];
  for(const [which,fixture] of [['accepted',accepted],['rejected',rejected]]){
    const payload=await action(names[1],{session_id:common(which).session_id,message_id:'fixture-1',snapshot_id:2});
    assert.doesNotMatch(JSON.stringify(payload),/foreign-data-never-return|same label but|imaginary locker|prompt/);
    const info=data(payload);
    assert.equal(info.total,1);
    assert.match(info.notice,/Message bodies are not exposed/);
    assert.equal(info.messages[0].event_id,fixture.event_id);
    events.push(info.messages[0].event_id);
  }
  assert.notEqual(...events);
  const foreign=data(await action(names[1],{session_id:'foreign-private-session'}));
  assert.deepEqual(foreign.messages,[]);
  assert.ok(sql.slice(start).every(s=>/^(?:SELECT|WITH) /.test(s)));
});

test('marker, JSON bounds and unsupported tools fail closed',async t=>{
  const {db,sql}=testDb(t,{marker:false});
  const env={DB:db,MEMORY_OWNER_ID:ownerId};
  const action=async options=>await (await request(defaultRoute,env,{identity:ownerId,...options})).json();
  assert.equal((await action({body:'{'})).error.code,-32700);
  assert.equal((await action({body:'A'.repeat(8193)})).error.code,-32700);
  assert.equal((await action({value:call('unsupported')})).error.code,-32601);
  assert.equal((await action({value:call('tools/call',{name:names[0],arguments:{limit:2000}})})).result.isError,true);
  assert.equal((await action({value:call('tools/call',{name:'memory_source'})})).result.isError,true);
  assert.deepEqual(sql,[]);
  const denied=await action({value:call('tools/call',{name:names[0]})});
  assert.match(denied.result.content[0].text,/Dedicated synthetic preview storage is unavailable/);
  assert.equal(sql.length,1);
  const notify=await request(defaultRoute,env,{value:{jsonrpc:'2.0',method:'notifications/initialized'}});
  assert.equal(notify.status,202);
  assert.equal((await request(defaultRoute,env,{httpMethod:'GET'})).status,405);
});
