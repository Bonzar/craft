"""Real official-SDK stdio acceptance; the model is a deterministic test provider."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

try:
    from mcp import ClientSession, StdioServerParameters, types
    from mcp.client.stdio import stdio_client
except ImportError:
    ClientSession = None

ROOT = Path(__file__).resolve().parents[1]
MODEL_SCRIPT = r'''
import json, sys, time
payload = json.loads(sys.stdin.read().split("\n\n", 1)[1])
records = payload["records"]
task = payload["task"]
if task == "Review grounded memory contribution":
    value = {"grounding_status":"accepted", "coverage":"packet_claims",
             "reason":"Grounded transport fixture contribution.", "issues":[], "context_requests":[],
             "global_requests":[]}
elif task in {"Review grounded memory overview", "Review memory overview assembly"}:
    value = {"status":"accepted", "coverage":"all_claims", "reason":"Grounded transport fixture.",
             "issues":[], "context_requests":[], "unknown_needs":[]}
elif task == "Plan memory retrieval for a question":
    value = {"reuse_view_id": None, "related_view_ids": [], "subquestions": [], "reason": "Transport fixture."}
elif task == "Review source material coverage":
    value = {"source_coverage":{"status":"accepted","reason":"Exact literal fictional source is covered.","omissions":[]}}
elif task.startswith("Extract"):
    time.sleep(0.2)
    target = records[0]
    value = {"assertions": [{"text":target["text"], "quote":target["text"], "kind":"decision"}]}
elif task.startswith("Verify contextual"):
    value = {"verdicts": [{"index": row["index"], "status":"accepted", "reason":"source says so"}
                          for row in records if row.get("role") == "interpretation"]}
elif "link" in task.lower():
    value = {"links": []}
elif task == "Synthesize grounded memory overview":
    print("\n".join(row["text"] for row in records if row.get("role") == "verified_contribution"))
    sys.exit(0)
else:
    print("\n".join([*("[" + row["id"] + "] " + row["text"] for row in records if "id" in row and "text" in row),
                     *("Condition for [" + edge["from_id"] + "] relative to [" + edge["to_id"] + "]: " + edge["metadata"]["condition"]
                       for row in records if row.get("role") == "evidence_edges" for edge in row["edges"]
                       if edge.get("metadata", {}).get("condition"))])
          or "No evidence records were supplied for this query.")
    sys.exit(0)
print(json.dumps(value))
'''


@asynccontextmanager
async def connect(db, provider, name="Claude", background=False, idle=1):
    args = ["-m", "prototypes.assertion_memory.mcp_server", "--db", str(db),
            "--provider", "subprocess", "--model-command-json", json.dumps([sys.executable, str(provider)]),
            "--worker-idle-seconds", str(idle)]
    if not background:
        args.append("--no-background")
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    params = StdioServerParameters(command=sys.executable, args=args, env=env, cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, client_info=types.Implementation(name=name, version="test")) as client:
            initialized = await client.initialize()
            yield client, initialized


def result(value):
    if value.isError:
        raise AssertionError(value.content)
    return json.loads(value.content[0].text)


@unittest.skipIf(ClientSession is None, 'Install assertion_memory/requirements-mcp.txt for real SDK tests')
class MCPServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.root = Path(self.scratch.name)
        self.db = self.root / "memory.sqlite3"
        self.provider = self.root / "provider.py"
        self.provider.write_text(MODEL_SCRIPT)

    async def asyncTearDown(self):
        await asyncio.sleep(1.3)  # Workers release their DB before temp-directory removal.
        self.scratch.cleanup()

    def message(self, **changes):
        return {"session_id":"dialogue", "message_id":"m1", "actor_id":"alice", "role":"user",
                "text":"We decided to use the widget collection.", "position":0,
                "source":"chat://claude/dialogue/m1", **changes}

    async def wait_processed(self, client, event_id):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = result(await client.call_tool("memory_status", {"event_id":event_id}))
            if state["job"]["status"] == "completed" and state["pending_count"] == 0:
                return state
            await asyncio.sleep(0.1)
        self.fail(f"worker did not complete: {state}")

    async def view_evidence(self,client,response):
        if 'evidence' in response['view']:return response['view']['evidence']
        # A complete source can use the normal compact query response. Follow
        # its immutable evidence continuation instead of requiring inline proof.
        cursor=next(c for c in response['continuations'] if c['tool']=='memory_history'
            and c['arguments'].get('section')=='evidence')
        pieces=[]
        while cursor:
            page=result(await client.call_tool(cursor['tool'],cursor['arguments']))
            pieces.append(page['content']);cursor=page['continuation']
        return json.loads(''.join(pieces))

    async def test_official_stdio_roundtrip_shared_db_sources_history_and_cutoff(self):
        async with connect(self.db, self.provider) as (claude, initialized):
            self.assertEqual(initialized.serverInfo.name, "assertion-memory")
            listing = await claude.list_tools()
            tools = {tool.name:tool for tool in listing.tools}
            self.assertEqual(set(tools), {"memory_capture_message", "memory_query", "memory_source",
                                         "memory_history", "memory_status", "memory_process"})
            schema = tools["memory_capture_message"].inputSchema
            self.assertNotIn("kind", schema["properties"])
            for tool in tools.values():
                self.assertNotIn("db", tool.inputSchema["properties"])
            started = time.monotonic()
            saved = result(await claude.call_tool("memory_capture_message", self.message()))
            self.assertLess(time.monotonic()-started, 1)
            self.assertEqual(saved["job"]["status"], "waiting_model")
            repeated = result(await claude.call_tool("memory_capture_message", self.message()))
            self.assertEqual(saved["event"]["id"], repeated["event"]["id"])
            result(await claude.call_tool("memory_process", {"limit":20}))
            state = await self.wait_processed(claude, saved["event"]["id"])
            assertion_id = state["job"]["extracted_ids"][0]
            view = result(await claude.call_tool("memory_query", {"query":"widget collection"}))
            self.assertIn(assertion_id, json.dumps(await self.view_evidence(claude,view)))
            source = result(await claude.call_tool("memory_source", {"record_id":assertion_id}))
            self.assertEqual(source["references"][0]["text"], self.message()["text"])
            self.assertEqual(source["references"][0]["source"], self.message()["source"])
            historic = result(await claude.call_tool("memory_query", {
                "query":"widget collection", "recorded_as_of":"2000-01-01"}))
            self.assertEqual(await self.view_evidence(claude,historic), [])
        async with connect(self.db, self.provider, name="AnotherHarness") as (other, _):
            seen = result(await other.call_tool("memory_source", {"record_id":saved["event"]["id"]}))
            self.assertEqual(seen["event"]["actor"], "alice")
            history = result(await other.call_tool("memory_history", {"view_id":view["view"]["view_id"]}))
            self.assertGreaterEqual(len(history["versions"]), 2)
            self.assertEqual(history["versions"][0]["id"], view["view"]["id"])

    async def test_idempotency_and_validation_errors_do_not_mutate_capture(self):
        async with connect(self.db, self.provider) as (client, _):
            saved = result(await client.call_tool("memory_capture_message", self.message()))
            for changes in ({"text":"Changed."}, {"actor_id":"bob"}, {"position":1}, {"source":"other"}):
                response = await client.call_tool("memory_capture_message", self.message(**changes))
                self.assertTrue(response.isError, response)
            for changes in ({"role":"system"}, {"position":-1}, {"actor_id":" "}, {"text":" "}):
                response = await client.call_tool("memory_capture_message", self.message(message_id="bad", **changes))
                self.assertTrue(response.isError)
            self.assertTrue((await client.call_tool("memory_source", {"record_id":"missing"})).isError)
            self.assertTrue((await client.call_tool("memory_process", {"limit":0})).isError)
            current = result(await client.call_tool("memory_status", {"event_id":saved["event"]["id"]}))
            collision = await client.call_tool("memory_capture_message", self.message(message_id="collision"))
            self.assertTrue(collision.isError)
            self.assertEqual(current["event_count"], 1)
            assistant = result(await client.call_tool("memory_capture_message", self.message(
                message_id="m2", role="assistant", actor_id="model-a", position=1, text="Maybe use the table.")))
            self.assertEqual(assistant["job"]["status"], "completed")
            invalid_span = await client.call_tool("memory_source", {"record_id":saved["event"]["id"], "start":3})
            self.assertTrue(invalid_span.isError)

    async def test_pending_restart_and_worker_survives_stdio_exit(self):
        async with connect(self.db, self.provider) as (client, _):
            saved = result(await client.call_tool("memory_capture_message", self.message()))
        async with connect(self.db, self.provider, name="Generic", background=True) as (client, _):
            pending = result(await client.call_tool("memory_status", {"event_id":saved["event"]["id"]}))
            self.assertIn(pending["job"]["status"], {"waiting_model", "processing", "completed"})
        await asyncio.sleep(1)
        async with connect(self.db, self.provider) as (client, _):
            await self.wait_processed(client, saved["event"]["id"])
            view = result(await client.call_tool("memory_query", {"query":"widget collection"}))
            self.assertEqual(len(await self.view_evidence(client,view)), 1)

    async def test_worker_waits_for_a_previous_crashed_workers_lease(self):
        from datetime import datetime, timedelta, timezone
        from prototypes.assertion_memory.queue import CaptureQueue
        from prototypes.assertion_memory.storage import MemoryStore
        async with connect(self.db, self.provider) as (client, _):
            saved = result(await client.call_tool("memory_capture_message", self.message()))
        with MemoryStore(self.db) as store:
            claim = CaptureQueue(store)._claim(with_model=True)
            self.assertEqual(claim.job.id, saved["event"]["id"])
            expiry = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
            with store.transaction(write=True) as connection:
                connection.execute("UPDATE memory_capture_jobs SET lease_until = ? WHERE id = ?",
                                   (expiry, claim.job.id))
        async with connect(self.db, self.provider, background=True, idle=0.1) as (client, _):
            state = await self.wait_processed(client, saved["event"]["id"])
            self.assertEqual(state["job"]["attempts"], 2)

    async def test_shared_database_needs_no_optional_adapter(self):
        from prototypes.assertion_memory.storage import MemoryStore
        from prototypes.assertion_memory.queue import CaptureQueue
        probe = await asyncio.create_subprocess_exec(
            sys.executable, '-c',
            "import sys; import prototypes.assertion_memory.mcp_server; "
            "assert 'prototypes.assertion_memory.desktop' not in sys.modules",
            cwd=ROOT, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, error = await probe.communicate()
        self.assertEqual(probe.returncode, 0, error.decode()[:1000])
        async with connect(self.db, self.provider, name="NoAdapterHarness") as (client, _):
            saved = result(await client.call_tool("memory_capture_message", self.message()))
        with MemoryStore(self.db) as store:
            event = CaptureQueue(store).get_event(saved["event"]["id"])
            self.assertEqual(event.prompt, self.message()["text"])
            self.assertEqual(event.source, self.message()["source"])
            self.assertEqual(event.metadata["role"], "user")

    async def test_slow_query_does_not_block_capture_or_status_on_same_stdio_client(self):
        from prototypes.assertion_memory.storage import MemoryStore
        with MemoryStore(self.db) as store:
            store.append_assertion(assertion_id="seed", text="We use widget collection.", actor="alice",
                                   source="test://seed", episode="seed", recorded_at="2026-01-01")
        self.provider.write_text(MODEL_SCRIPT.replace('else:\n    print',
            'else:\n    time.sleep(1.5)\n    print'))
        async with connect(self.db, self.provider) as (client, _):
            slow = asyncio.create_task(client.call_tool("memory_query", {"query":"widget collection"}))
            await asyncio.sleep(0.3)
            self.assertFalse(slow.done())
            started = time.monotonic()
            captured = result(await client.call_tool("memory_capture_message", self.message()))
            state = result(await client.call_tool("memory_status", {"event_id":captured["event"]["id"]}))
            self.assertLess(time.monotonic() - started, 0.75)
            self.assertEqual(state["job"]["status"], "waiting_model")
            self.assertFalse(slow.done())
            result(await slow)

    async def test_completed_jobs_keep_unresolved_extraction_and_link_reviews_visible(self):
        from prototypes.assertion_memory.storage import MemoryStore
        async with connect(self.db, self.provider) as (client, _):
            captured = result(await client.call_tool("memory_capture_message", self.message()))
            proposals = [{"kind":"interpretation", "status":"unresolved", "reason":"ambiguous reference"},
                         {"kind":"supports", "status":"pending", "reason":"needs semantic review"}]
            with MemoryStore(self.db) as store:
                with store.transaction(write=True) as connection:
                    connection.execute("UPDATE memory_capture_jobs SET status='completed', proposals=? WHERE id=?",
                                       (json.dumps(proposals), captured["event"]["id"]))
            view = result(await client.call_tool("memory_query", {"query":"widget collection"}))
            self.assertEqual(view["pending_count"], 0)
            self.assertEqual(view["unresolved_count"], 2)
            self.assertEqual(len(view["unresolved"]), 2)
            self.assertEqual(view["processing_state"], "needs_review")
            self.assertEqual(view["completeness"], "incomplete")

    async def test_status_scope_and_pagination_parameters_roundtrip(self):
        async with connect(self.db, self.provider) as (client, _):
            for i in range(3):
                result(await client.call_tool("memory_capture_message", self.message(
                    message_id=f"private-{i}",position=i,scope="private-scope")))
            page = result(await client.call_tool("memory_status", {
                "scope":"private-scope","offset":1,"limit":1}))
            self.assertEqual(page["pending_count"],3)
            self.assertEqual(len(page["pending_jobs"]),1)
            self.assertEqual(page["pagination"]["pending_jobs"]["next_offset"],2)
            public = result(await client.call_tool("memory_query", {
                "query":"public collection","scope":"public-scope"}))
            self.assertEqual(public["event_count"],0)
            self.assertEqual(public["pending_count"],0)
            self.assertEqual(public["processing_state"],"idle")
            self.assertNotIn("private-scope",json.dumps(public))


if __name__ == "__main__":
    unittest.main()
