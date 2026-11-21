"""Fictional canonical Thought-history fixtures from the real MCP dispatcher."""
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from prototypes.assertion_memory.mcp_server import ServiceConfig, create_server
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_payload import decode_payload


async def build():
    with TemporaryDirectory() as directory:
        path = str(Path(directory) / "history.sqlite3")
        with MemoryStore(path) as store:
            config = ServiceConfig(db=path, provider="none")
            service = config.service(store)
            server = create_server(config, background=False, store=store)

            async def call(name, args):
                result = await server.call_tool(name, args)
                return json.loads(result[0].text)

            async def capture(suffix, text, scope):
                receipt = await call("memory_capture_message", dict(session_id="history-fixture-"+suffix,
                    message_id="one", actor_id="user", role="user", text=text, source="fixture://history/"+suffix,
                    scope=scope))
                return receipt["event"]["id"]

            source_id = await capture("authorized", "Ключ 🧩: ёж.", "fictional-allowed")
            secret_id = await capture("denied", "Foreign fictional fixture.", "fictional-denied")
            store_thoughts = service.thoughts
            root = store_thoughts.publish("🐾 Сказано «ёж».", question="Что сказано?",
                origin={"kind":"source_interpretation","event_id":source_id}, thought_id="answer-fixture",
                bases=[{"event_id":source_id,"span":{"start":8,"end":10},"meaning":"ёж"}])
            store_thoughts.save_question_check(root, "Какое животное?", "test-frontier")
            proof = dict(thought_id=root.id,version=1)
            dep = store_thoughts.publish("Версия 2 сохраняет утверждение.", question="Какая версия?",
                origin={"kind":"source_interpretation","event_id":source_id}, thought_id=root.id,
                expected_version=1, bases=[proof])
            secret = store_thoughts.publish("Unavailable synthetic text.", question="Private fixture?",
                origin={"kind":"source_interpretation","event_id":secret_id}, thought_id="foreign-fixture",
                bases=[{"event_id":secret_id}])
            mix = store_thoughts.publish("A mixed-scope record.",
                origin={"kind":"source_interpretation","event_id":source_id}, thought_id="mixed-fixture",
                bases=[{"event_id":secret_id}])
            with store.transaction() as c:
                raw = [dict(row) for row in c.execute("SELECT * FROM memory_raw_events ORDER BY rowid")]
                for row in raw:
                    row["metadata"] = json.loads(row["metadata"])
                versions = [{**dict(row),"payload":decode_payload(row["payload"])}
                            for row in c.execute("SELECT * FROM memory_thought_versions ORDER BY rowid")]
                questions = [dict(row) for row in c.execute("SELECT * FROM memory_thought_reuse_checks")]
                proof_schema = [dict(row) for row in c.execute("PRAGMA table_info(memory_thought_reuse_checks)")]
            cases = [
                {"view_id":root.id}, {"view_id":root.id,"version_id":1,"limit":91},
                {"view_id":root.id,"section":"summary","version_id":1},
                {"view_id":root.id,"section":"versions","snapshot_id":1},
                {"view_id":root.id,"version_id":2,"snapshot_id":1},
                {"view_id":root.id,"section":"checks","snapshot_id":1,"offset":91,"limit":91},
                {"view_id":root.id,"snapshot_id":0},
            ]
            expected = [(case,await call("memory_history",case)) for case in cases]
            span = {"record_id":source_id,"start":8,"end":10}
            return dict(raw=raw,versions=versions,questions=questions,proof_schema=proof_schema,expected=expected,
                denied=[secret.id,mix.id],span=[span,await call("memory_source",span)])


if __name__ == "__main__":
    print(json.dumps(asyncio.run(build()),ensure_ascii=False))
