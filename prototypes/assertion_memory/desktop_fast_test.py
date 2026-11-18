"""Synthetic protocol tests for the explicitly enabled lightweight hook."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import desktop
from .queue import CaptureQueue
from .storage import MemoryStore


class DesktopFastTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = {"db": str(self.root / "memory.sqlite3"), "scope": "fiction",
                       "cwd_roots": [str(self.root)], "auto_process": False, "mode": "fast"}
        self.path = self.root / "synthetic.jsonl"
        self.rows = [{"type": "session_meta", "payload": {"id": "physical-fiction", "cwd": str(self.root)}}]

    def tearDown(self):
        self.tmp.cleanup()

    def turn(self, turn="one", text="Should we choose the blue fictional boat?", trigger="user"):
        self.rows += [
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": turn,
                 "turn_attribution": {"turn_trigger": trigger}}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": text}],
                "internal_chat_message_metadata_passthrough": {"turn_id": turn, "content_item_kinds": ["user.text"],
                    "content_item_metadata": [{"provenance": {"type": "user"}}]}}},
        ]
        self.flush()
        return {"hook_event_name": "UserPromptSubmit", "session_id": "physical-fiction", "turn_id": turn,
                "prompt": text, "cwd": str(self.root), "transcript_path": str(self.path)}

    def flush(self):
        self.path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in self.rows))

    def test_fast_capture_has_small_control_context_without_recall_or_models(self):
        raw = self.turn(text="  The fictional boat is blue.\nKeep this exact.  ")
        with patch.object(desktop, "recall", side_effect=AssertionError("ordinary prompts must not recall")), \
             patch("prototypes.assertion_memory.runtime_config.build_candidate_search", side_effect=AssertionError("no embedding construction")), \
             patch("prototypes.assertion_memory.runtime_config.build_model", side_effect=AssertionError("no model construction")):
            result = desktop.handle_event(raw, self.config)
        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertLess(len(context), 900)
        self.assertIn("automatically", context)
        self.assertIn("do not", context.lower())
        with MemoryStore(self.config["db"]) as store:
            self.assertEqual([event.prompt for event in CaptureQueue(store).list_events()], [raw["prompt"]])

    def assistant(self, text, message="assistant-fiction"):
        self.rows.append({"type": "response_item", "payload": {"id": message, "type": "message", "role": "assistant",
            "channel": "final", "content": [{"type": "output_text", "text": text}]}})
        self.flush()

    def test_activation_alias_and_seed_preserve_physical_provenance_without_replaying_history(self):
        from .desktop_fast import recover
        self.turn("old", "Old fictional user input must not replay.")
        self.assistant("Propose keeping the fictional blue boat?\nExact context.", "seed-fiction")
        activation = self.path.stat().st_size
        self.config.update(native_session_ids=["physical-fiction"],
            logical_session_aliases={"physical-fiction": "logical-fiction"},
            capture_after_offsets={"physical-fiction": activation}, user_actor_id="fictional-user",
            bootstrap_assistant_context={"physical-fiction": {"turn_id": "old", "message_id": "seed-fiction"}})
        raw = self.turn("new", "да")
        desktop.handle_event(raw, self.config)
        final = "Keeping the boat.\nThis is an assistant report, not a new human decision."
        self.assistant(final, "new-final")
        desktop.handle_event({**raw, "hook_event_name": "Stop"}, self.config)
        recover(self.config)
        recover(self.config)
        with MemoryStore(self.config["db"]) as store:
            queue = CaptureQueue(store)
            events = queue.list_events()
            self.assertEqual(len(events), 3)
            human = next(event for event in events if event.actor == "fictional-user")
            self.assertEqual(human.prompt, "да")
            self.assertEqual(human.session_id, "logical-fiction")
            self.assertIn("/session/physical-fiction/", human.source)
            self.assertEqual(human.metadata["native_session_id"], "physical-fiction")
            self.assertEqual(human.metadata["declared_user_actor_id"], "fictional-user")
            self.assertGreaterEqual(human.metadata["source_position"], activation)
            self.assertEqual([event.prompt for event in queue.context_events(human.id)],
                             ["Propose keeping the fictional blue boat?\nExact context.", "да"])
            self.assertEqual(next(event.prompt for event in events if event.turn_id == "new" and event.actor == "assistant"), final)
            self.assertEqual(store.list_assertions(), [])

    def test_physical_allowlist_and_synthetic_nonhuman_turns_fail_closed(self):
        self.config.update(native_session_ids=["physical-fiction"],
            logical_session_aliases={"physical-fiction": "logical-fiction"})
        raw = self.turn()
        self.assertEqual(desktop.handle_event({**raw, "session_id": "logical-fiction"}, self.config), {})
        for trigger in ("app_tool_create_thread", "guardian_review", "subagent_message", "auto_resume", "root_goal_continue"):
            raw = self.turn(turn=trigger, trigger=trigger)
            self.assertEqual(desktop.handle_event(raw, self.config), {})
        self.assertEqual(desktop.handle_event({**raw, "transcript_path": None}, self.config), {})
        self.assertEqual(desktop.handle_event({**raw, "agent_id": "fictional-subagent"}, self.config), {})
        if Path(self.config["db"]).exists():
            with MemoryStore(self.config["db"]) as store:
                self.assertEqual(CaptureQueue(store).list_events(), [])

    def test_fast_prompt_does_not_read_historical_store_or_catch_up(self):
        raw = self.turn()
        with patch.object(MemoryStore, "list_assertions", side_effect=AssertionError("no corpus read")), \
             patch.object(MemoryStore, "list_edges", side_effect=AssertionError("no historical links")), \
             patch.object(MemoryStore, "search", side_effect=AssertionError("no historical search")), \
             patch.object(desktop, "catch_up", side_effect=AssertionError("no old turn replay")):
            desktop.handle_event(raw, self.config)

    def test_stop_is_durable_and_detached_before_native_task_complete(self):
        from .desktop_fast import recover
        raw = self.turn()
        self.assistant("Synthetic final before task_complete.")
        config_path = self.root / "desktop.json"
        self.config["auto_process"] = True
        config_path.write_text(json.dumps(self.config))
        with patch("subprocess.Popen") as launch:
            self.assertEqual(desktop.handle_event({**raw, "hook_event_name": "Stop"}, self.config, config_path=config_path), {})
        self.assertTrue(launch.called)
        self.assertTrue(launch.call_args.kwargs["start_new_session"])
        self.assertFalse(Path(self.config["db"]).exists())
        recover(self.config)
        with MemoryStore(self.config["db"]) as store:
            events = CaptureQueue(store).list_events()
            self.assertEqual(len(events), 2)
            self.assertEqual(next(event.prompt for event in events if event.actor == "assistant"), "Synthetic final before task_complete.")

    def test_native_staging_recovers_exactly_once_with_declared_actor_and_alias(self):
        from .desktop_fast import recover
        raw = self.turn(text="  Fictional native input.\n  Exact whitespace. ")
        self.rows[0]["payload"].update(originator="Codex Desktop", source="vscode")
        self.rows[1]["payload"].update(root_turn_id="one", turn_attribution={"turn_trigger": None,
            "parent_turn_id": None, "initiating_agent_path": None, "root_turn_id": "one"})
        complete = list(self.rows)
        complete.append({"type": "event_msg", "payload": {"type": "item_completed", "thread_id": "physical-fiction",
            "turn_id": "one", "item": {"type": "UserMessage", "content": [{"type": "text", "text": raw["prompt"]}]}}})
        self.rows = self.rows[:2]
        self.flush()
        self.config.update(native_session_ids=["physical-fiction"], user_actor_id="fictional-user",
                           logical_session_aliases={"physical-fiction": "logical-fiction"})
        desktop.handle_event(raw, self.config)
        with MemoryStore(self.config["db"]) as store:
            queue = CaptureQueue(store)
            self.assertEqual(queue.list_events(), [])
        receipts = list(Path(self.config["db"] + ".desktop-recovery").glob("*.json"))
        self.assertEqual(len(receipts), 1)
        self.rows = complete
        self.flush()
        recover(self.config)
        recover(self.config)
        with MemoryStore(self.config["db"]) as store:
            queue = CaptureQueue(store)
            events = queue.list_events()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].prompt, raw["prompt"])
            self.assertIn('/message/', events[0].source)
            self.assertEqual(queue.get(events[0].id).status, "waiting_model")
            self.assertEqual(store.list_assertions(), [])

    def test_recovery_keeps_receipt_after_commentary_until_final_exists(self):
        from .desktop_fast import recover
        raw = self.turn()
        desktop.handle_event(raw, self.config)
        self.assistant("Synthetic commentary is not the final result.", "commentary")
        self.rows[-1]["payload"]["channel"] = "commentary"
        self.flush()
        recover(self.config)
        self.assertTrue(list(Path(self.config["db"] + ".desktop-recovery").glob("*.json")))
        self.assistant("Exact final after the lost Stop.", "final")
        recover(self.config)
        with MemoryStore(self.config["db"]) as store:
            self.assertIn("Exact final after the lost Stop.", [event.prompt for event in CaptureQueue(store).list_events()])

    def test_nonhuman_stop_and_mismatched_prompt_never_stage_raw_receipts(self):
        raw = self.turn(trigger="app_tool_create_thread")
        desktop.handle_event({**raw, "hook_event_name": "Stop"}, self.config)
        self.assertFalse(Path(self.config["db"] + ".desktop-recovery").exists())
        raw = self.turn("human")
        desktop.handle_event({**raw, "prompt": "Invented input different from native transcript"}, self.config)
        self.assertFalse(Path(self.config["db"]).exists())

    def test_native_steering_staged_before_persistence_gets_exact_later_position(self):
        from .desktop_fast import recover
        raw = self.turn(text="First fictional input.")
        self.rows[0]["payload"].update(originator="Codex Desktop", source="vscode")
        self.rows[1]["payload"].update(root_turn_id="one", turn_attribution={"turn_trigger": None,
            "parent_turn_id": None, "initiating_agent_path": None, "root_turn_id": "one"})
        self.rows.append({"type": "event_msg", "payload": {"type": "item_completed", "thread_id": "physical-fiction",
            "turn_id": "one", "item": {"type": "UserMessage", "content": [{"type": "text", "text": raw["prompt"]}]}}})
        self.flush()
        steering = {**raw, "prompt": "Second fictional steering input."}
        desktop.handle_event(steering, self.config)
        self.rows.append({**self.rows[2], "payload": {**self.rows[2]["payload"],
            "content": [{"type": "input_text", "text": steering["prompt"]}]}})
        self.rows.append({"type": "event_msg", "payload": {"type": "item_completed", "thread_id": "physical-fiction",
            "turn_id": "one", "item": {"type": "UserMessage", "content": [{"type": "text", "text": steering["prompt"]}]}}})
        self.flush()
        recover(self.config)
        with MemoryStore(self.config["db"]) as store:
            queue = CaptureQueue(store)
            events = queue.list_events()
            first = next(event for event in events if event.prompt == raw["prompt"])
            second = next(event for event in events if event.prompt == steering["prompt"])
            self.assertEqual(queue.get(second.id).status, "waiting_model")
            self.assertEqual([event.id for event in queue.context_events(second.id)], [first.id, second.id])

    def test_overlapping_recovery_waits_for_raw_recovery_owner_instead_of_dropping_stop(self):
        import fcntl
        import subprocess
        import sys
        import time
        from .desktop_fast import _receipt_dir
        raw = self.turn()
        desktop.handle_event(raw, self.config)
        root = _receipt_dir(self.config)
        config_path = self.root / "profile.json"
        config_path.write_text(json.dumps(self.config))
        with (root / "recovery.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            code = ("import json,sys; from prototypes.assertion_memory.desktop_fast import recover; "
                    "print('ready',flush=True); print(recover(json.load(open(sys.argv[1]))),flush=True)")
            child = subprocess.Popen([sys.executable, "-c", code, str(config_path)], stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), "ready")
                time.sleep(.05)
                self.assertIsNone(child.poll(), "overlapping recovery must wait for its predecessor")
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
                child.communicate(timeout=5)

    def test_synthetic_9000_assertion_protocol_fast_hook_is_subsecond(self):
        """Real subprocess, SQLite and protocol; measures no live Desktop latency."""
        import shutil
        import statistics
        import subprocess
        import sys
        import time
        raw = self.turn(text="How are fictional boats stored?")
        with MemoryStore(self.config["db"]) as store:
            with store.transaction(write=True):
                for index in range(9000):
                    store.append_assertion(assertion_id=f"fiction-{index}",
                        text=f"Fictional boat {index} uses blue rope.", actor="fictional-user",
                        source=f"fiction://{index}", episode="fiction", scope="fiction",
                        recorded_at="2026-11-01T00:00:00+00:00")
        timings = {}
        for mode in ("legacy", "fast"):
            profile = {**self.config, "mode": mode, "db": str(self.root / f"{mode}.sqlite3"),
                       "environment": {"ASSERTION_MEMORY_MODEL_MODE": "extractive",
                                       "ASSERTION_MEMORY_EMBEDDING_MODE": "lexical"}}
            shutil.copy2(self.config["db"], profile["db"])
            config_path = self.root / f"{mode}.json"
            config_path.write_text(json.dumps(profile))
            elapsed = []
            for _ in range(3):
                started = time.perf_counter()
                result = subprocess.run([sys.executable, desktop.__file__, "hook", "--config", str(config_path)],
                    input=json.dumps(raw), capture_output=True, text=True, check=True, timeout=10)
                elapsed.append(time.perf_counter() - started)
                context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
                if mode == "fast":
                    self.assertLess(len(context), 900)
                    self.assertIn("automatically", context)
            timings[mode] = statistics.median(elapsed)
        self.assertLess(timings["fast"], 1.0, timings)
        self.assertLess(timings["fast"], timings["legacy"], timings)

    def test_explicit_verified_seed_precedes_first_short_confirmation_without_rewriting_history(self):
        from .desktop_fast import seed_assistant_context
        raw = self.turn("before", "Synthetic initial question?")
        self.assistant("Propose the fictional green boat?\nExact final seed.", "explicit-seed")
        self.config.update(native_session_ids=["physical-fiction"], user_actor_id="fictional-user",
            logical_session_aliases={"physical-fiction": "logical-fiction"},
            capture_after_offsets={"physical-fiction": self.path.stat().st_size})
        first = seed_assistant_context(raw, self.config, "explicit-seed")
        self.assertEqual(seed_assistant_context(raw, self.config, "explicit-seed")["event_id"], first["event_id"])
        confirmation = self.turn("after", "да")
        desktop.handle_event(confirmation, self.config)
        with MemoryStore(self.config["db"]) as store:
            queue = CaptureQueue(store)
            events = queue.list_events()
            self.assertEqual(len(events), 2)
            user = next(event for event in events if event.actor == "fictional-user")
            self.assertEqual([event.prompt for event in queue.context_events(user.id)],
                ["Propose the fictional green boat?\nExact final seed.", "да"])
            self.assertEqual(store.list_assertions(), [])
        with self.assertRaisesRegex(ValueError, "assistant"):
            seed_assistant_context(raw, self.config, "nonexistent-message")
