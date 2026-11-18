import json
import tempfile
import unittest
from pathlib import Path

from prototypes.assertion_memory.desktop import event_identity
from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.native_acceptance import verify_sessions
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.tests.model_fixtures import with_literal_verification
from prototypes.assertion_memory.worker import LinkWorker


class NativeAcceptanceTests(unittest.TestCase):
    def test_requires_a_native_reader_and_recall_before_the_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "memory.sqlite3"
            marker = "amber-delta-278"
            statement = f"Для теста согласовали код {marker}."

            def capture(queue, session, prompt, answer):
                transcript = root / f"{session}.jsonl"
                entries = [
                    {"type": "session_meta", "payload": {"id": session, "cwd": str(root)}},
                    {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "one",
                     "turn_attribution": {"turn_trigger": "user"}}},
                    {"type": "event_msg", "payload": {"type": "item_completed", "turn_id": "one", "item": {
                        "type": "UserMessage", "content": [{"type": "text", "text": prompt}]
                    }}},
                    {"timestamp": "2099-01-01T14:00:00Z" if session == "reader" else "2099-01-01T12:00:00Z", "type": "response_item", "payload": {
                        "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": answer}],
                    }},
                    {"timestamp": "2099-01-02T00:00:00Z", "type": "event_msg", "payload": {
                        "type": "task_complete", "turn_id": "one", "last_agent_message": answer,
                    }},
                ]
                transcript.write_text("".join(json.dumps(row) + "\n" for row in entries))
                source = event_identity({"session_id": session, "turn_id": "one", "prompt": prompt})[3]
                return queue.capture_event(session, "one", prompt, source=source, metadata={
                    "transport": "codex-desktop", "cwd": str(root), "transcript_path": str(transcript),
                })

            with MemoryStore(path) as store:
                queue = CaptureQueue(store)
                source = capture(queue, "source", statement, "Принято")
                worker = LinkWorker(queue, MemoryEngine(store), with_literal_verification(lambda _: json.dumps({"assertions": [statement]})))
                extracted = worker.run_once()
                capture(queue, "reader", "Какой код согласовали?", f"Согласованный код — {marker}.")
                assertion_id = extracted.extracted_ids[0]
                self.assertEqual(extracted.id, source.id)
            trace = path.with_suffix(".desktop.jsonl")
            receipt = {"event": "recalled", "session_id": "reader", "turn_id": "one",
                       "evidence_ids": [assertion_id], "at": "2099-01-01T00:00:00Z"}
            capture_receipt = {"event": "captured", "source": source.source, "at": "2098-12-31T00:00:00Z"}
            trace.write_text(json.dumps(capture_receipt) + "\n" + json.dumps(receipt) + "\n")
            config = {"db": str(path)}
            self.assertEqual(verify_sessions(config, "source", "reader", marker)["assertion_id"], assertion_id)
            direct = verify_sessions(config, "source", "reader", marker)
            self.assertEqual(direct["source_capture_phase"], "verified_before_answer")
            staged = {"event": "staged", "source": source.source, "at": "2099-01-01T11:00:00Z"}
            capture_receipt["at"] = "2099-01-01T12:01:00Z"
            receipt["at"] = "2099-01-01T13:00:00Z"
            trace.write_text("".join(json.dumps(row) + "\n" for row in (staged, capture_receipt, receipt)))
            staged_proof = verify_sessions(config, "source", "reader", marker)
            self.assertEqual(staged_proof["source_capture_phase"], "staged_before_answer_then_verified")
            trace.write_text(json.dumps(staged) + "\n" + json.dumps(receipt) + "\n")
            with self.assertRaisesRegex(ValueError, "attributable"):
                verify_sessions(config, "source", "reader", marker)
            capture_receipt["at"] = "2099-01-01T13:01:00Z"
            trace.write_text("".join(json.dumps(row) + "\n" for row in (staged, capture_receipt, receipt)))
            with self.assertRaisesRegex(ValueError, "before the answer"):
                verify_sessions(config, "source", "reader", marker)
            capture_receipt["at"] = "2098-12-31T00:00:00Z"
            receipt["at"] = "2099-01-01T15:00:00Z"
            trace.write_text(json.dumps(capture_receipt) + "\n" + json.dumps(receipt) + "\n")
            with self.assertRaisesRegex(ValueError, "before the answer"):
                verify_sessions(config, "source", "reader", marker)
