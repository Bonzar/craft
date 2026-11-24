import asyncio, json, hashlib, importlib.metadata as md, sys
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from prototypes.assertion_memory import mcp_server as canonical
TARGET="f6e76c92752ece77318286d2c1d63ed5d4f95b84"
PIN="4eea4c6bf55be51fa972c54ee5fb0a259b5b5766acbd69676e1e6804566fa11c"
assert hashlib.sha256(Path(canonical.__file__).read_bytes()).hexdigest()==PIN
if md.version("mcp")!="1.30.0" and sys.argv[1:]!=["--allow-unpinned-review"]:
 raise SystemExit("MCP SDK 1.30.0 required before promotion")
class Forbidden:
 def __getattribute__(self, key): raise AssertionError("unexpected service call")
server=FastMCP("assertion-memory")
canonical._register_write_tools(server,Forbidden(),Forbidden(),False)
canonical._register_read_tools(server,Forbidden(),Forbidden(),None)
all_tools=asyncio.run(server.list_tools())
print(json.dumps({"revision":TARGET,"module_sha256":PIN,"runtime_mcp_sdk":md.version("mcp"),"required_mcp_sdk":"1.30.0","tools":{t.name:t.inputSchema for t in sorted(all_tools,key=lambda x:x.name)}},ensure_ascii=True,sort_keys=True,separators=(",",":")))
