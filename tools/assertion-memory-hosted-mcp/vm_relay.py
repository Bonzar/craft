"""One common hosted MCP ASGI relay. Bind an independently attested environment profile at boot."""
import asyncio, hmac, json, re
from copy import deepcopy
from jsonschema import Draft202012Validator
from policy import authorized
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

MAX_REQUEST, MAX_RESPONSE = 100_000, 131_072
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
    def forward(rid, name, args):
        wire = json.dumps({"jsonrpc":"2.0","id":rid,"method":"tools/call",
                          "params":{"name":name,"arguments":args}}, separators=(",",":")).encode()
        req = Request(upstream, method="POST", data=wire, headers={
            "Authorization": "Bearer " + backend_token, "Content-Type":"application/json",
            "Accept":"application/json, text/event-stream", "MCP-Protocol-Version":"2025-11-25"})
        with opener.open(req, timeout=5) as resp:
            if resp.status != 200 or resp.headers.get_content_type() != "application/json":
                raise ValueError("unexpected upstream transport")
            raw = resp.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise ValueError("response exceeds bound")
            return parse(raw)
    return forward

def make_app(profile, catalog, gateway_token, read_live_release, forward):
    profile,catalog=deepcopy(profile),deepcopy(catalog)
    lane=profile.get('lane')
    if lane not in ('test','prod') or any(x!='baseline_read' for x in profile.get('enabled_capabilities',())):
        raise ValueError('invalid profile')
    path=f'/memory-mcp-{lane}-tools'
    compiled={n:Draft202012Validator(t['capability_projections']['baseline_read']) for n,t in catalog['catalog']['tools'].items()
              if t['classification']=='baseline_read'}
    if not isinstance(gateway_token, str) or not HEX.fullmatch(gateway_token):
        raise ValueError("invalid gateway credential")
    if not callable(read_live_release) or not callable(forward):
        raise ValueError("missing host authority")
    expected = b"Bearer " + gateway_token.encode()
    gate = asyncio.Semaphore(12)

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                kind = (await receive())["type"]
                if kind == "lifespan.startup": await send({"type":"lifespan.startup.complete"})
                elif kind == "lifespan.shutdown":
                    await send({"type":"lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http": return

        async def reply(status, data):
            raw=json.dumps(data, separators=(",",":"), ensure_ascii=True).encode()
            if len(raw)>MAX_RESPONSE:
                status,raw=502,b'{"error":"unavailable"}'
            await send({"type":"http.response.start","status":status,"headers":[
                (b"content-type",b"application/json"),(b"cache-control",b"private, no-store")]})
            await send({"type":"http.response.body","body":raw})

        if scope.get("path")!=path or scope.get("method")!="POST" or scope.get("query_string"):
            return await reply(404, {"error":"not_found"})
        def header(name):
            return [v for k,v in scope.get("headers",()) if k.lower()==name]
        auth=header(b"authorization")
        if len(auth)!=1 or not hmac.compare_digest(auth[0],expected):
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
                    if len(raw)>MAX_REQUEST: raise ValueError()
                    if not event.get("more_body"): break
            b=parse(raw)
            if type(b) is not dict or set(b)!={"request_id","operation","arguments"}:
                raise ValueError()
            rid,name,args=b["request_id"],b["operation"],b["arguments"]
            if not isinstance(rid,str) or not RID.fullmatch(rid) or not isinstance(name,str) or type(args) is not dict or \
                name not in compiled or not authorized(profile,catalog,lane,name,"baseline_read") or not compiled[name].is_valid(args):
                raise ValueError()
        except Exception:
            return await reply(400, {"error":"invalid_request"})
        if gate.locked(): return await reply(503, {"error":"unavailable"})
        try:
            async with gate:
                identity=await asyncio.to_thread(read_live_release)
                if type(identity) is not tuple or len(identity)!=2: raise ValueError()
                release, generation = identity
                if not isinstance(release,str) or not TAG.fullmatch(release) or not isinstance(generation,str) or \
                    not HEX.fullmatch(generation): raise ValueError()
                if release!=profile.get("base_release"): raise ValueError()
                response=await asyncio.to_thread(forward,rid,name,args)
                if await asyncio.to_thread(read_live_release)!=identity: raise ValueError()
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
            return await reply(200, {"environment":lane,"release":release,"request_id":rid,"catalog_sha256":catalog["catalog_sha256"],"result":out})
        except Exception:
            return await reply(502, {"error":"unavailable"})
    return app
