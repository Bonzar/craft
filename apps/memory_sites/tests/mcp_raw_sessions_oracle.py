"""Compare original MCP dispatcher JSON using only fictional records."""
import asyncio
import json
import tempfile

from prototypes.assertion_memory.mcp_server import ServiceConfig, create_server
from prototypes.assertion_memory.storage import MemoryStore


async def build():
    options = [
        ("empty", {}, None), ("sessions", {}, None), ("sessions", {"limit":1}, None),
        ("sessions", {"snapshot_id":2,"offset":1}, None),
        ("sessions", {"snapshot_id":0}, None),
        ("messages", {"session_id":"native"}, None),
        ("messages", {"session_id":"capture-a","limit":1}, None),
        ("messages", {"session_id":"capture-a","offset":1,"snapshot_id":3}, None),
        ("messages", {"session_id":"native","turn_id":"m1","message_id":"m1"}, None),
        ("messages", {"session_id":"native","turn_id":"real-turn"}, None),
        ("messages", {"session_id":"fallback","message_id":"m4"}, None),
        ("sessions", {"limit":200}, "budget"),
    ]
    definitions = [
        ("capture-a",{"native_session_id":"native","native_turn_id":"real-turn","role":"user"},9),
        ("native",{"native_session_id":"   ","role":"assistant"},None),
        ("capture-a",{"native_session_id":"native","role":"tool"},0),
        ("fallback",{"native_session_id":False},1),
    ] + [("z"*1300+str(i),{},None) for i in range(20)]
    seed=[]
    for i,(session,meta,position) in enumerate(definitions,1):
        message=f"m{i}"
        seed.append([dict(id=f"fixture-{i}",session_id=session,turn_id=message,prompt="NEVER EXPOSE 🧭",
                         actor="fictional",source="synthetic",scope="test",
                         recorded_at=f"2026-01-01T00:00:{i:02}+00:00",
                         metadata={**meta,"message_id":message}),position])
    async def call(server,name,args):
        reply=await server.call_tool(name,args)
        return json.loads(reply[0].text)
    with tempfile.TemporaryDirectory() as directory:
        path=directory+"/test.sqlite3"
        with MemoryStore(path) as store:
            server=create_server(ServiceConfig(db=path,provider="none"),background=False,store=store)
            empty=await call(server,"memory_sessions",{})
            expected=[["memory_sessions",{},empty,"empty"]]
            for start,end in [(0,4),(4,len(seed))]:
                with store.transaction(write=True) as c:
                    for raw,position in seed[start:end]:
                        c.execute("INSERT INTO memory_raw_events VALUES (?,?,?,?,?,?,?,?,?)",
                                  tuple(raw[key] for key in ("id","session_id","turn_id","prompt","actor","source","scope","recorded_at"))+
                                  (json.dumps(raw["metadata"]),))
                        if position is not None:
                            c.execute("INSERT INTO memory_event_positions VALUES (?,?)",(raw["id"],position))
                for kind,args,group in options:
                    if kind=="empty" or (group=="budget")!=(start>0):continue
                    name="memory_sessions" if kind=="sessions" else "memory_session_messages"
                    expected.append([name,args,await call(server,name,args),group])
    return {"seed":seed,"expected":expected}


if __name__=="__main__":
    print(json.dumps(asyncio.run(build()),ensure_ascii=False))
