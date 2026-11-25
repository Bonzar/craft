"""One common hosted MCP ASGI relay. Bind an independently attested environment profile at boot."""
import asyncio, hashlib, hmac, json, re
from copy import deepcopy
from jsonschema import Draft202012Validator
from policy import EFFECTS, QUERY, authorized, digest
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

MAX_REQUEST, MAX_RESPONSE = 100_000, 131_072
MACHINE_MAX = 4 * 1024 * 1024
HEX = re.compile(r"[0-9a-f]{64}\Z")
RID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
TAG = re.compile(r"assertion-memory-v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?\Z")
ID = re.compile(r"[^\x00-\x1f]{1,160}\Z")

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None

def parse(data):
    def unique(pairs):
        out = {}
        for k, v in pairs:
            if k in out:
                raise ValueError("duplicate")
            out[k] = v
        return out
    def invalid_constant(_):
        raise ValueError("invalid")
    return json.loads(data.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid_constant)

def make_forward(profile, backend_token):
    port=profile.get('canonical_mcp_port')
    if type(port) is not int or not 1024<=port<=65535: raise ValueError('invalid fixed port')
    upstream=f'http://127.0.0.1:{port}/mcp'
    if not isinstance(backend_token, str) or not backend_token.isascii() or not \
        32 <= len(backend_token) <= 4096 or any(x.isspace() for x in backend_token):
        raise ValueError("invalid backend credential")
    opener = build_opener(ProxyHandler({}), NoRedirect())
    query_timeout=profile.get('query_timeout_seconds')
    if 'generate' in profile.get('enabled_capabilities',()) and (type(query_timeout) is not int or not 10<=query_timeout<=240):
        raise ValueError('unverified query timeout')
    def forward(rid, name, args):
        wire = json.dumps({"jsonrpc":"2.0","id":rid,"method":"tools/call",
                          "params":{"name":name,"arguments":args}}, separators=(",",":")).encode()
        req = Request(upstream, method="POST", data=wire, headers={
            "Authorization": "Bearer " + backend_token, "Content-Type":"application/json",
            "Accept":"application/json, text/event-stream", "MCP-Protocol-Version":"2025-11-25"})
        with opener.open(req, timeout=query_timeout if name==QUERY and query_timeout else 5) as resp:
            if resp.status != 200 or resp.headers.get_content_type() != "application/json":
                raise ValueError("unexpected upstream transport")
            raw = resp.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise ValueError("response exceeds bound")
            return parse(raw)
    return forward

def machine_authorized(header, digests):
    if re.fullmatch(rb'Bearer [0-9a-f]{64}',header) is None:
        return False
    candidate=hashlib.sha256(header[7:]).hexdigest()
    return any([hmac.compare_digest(candidate,expected) for expected in digests])


def make_app(profile, catalog, gateway_token, read_live_release, forward=None, *, effect_token=None, operations=None, machine_digests=()):
    profile,catalog=deepcopy(profile),deepcopy(catalog)
    lane=profile.get('lane')
    caps=profile.get('enabled_capabilities',())
    manifest=catalog['catalog']['tools']
    effects="intake_process" in caps
    if (lane not in ('test','prod') or set(caps) not in ({"baseline_read"},{"baseline_read","intake_process"},{"baseline_read","intake_process","generate"})
            or len(caps)!=len(set(caps)) or effects!=(effect_token is not None)):
        raise ValueError('invalid profile')
    if effects and (not isinstance(effect_token,str) or not HEX.fullmatch(effect_token) or effect_token==gateway_token
            or {name for name,meta in manifest.items() if 'intake_process' in meta.get('capability_projections',{})}!=EFFECTS):
        raise ValueError('invalid effect authority')
    read_catalog=deepcopy(catalog['catalog'])
    for row in read_catalog['tools'].values():
        row['capability_projections'].pop('intake_process',None)
        row['capability_projections'].pop('generate',None)
    read_digest=digest(read_catalog)
    expected_read=profile.get('read_catalog_sha256',catalog['catalog_sha256'])
    if read_digest!=expected_read:raise ValueError('invalid baseline read catalog')
    effects_only=profile.get('effects_only',False)
    if type(effects_only) is not bool or effects_only and not effects:raise ValueError('invalid listener split')
    routes={} if effects_only else {f'/memory-mcp-{lane}-tools':("baseline_read",gateway_token)}
    if effects:routes[f'/memory-mcp-{lane}-effects']=("intake_process",effect_token)
    if (type(machine_digests) is not tuple or len(machine_digests)>16
            or any(not isinstance(t,str) or not HEX.fullmatch(t) for t in machine_digests)
            or len(set(machine_digests))!=len(machine_digests)
            or any(hashlib.sha256(t.encode('ascii')).hexdigest() in machine_digests
                   for t in (gateway_token,effect_token) if isinstance(t,str))
            or machine_digests and operations is None):
        raise ValueError('invalid machine credentials')
    if machine_digests: routes[f'/memory-api-{lane}']=('machine',None)
    full_sites=profile.get('sites_full_catalog',False)
    if type(full_sites) is not bool or full_sites and (operations is None or not effects or effects_only):
        raise ValueError('Full Sites catalog requires the typed registry and both authenticated routes')
    durable_queries=profile.get('durable_query_jobs',False)
    if type(durable_queries) is not bool or durable_queries and not full_sites:
        raise ValueError('Durable interactive queries require the full typed Sites profile')

    canonical_schemas={name:{**row['canonicalInputSchema'],'additionalProperties':False}
                       for name,row in manifest.items()} if machine_digests or full_sites else {}
    canonical_validators={name:Draft202012Validator(schema) for name,schema in canonical_schemas.items()}
    canonical_descriptors=[{"name":name,"description":manifest[name].get("description",name),
                          "inputSchema":schema,
                          "annotations":{"readOnlyHint":manifest[name].get("readOnlyHint") is True,
                                         "destructiveHint":False,"openWorldHint":False}}
                         for name,schema in sorted(canonical_schemas.items())]
    machine_digest=digest(canonical_descriptors)
    compiled={(cap,n):Draft202012Validator(projection) for n,t in manifest.items()
               for cap,projection in t['capability_projections'].items() if cap in caps}
    if not isinstance(gateway_token, str) or not HEX.fullmatch(gateway_token):
        raise ValueError("invalid gateway credential")
    if operations is not None:
        if forward is not None: raise ValueError("Ambiguous operation dispatch")
        declared=operations.descriptors()
        if set(declared)!=set(manifest) or any(
                declared[name]["inputSchema"]!=entry["canonicalInputSchema"]
                or declared[name]["description"]!=entry.get("description") for name,entry in manifest.items()):
            raise ValueError("Live operation descriptors differ from release catalog")
    if not callable(read_live_release) or (operations is None and not callable(forward)):
        raise ValueError("missing host authority")
    strict_catalog=profile.get("require_catalog_identity",False)
    if type(strict_catalog) is not bool: raise ValueError("invalid catalog identity enforcement")
    def descriptors(route_capability):
        discovery_caps=(route_capability,"generate") if route_capability=="intake_process" and "generate" in caps else (route_capability,)
        tools=[]
        for name,row in sorted(manifest.items()):
            for cap in discovery_caps:
                if authorized(profile,catalog,lane,name,cap):
                    read=cap=="baseline_read"
                    tools.append({"name":name,"description":row.get("description",name),
                                  "inputSchema":row["capability_projections"][cap],
                                  "annotations":{"readOnlyHint":read,"destructiveHint":False,"openWorldHint":False}})
        return tools
    route_tools={cap:descriptors(cap) for cap,_ in routes.values() if cap!='machine'}
    if full_sites:
        # Same canonical operation descriptions/schema as the machine API. The
        # already authenticated transport routes still separate reads and writes.
        route_tools={cap:[d for d in canonical_descriptors
                          if d['annotations']['readOnlyHint']==(cap=='baseline_read')]
                     for cap in ('baseline_read','intake_process')}
    full_digests={cap:digest(items) for cap,items in route_tools.items()} if full_sites else {}

    gate = asyncio.Semaphore(12)
    effect_gate = asyncio.Semaphore(1)
    query_gate = asyncio.Semaphore(1)

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                kind = (await receive())["type"]
                if kind == "lifespan.startup": await send({"type":"lifespan.startup.complete"})
                elif kind == "lifespan.shutdown":
                    await send({"type":"lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http": return

        machine=scope.get('path')==f'/memory-api-{lane}' and bool(machine_digests)
        async def reply(status, data):
            raw=json.dumps(data, separators=(",",":"), ensure_ascii=True).encode()
            if len(raw)>(MACHINE_MAX if machine else MAX_RESPONSE):
                status,raw=502,b'{"error":"unavailable"}'
            await send({"type":"http.response.start","status":status,"headers":[
                (b"content-type",b"application/json"),(b"cache-control",b"private, no-store")]})
            await send({"type":"http.response.body","body":raw})

        if scope.get("path") not in routes or scope.get("method")!="POST" or scope.get("query_string"):
            return await reply(404, {"error":"not_found"})
        def header(name):
            return [v for k,v in scope.get("headers",()) if k.lower()==name]
        capability,secret=routes[scope["path"]]
        auth=header(b"authorization")
        if machine:
            allowed=len(auth)==1 and machine_authorized(auth[0],machine_digests)
        else:
            allowed=len(auth)==1 and hmac.compare_digest(auth[0],b'Bearer '+secret.encode('ascii'))
        if not allowed:
            return await reply(401, {"error":"unauthorized"})
        if header(b"content-type")!=[b"application/json"]:
            return await reply(415, {"error":"unsupported"})
        # Handle only strict small JSON, and reject requests before invoking FastMCP.
        try:
            raw=bytearray()
            async with asyncio.timeout(3):
                while True:
                    event=await receive()
                    if event["type"]!="http.request": raise ValueError()
                    raw.extend(event.get("body",b""))
                    if len(raw)>(MACHINE_MAX if machine else MAX_REQUEST): raise ValueError()
                    if not event.get("more_body"): break
            b=parse(raw)
            basic={"request_id","operation","arguments"}
            if type(b) is not dict or set(b) not in (basic,basic|{"expected"}):
                raise ValueError()
            rid,name,args=b["request_id"],b["operation"],b["arguments"]
            if not isinstance(rid,str) or not RID.fullmatch(rid) or not isinstance(name,str) or type(args) is not dict:
                raise ValueError()
            discovery=name=="catalog"
            expected_identity=b.get("expected")
            if discovery:
                if args or set(b)!=basic: raise ValueError()
            else:
                if (strict_catalog or machine or full_sites) and "expected" not in b: raise ValueError()
                if "expected" in b and (type(expected_identity) is not dict
                        or set(expected_identity)!={"release","generation","catalog_sha256"}
                        or not all(isinstance(v,str) for v in expected_identity.values())
                        or not TAG.fullmatch(expected_identity["release"])
                        or not all(HEX.fullmatch(expected_identity[k]) for k in ("generation","catalog_sha256"))):
                    raise ValueError()
                if not full_sites and name==QUERY and capability=="intake_process" and "generate" in caps:capability="generate"
                if machine or full_sites:
                    if name not in canonical_validators or not canonical_validators[name].is_valid(args):
                        raise ValueError()
                    if not machine and manifest[name].get("readOnlyHint") is not (capability=="baseline_read"):
                        raise ValueError()
                elif (capability,name) not in compiled or not authorized(profile,catalog,lane,name,capability) or                    not compiled[(capability,name)].is_valid(args): raise ValueError()
        except Exception:
            return await reply(400, {"error":"invalid_request"})
        query_call = (name == QUERY and (machine or full_sites)) or capability == "generate"
        is_effect = (machine and manifest.get(name, {}).get("readOnlyHint") is not True) or capability == "intake_process"
        limiter = (gate if discovery or query_call and durable_queries else
                   query_gate if query_call else effect_gate if is_effect else gate)
        if limiter.locked(): return await reply(503, {"error":"unavailable"})
        try:
            async with limiter:
                identity=await asyncio.to_thread(read_live_release)
                if type(identity) is not tuple or len(identity)!=2: raise ValueError()
                release, generation = identity
                if not isinstance(release,str) or not TAG.fullmatch(release) or not isinstance(generation,str) or \
                    not HEX.fullmatch(generation): raise ValueError()
                if release!=profile.get("base_release"): raise ValueError()
                receipt={"release":release,"generation":generation,
                         "catalog_sha256":machine_digest if machine else full_digests[capability] if full_sites else
                             read_digest if capability=="baseline_read" else catalog["catalog_sha256"]}
                if discovery:
                    if await asyncio.to_thread(read_live_release)!=identity: raise ValueError()
                    return await reply(200, {"environment":lane,"request_id":rid,**receipt,
                                             "capability":capability,"tools":canonical_descriptors if machine else route_tools[capability]})
                if expected_identity is not None and expected_identity!=receipt:
                    return await reply(409, {"error":"stale_catalog"})
                if operations is None:
                    response=await asyncio.to_thread(forward,rid,name,args)
                else:
                    value=await operations.invoke(name,args)
                    if type(value) is not dict: raise ValueError()
                    json.dumps(value,separators=(",",":"),ensure_ascii=True,allow_nan=False)
                if await asyncio.to_thread(read_live_release)!=identity: raise ValueError()
            if operations is not None:
                return await reply(200,{"environment":lane,"request_id":rid,**receipt,
                                        "status":"ok","result":value})
            if operations is None:
                if type(response) is not dict or response.get("jsonrpc")!="2.0" or response.get("id")!=rid or \
                    "error" in response: raise ValueError()
                result=response.get("result")
            if type(result) is not dict or result.get("isError") is True: raise ValueError()
            content=result.get("content")
            if type(content) is not list or not 0<len(content)<=5 or any(
                type(v) is not dict or set(v)!={"type","text"} or v["type"]!="text" or
                not isinstance(v["text"],str) for v in content): raise ValueError()
            out={"content":content}
            if type(result.get("structuredContent")) is dict: out["structuredContent"]=result["structuredContent"]
            if expected_identity is None: receipt.pop("generation")
            return await reply(200, {"environment":lane,"request_id":rid,**receipt,"result":out})
        except Exception:
            return await reply(502, {"error":"unavailable"})
    return app
