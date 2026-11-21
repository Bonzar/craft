"""Synthetic parity fixtures emitted by the actual original MCP dispatcher."""
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from prototypes.assertion_memory.mcp_server import ServiceConfig, create_server
from prototypes.assertion_memory.storage import MemoryStore


async def oracle():
    await_cases = []
    with TemporaryDirectory() as directory:
        path = str(Path(directory) / "memory.sqlite3")
        with MemoryStore(path) as store:
            server = create_server(ServiceConfig(db=path, provider="none"), background=False, store=store)

            async def call(name, args):
                reply = await server.call_tool(name, args)
                return json.loads(reply[0].text)

            async def add(suffix, text, scope):
                return (await call("memory_capture_message", dict(
                    session_id="fictional-"+suffix, message_id="message-1", actor_id="fictional-user",
                    role="user", text=text, source="fixture://source", scope=scope,
                    source_message_at="2026-01-01T12:31:35.123456+01:00", timezone="Europe/Berlin",
                )))["event"]["id"]

            a = await add("plain", "Start 🌿\nКонец.", "allowed-fixture")
            b = await add("budget", "Q" + '\\"\U0001f339' * 4000, "allowed-fixture")
            c = await add("unauthorized", "Private fixture must not leak.", "other-fixture")
            with store.transaction() as sql:
                seed = [dict(row) for row in sql.execute("SELECT * FROM memory_raw_events ORDER BY rowid")]
                for raw in seed:
                    raw["metadata"] = json.loads(raw["metadata"])
            for args in [
                dict(record_id=a, section="text", limit=6),
                dict(record_id=a, section="text", offset=6, snapshot_id=9),
                dict(record_id=a, section="metadata", limit=31),
                dict(record_id=a, section="metadata", offset=31, snapshot_id=0),
                dict(record_id=a, start=0, end=14),
                dict(record_id=a, start=6, end=8, section="edges"),
                dict(record_id=b, section="text", limit=12000),
                dict(record_id=b, start=0, end=6000),
            ]:
                await_cases.append([args, await call("memory_source", args)])
    return dict(seed=seed, expected=await_cases, denied=c)


if __name__ == "__main__":
    print(json.dumps(asyncio.run(oracle()), ensure_ascii=False))
