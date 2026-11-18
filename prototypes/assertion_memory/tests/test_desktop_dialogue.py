"""Authenticated Desktop dialogue transport, including missed Stop recovery."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prototypes.assertion_memory import desktop
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore


class DesktopDialogueTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.transcript = self.root / "rollout.jsonl"
        self.config = {"db": str(self.root / "memory.sqlite3"), "scope": "campus", "auto_process": False,
                       "cwd_roots": [str(self.root)]}
        self.rows = [{"type": "session_meta", "payload": {"id": "native", "cwd": str(self.root)}}]

    def turn(self, turn, prompt, answer=None, *, trigger="user", channel="final", completion=True, parent=None):
        attribution = {"turn_trigger": trigger}
        if parent:
            attribution.update(parent_turn_id=parent, initiating_agent_path="/root/child")
        self.rows += [
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": turn, "turn_attribution": attribution}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
                "internal_chat_message_metadata_passthrough": {"turn_id": turn,
                    "content_item_kinds": ["user.text"], "content_item_metadata": [{"provenance": {"type": "user"}}]}}},
        ]
        if answer is not None:
            payload = {"type": "message", "role": "assistant", "id": "answer-" + turn,
                       "content": [{"type": "output_text", "text": answer}]}
            if channel is not None:
                payload["channel"] = channel
            self.rows.append({"type": "response_item", "payload": payload})
            if completion:
                self.rows.append({"type": "event_msg", "payload": {"type": "task_complete", "turn_id": turn,
                                                                         "last_agent_message": answer}})

    def write(self):
        self.transcript.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in self.rows))

    def raw(self, turn, *, event="Stop", prompt=None):
        raw = {"hook_event_name": event, "session_id": "native", "turn_id": turn,
               "cwd": str(self.root), "transcript_path": str(self.transcript)}
        if prompt is not None:
            raw["prompt"] = prompt
        return raw

    def events(self):
        with MemoryStore(self.config["db"]) as store:
            return CaptureQueue(store).list_events("native")

    def test_stop_captures_final_assistant_as_raw_context_with_idempotent_identity(self):
        self.turn("one", "Нужен экономный режим.", "Предлагаю отключать корпус ночью. Подтверждаешь?", channel=None)
        self.write()
        desktop.handle_event(self.raw("one"), self.config)
        desktop.handle_event(self.raw("one"), self.config)
        events = self.events()
        self.assertEqual([(row.actor, row.prompt) for row in events], [
            ("user", "Нужен экономный режим."),
            ("assistant", "Предлагаю отключать корпус ночью. Подтверждаешь?"),
        ])
        self.assertEqual(events[1].metadata["role"], "assistant")
        self.assertIn("answer-one", events[1].source)
        with MemoryStore(self.config["db"]) as store:
            self.assertEqual(store.list_assertions(), [])

    def test_native_final_answer_phase_is_captured_before_task_complete_is_appended(self):
        self.turn("one", "Уточни режим.", "Отключать свет с 23 до 6?", channel=None, completion=False)
        self.rows[-1]["payload"]["phase"] = "final_answer"
        self.write()
        desktop.handle_event(self.raw("one"), self.config)
        self.assertEqual([(row.actor, row.prompt) for row in self.events()], [
            ("user", "Уточни режим."), ("assistant", "Отключать свет с 23 до 6?"),
        ])

    def test_submit_backfills_missed_stop_before_current_input_and_excludes_later_assistant(self):
        self.turn("one", "Комнаты 214 и 216 — серверные.", "Отключать остальной корпус ночью?")
        self.turn("two", "Да, кроме серверных.", "Позднее пояснение, которого ещё не было при согласии.")
        self.write()
        empty = {"view_id": "test", "evidence": [], "edges": []}
        with patch.object(desktop, "recall", return_value=empty):
            desktop.handle_event(self.raw("two", event="UserPromptSubmit", prompt="Да, кроме серверных."), self.config)
        events = self.events()
        self.assertEqual([(row.actor, row.prompt) for row in events], [
            ("user", "Комнаты 214 и 216 — серверные."),
            ("assistant", "Отключать остальной корпус ночью?"),
            ("user", "Да, кроме серверных."),
        ])

    def test_late_recovery_uses_transcript_order_and_never_future_assistant_as_context(self):
        self.turn("one", "Серверные 214 и 216 всегда с питанием.", "Остальной корпус отключать ночью?")
        self.turn("two", "Да, с этим исключением.", "Этот ответ появился после согласия.")
        self.write()
        # The prior Stop arrives after the next turn has already been captured.
        desktop.handle_event(self.raw("two"), self.config)
        desktop.handle_event(self.raw("one"), self.config)
        events = self.events()
        target = next(row for row in events if row.prompt == "Да, с этим исключением.")
        with MemoryStore(self.config["db"]) as store:
            context = CaptureQueue(store).context_events(target.id)
        self.assertEqual([(row.actor, row.prompt) for row in context], [
            ("user", "Серверные 214 и 216 всегда с питанием."),
            ("assistant", "Остальной корпус отключать ночью?"),
            ("user", "Да, с этим исключением."),
        ])

    def test_analysis_and_unconfirmed_channel_less_assistant_are_never_context(self):
        self.turn("one", "Первое сообщение.", "Внутреннее рассуждение.", channel="analysis")
        self.turn("two", "Второе сообщение.", "Неподтверждённый черновик.", channel=None, completion=False)
        self.turn("three", "Третье сообщение.", "Вызов инструмента.")
        self.rows[-2]["payload"]["recipient"] = "functions.exec"
        self.write()
        desktop.handle_event(self.raw("one"), self.config)
        desktop.handle_event(self.raw("two"), self.config)
        desktop.handle_event(self.raw("three"), self.config)
        self.assertTrue(self.events())
        self.assertTrue(all(row.actor == "user" for row in self.events()))

    def test_delegated_turn_does_not_become_human_or_assistant_dialogue(self):
        self.turn("child", "Текст дочернего агента.", "Ответ дочернего агента.", parent="root")
        self.turn("human", "Настоящий пользователь.")
        self.write()
        empty = {"view_id": "test", "evidence": [], "edges": []}
        with patch.object(desktop, "recall", return_value=empty):
            desktop.handle_event(self.raw("human", event="UserPromptSubmit", prompt="Настоящий пользователь."), self.config)
        self.assertEqual([(row.actor, row.prompt) for row in self.events()], [("user", "Настоящий пользователь.")])

    def test_byte_positions_survive_unicode_and_ignore_later_turns(self):
        self.turn("one", "Ёжик у ворот.", "Проверить доступность?")
        self.turn("two", "Да.")
        self.write()
        raw = self.raw("two", event="UserPromptSubmit", prompt="Да.")
        rows, origin = desktop._native_turn_records(raw)
        data = self.transcript.read_bytes()
        for row in rows:
            start = row["_memory_offset"]
            parsed = json.loads(data[start:data.index(b"\n", start)])
            self.assertEqual(parsed["payload"], row["payload"])
        self.assertEqual(origin["position"], next(row["_memory_offset"] for row in rows
            if row.get("type") == "response_item" and row["payload"].get("role") == "user"
            and row["payload"].get("internal_chat_message_metadata_passthrough", {}).get("turn_id") == "two"))


if __name__ == "__main__":
    unittest.main()
