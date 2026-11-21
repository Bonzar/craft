import { test } from "node:test";
import assert from "node:assert/strict";
import worker from "../worker.mjs";

const origin="https://memory.example";
const env={MEMORY_OWNER_ID:"site-owner"};
const request=(path,options)=>new Request(origin+path,options);
const asOwner={"oai-authenticated-user-id":"site-owner"};
const rpc=(method,params)=>({jsonrpc:"2.0",id:1,method,params});
const post=(value,headers={})=>({method:"POST",headers,body:JSON.stringify(value)});

test("Site worker never serves owner data to anonymous or other Site users",async()=>{
  for (const headers of [{},{"oai-authenticated-user-id":"other"}]) {
    assert.equal((await worker.fetch(request("/",{headers}),env)).status,403);
    assert.equal((await worker.fetch(request("/api/graph",{headers}),env)).status,403);
    const read=request("/mcp",post(rpc("tools/call",{name:"memory_graph_snapshot"}),headers));
    assert.equal((await worker.fetch(read,env)).status,403);
  }
  assert.equal((await worker.fetch(request("/",{headers:asOwner}),{})).status,403);
});

test("MCP discovery exposes metadata only; write calls not part of tool registry",async()=>{
  const discovery=await (await worker.fetch(request("/mcp",post(rpc("tools/list"))),env)).json();
  assert.deepEqual(discovery.result.tools.map(t=>t.name),
    ["memory_graph_snapshot","memory_graph_node","memory_saved_overviews"]);
  const write=await worker.fetch(request("/mcp",post(rpc("tools/call",{name:"memory_import"}),asOwner)),env);
  assert.equal((await write.json()).error.code,-32602);
});

test("Import requires owner and browser same origin",async()=>{
  for (const headers of [
    asOwner,
    {...asOwner,Origin:"https://other.example","Content-Type":"application/json"},
    {...asOwner,Origin:origin},
  ]) {
    const result=await worker.fetch(request("/api/import",post({},headers)),{...env,DB:{}});
    assert.equal(result.status,403);
  }
});
