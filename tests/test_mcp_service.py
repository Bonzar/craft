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
records, task = payload["records"], payload["task"]
if task == "Navigate thought graph":
    selected = next(row for row in records if row["role"] in ("option", "current_thought"))
    value = {"action":"focus", "question":None,
             "quote":selected.get("description",selected.get("text")), "text":"Use the visible fixture thought."}
elif task == "Check ready thought":
    time.sleep(0)
    question = next(row["text"] for row in records if row["role"] == "question")
    candidate = next(row for row in records if row["role"] == "candidate")
    value = {"verdict":"ready" if question in candidate.get("questions",[]) else "build",
             "question":None,"text":"Preserve the fixture condition for this question."}
elif task == "Construct thought":
    time.sleep(0.2)
    fragments = [fragment for row in records for fragment in row.get("fragments",[])]
    draft = {"text":"".join(fragment["text"] for fragment in fragments), "question":None,
             "support_fragments":[fragment["option"] for fragment in fragments]}
    value = {"thought":draft,"needs_detail":None} if any(row["role"] == "question" for row in records) else {"thoughts":[draft],"needs_detail":None}
elif task in ("Verify thought", "Verify source thought batch"):
    value = {"verdict":"accept", "text":"The exact fictional source and its condition are preserved."}
elif task == "Find useful shared thought question":
    value = {"question":None,"text":"This fixture needs no further composite."}
else:
    raise AssertionError("Unexpected provider task: " + task)
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

    async def section(self, client, page):
        pieces=[page['content']]
        cursor=page['continuation']
        while cursor:
            page=result(await client.call_tool(cursor['tool'],cursor['arguments']))
            pieces.append(page['content']);cursor=page['continuation']
        text=''.join(pieces)
        return text if page['encoding']=='text' else json.loads(text)

    async def test_official_stdio_roundtrip_shared_db_sources_history_and_cutoff(self):
        async with connect(self.db, self.provider) as (claude, initialized):
            self.assertEqual(initialized.serverInfo.name, "assertion-memory")
            listing = await claude.list_tools()
            tools = {tool.name:tool for tool in listing.tools}
            self.assertEqual(set(tools), {"memory_capture_message", "memory_query", "memory_search", "memory_source",
                                         "memory_history", "memory_status", "memory_process", "memory_ingest_register",
                                         "memory_ingest_report", "memory_store_artifact", "memory_artifact",
                                         "memory_model_call", "memory_sessions", "memory_session_messages",
                                         "memory_provenance", "memory_session_outputs", "memory_run_history", "memory_run_event"})
            self.assertIn("session_id", tools["memory_search"].inputSchema["properties"])
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
            self.assertTrue(view['answer_accepted'])
            self.assertIn(assertion_id, [basis['thought_id'] for basis in view['thought']['bases']])
            source = await self.section(claude,result(await claude.call_tool("memory_source",
                {"record_id":assertion_id,"section":"references"})))
            self.assertEqual(source[0]['event_id'],saved['event']['id'])
            raw=result(await claude.call_tool('memory_source',{'record_id':saved['event']['id']}))
            self.assertEqual(raw['event']['prompt'],self.message()['text'])
            self.assertEqual(raw['event']['source'],self.message()['source'])
            pinned=result(await claude.call_tool('memory_history',{'view_id':view['view_id']}))
            historic = result(await claude.call_tool("memory_query", {
                "query":"widget collection", "recorded_as_of":"2000-01-01"}))
            self.assertFalse(historic['answer_accepted'])
            self.assertEqual(historic['answer'],'В полученных материалах нет сведений для ответа на этот вопрос')
            self.assertEqual(historic['answer_metadata']['notice']['purpose'],'knowledge_limit_notice')
            self.assertTrue(historic['answer_metadata']['notice']['notice_payload_complete'])
        async with connect(self.db, self.provider, name="AnotherHarness") as (other, _):
            seen = result(await other.call_tool("memory_source", {"record_id":saved["event"]["id"]}))
            self.assertEqual(seen["event"]["actor"], "alice")
            from prototypes.assertion_memory.storage import MemoryStore
            from prototypes.assertion_memory.thought_store import ThoughtStore
            with MemoryStore(self.db) as store:
                thoughts=ThoughtStore(store);old=thoughts.get(view['view_id'])
                thoughts.publish(old.text+' The later revision retains the condition.',question=old.question,
                    bases=old.bases,metadata=old.metadata,thought_id=old.id,expected_version=old.version)
            history = await self.section(other,result(await other.call_tool('memory_history',{'view_id':view['view_id']})))
            self.assertEqual(len(history),2)
            self.assertEqual({row['version'] for row in history},{view['version_id'],view['version_id']+1})
            frozen=await self.section(other,pinned)
            self.assertEqual([row['version'] for row in frozen],[view['version_id']])

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
            self.assertEqual(assistant["job"]["status"], "waiting_model")
            invalid_span = await client.call_tool("memory_source", {"record_id":saved["event"]["id"], "start":3})
            self.assertTrue(invalid_span.isError)

    async def test_pending_restart_and_worker_survives_stdio_exit(self):
        async with connect(self.db, self.provider) as (client, _):
            saved = result(await client.call_tool("memory_capture_message", self.message()))
        async with connect(self.db, self.provider, name="Generic", background=True) as (client, _):
            pending = result(await client.call_tool("memory_status", {"event_id":saved["event"]["id"]}))
            self.assertIn(pending["job"]["status"], {"waiting_model", "running", "completed"})
        await asyncio.sleep(1)
        async with connect(self.db, self.provider) as (client, _):
            await self.wait_processed(client, saved["event"]["id"])
            view = result(await client.call_tool("memory_query", {"query":"widget collection"}))
            self.assertTrue(view['answer_accepted'])
            proof=result(await client.call_tool('memory_provenance',{'record_ids':[view['view_id']]}))
            self.assertIn('dialogue',json.dumps(proof))

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
        from prototypes.assertion_memory.thought_service import ThoughtService
        with MemoryStore(self.db) as store:
            ThoughtService(store).thoughts.publish('We use widget collection.',question='widget collection',
                metadata={'verification':'accepted'})
        self.provider.write_text(MODEL_SCRIPT.replace('time.sleep(0)','time.sleep(1.5)'))
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
