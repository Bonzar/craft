import { getGraph, getNode, importSnapshot, listOverviews, SnapshotError } from "./store.mjs";

const json = (data, status=200) => Response.json(data,{status,headers:{"cache-control":"no-store","x-content-type-options":"nosniff"}});
const MAX_IMPORT_BYTES=768 * 1024;
const tools = [
 {name:"memory_graph_snapshot",description:"Read a bounded view of the imported graph snapshot. Does not run the memory engine.",inputSchema:{type:"object",properties:{limit:{type:"integer",minimum:1,maximum:200}},additionalProperties:false}},
 {name:"memory_graph_node",description:"Read an imported node by its exact graph ID.",inputSchema:{type:"object",properties:{id:{type:"string",maxLength:512}},required:["id"],additionalProperties:false}},
 {name:"memory_saved_overviews",description:"Read imported overview versions. Does not create or update overviews.",inputSchema:{type:"object",properties:{limit:{type:"integer",minimum:1,maximum:100}},additionalProperties:false}},
];
function owner(req,env) {
  // Only trust this header behind the Sites authenticated dispatcher.
  // A service bypass token does not provide a visitor identity.
  return typeof env.MEMORY_OWNER_ID === "string" && env.MEMORY_OWNER_ID.length>0 &&
    req.headers.get("oai-authenticated-user-id") === env.MEMORY_OWNER_ID;
}
function safeLimit(value, maximum=200) {
  if (value === undefined) return Math.min(120,maximum);
  if (!Number.isSafeInteger(value) || value<1 || value>maximum) throw new RangeError("limit");
  return value;
}
async function boundedJson(req) {
  const reader=req.body?.getReader();
  if (!reader) throw new SyntaxError("Missing body");
  const decoder=new TextDecoder("utf-8",{fatal:true});
  let count=0, buffer="";
  try {
    while (true) {
      const {value,done}=await reader.read();
      if (done) break;
      count+=value.byteLength;
      if (count>MAX_IMPORT_BYTES) { await reader.cancel(); throw new RangeError("Body exceeds maximum"); }
      buffer+=decoder.decode(value,{stream:true});
    }
    return JSON.parse(buffer+decoder.decode());
  } finally { reader.releaseLock(); }
}
async function mcp(req,env) {
  if (req.method!=="POST") return json({error:"Use POST"},405);
  let request;
  try { request=await boundedJson(req); } catch { return json({jsonrpc:"2.0",id:null,error:{code:-32700,message:"Invalid JSON"}}); }
  const id=request?.id;
  const validId=id===undefined||id===null||typeof id==="string"||(typeof id==="number"&&Number.isFinite(id));
  const reply=(result)=>json({jsonrpc:"2.0",id:id??null,result});
  const error=(code,message)=>json({jsonrpc:"2.0",id:validId?(id??null):null,error:{code,message}});
  if (!validId||request?.jsonrpc!=="2.0"||typeof request.method!=="string") return error(-32600,"Invalid request");
  if (request.method==="notifications/initialized"&&id===undefined) return new Response(null,{status:202});
  if (request.method==="server/discover") return reply({supportedVersions:["2026-07-28"],capabilities:{tools:{}}});
  if (request.method==="initialize") return reply({protocolVersion:"2026-07-28",serverInfo:{name:"craft-memory-sites",version:"0.1.0"},capabilities:{tools:{}}});
  if (request.method==="tools/list") return reply({tools});
  if (request.method!=="tools/call") return error(-32601,"Method not found");
  if (!owner(req,env)) return json({error:"Owner authorization required"},403);
  const args=request.params?.arguments??{};
  const name=request.params?.name;
  if (!args || typeof args!=="object"||Array.isArray(args)) return error(-32602,"Invalid arguments");
  let value;
  try {
    switch (name) {
      case "memory_graph_snapshot":
      case "memory_saved_overviews": {
        if (Object.keys(args).some(k=>k!=="limit")) return error(-32602,"Unexpected arguments");
        let limit;
        try {limit=safeLimit(args.limit,name==="memory_saved_overviews"?100:200);} catch {return error(-32602,"Invalid limit");}
        value=name==="memory_graph_snapshot"?await getGraph(env.DB,{limit}):await listOverviews(env.DB,{limit});
        break;
      }
      case "memory_graph_node":
        if (typeof args.id!=="string"||!args.id||args.id.length>512||Object.keys(args).length!==1) return error(-32602,"Invalid node ID");
        value=await getNode(env.DB,args.id);
        break;
      default:return error(-32602,"Unknown tool");
    }
  } catch { return reply({isError:true,content:[{type:"text",text:"Snapshot storage unavailable"}]}); }
  return reply({content:[{type:"text",text:JSON.stringify(value??null)}]});
}
export default {
  async fetch(req,env) {
    const url=new URL(req.url);
    if (url.pathname==="/mcp") return mcp(req,env);
    if (!owner(req,env)) return json({error:"Owner authorization required"},403);
    if (!env.DB) return json({error:"Missing D1 binding"},503);
    if (url.pathname==="/api/graph"&&req.method==="GET") {
      try { return json(await getGraph(env.DB,{limit:120})); } catch {return json({error:"Snapshot storage unavailable"},503);}
    }
    if (url.pathname==="/api/overviews"&&req.method==="GET") {
      try { return json(await listOverviews(env.DB,{limit:40})); } catch {return json({error:"Snapshot storage unavailable"},503);}
    }
    if (url.pathname==="/api/import"&&req.method==="POST") {
      if (req.headers.get("origin")!==url.origin || !req.headers.get("content-type")?.toLowerCase().startsWith("application/json"))
        return json({error:"Same-origin JSON is required"},403);
      let body;
      try { body=await boundedJson(req); } catch { return json({error:"Invalid JSON or file exceeds 768 KiB"},400); }
      try { return json(await importSnapshot(env.DB,body),201); }
      catch (e) {
        // Never reflect imported text, SQL or upstream errors into the UI.
        const retryable=!(e instanceof SnapshotError || e instanceof TypeError || e instanceof RangeError || e instanceof SyntaxError);
        return json({error:retryable?"Import not applied":"Snapshot rejected by validation"},retryable?503:400);
      }
    }
    return json({error:"Not found"},404);
  },
};
