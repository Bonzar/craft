"""Кейсы pylib: общей части, копия которой едет в каждый пакет при сборке.

Гоняются отдельным шагом CI (`discover -s tests/pylib`): кейсы МОДУЛЕЙ лежат в
самих модулях и находятся своим обходом, а этот код не принадлежит ни одному
модулю и без своего шага остался бы непокрытым вовсе.
"""

import json
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "runtime", "pylib"))
sys.path.insert(0, os.path.join(ROOT, "runtime", "harness"))

import claude  # noqa: E402
import once  # noqa: E402
import state  # noqa: E402
import trace  # noqa: E402
from decision import allow, block, deny, missing_fact, none, unsupported  # noqa: E402
from key import event_key  # noqa: E402


class Key(unittest.TestCase):
    def test_идентификатор_вызова_сильнее_байтов(self):
        self.assertEqual(event_key("toolu_1", b'{"a":1}'), "toolu_1")

    def test_без_идентификатора_хеш_шестнадцати_знаков(self):
        key = event_key("", b'{"a":1}')
        self.assertEqual(len(key), 16)
        self.assertEqual(key, event_key("   ", b'{"a":1}'), "пробелы идентификатором не считаются")

    def test_разные_байты_разные_ключи(self):
        self.assertNotEqual(event_key("", b"a"), event_key("", b"b"))


class Decision(unittest.TestCase):
    def test_исходы_несут_имя_и_причину(self):
        self.assertEqual(deny("нельзя"), {"outcome": "deny", "reason": "нельзя"})
        self.assertEqual(allow(), {"outcome": "allow", "reason": ""})

    def test_none_без_контекста_ключа_не_заводит(self):
        # Пустой `add_context` печатался бы харнесу пустой директивой.
        self.assertNotIn("add_context", none("просто след"))
        self.assertEqual(none("", add_context="текст")["add_context"], "текст")

    def test_block_несёт_сообщение_отдельно_от_причины(self):
        self.assertEqual(block("почему", "что сказать агенту")["message"], "что сказать агенту")

    def test_недостающий_факт_называется_именем(self):
        self.assertEqual(missing_fact({"tokens": None}, ["tokens"]), "tokens")
        self.assertEqual(missing_fact({"tokens": {"input": 0}}, ["tokens"]), "")
        self.assertEqual(missing_fact({"journal": ""}, ["journal"]), "journal",
                         "пустая строка — это отсутствие пути, а не путь")
        self.assertEqual(missing_fact({}, []), "")

    def test_unsupported_называет_чего_не_хватило(self):
        self.assertIn("tokens", unsupported("tokens")["reason"])


class State(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_json_туда_и_обратно(self):
        path = os.path.join(self.dir, "s.json")
        self.assertTrue(state.write_json(path, {"a": 1}))
        self.assertEqual(state.read_json(path), {"a": 1})

    def test_испорченный_json_даёт_умолчание_а_не_падение(self):
        path = os.path.join(self.dir, "bad.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{не json")
        self.assertEqual(state.read_json(path, default={"пусто": True}), {"пусто": True})

    def test_jsonl_пишется_без_пробелов_как_у_js(self):
        # Журнал решений общий с JS-хуками: строка пакета обязана выглядеть так
        # же, как соседняя, иначе читатели журнала расходятся на пробелах.
        path = os.path.join(self.dir, "log.jsonl")
        state.append_jsonl(path, {"a": 1, "b": "два"})
        with open(path, "r", encoding="utf-8") as fh:
            line = fh.read().strip()
        self.assertEqual(line, json.dumps({"a": 1, "b": "два"}, ensure_ascii=False, separators=(",", ":")))
        self.assertEqual(state.read_jsonl(path), [{"a": 1, "b": "два"}])

    def test_непишущийся_путь_говорит_нет_а_не_падает(self):
        self.assertFalse(state.append_jsonl("/proc/нет-такого/log.jsonl", {"a": 1}))
        self.assertFalse(state.write_json("/proc/нет-такого/s.json", {"a": 1}))

    def test_лок_не_достаётся_второму(self):
        path = os.path.join(self.dir, "locked")
        with state.Lock(path, wait_ms=10) as first:
            self.assertTrue(first.taken)
            with state.Lock(path, wait_ms=10) as second:
                self.assertFalse(second.taken, "занятый лок не выдаётся дважды")
        with state.Lock(path, wait_ms=10) as third:
            self.assertTrue(third.taken, "после выхода лок свободен")


class Once(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def event(self, **over):
        base = {"event": "post-tool", "call_id": "toolu_1", "key": "toolu_1"}
        base.update(over)
        return base

    def test_второй_вызов_того_же_события_уступает(self):
        self.assertTrue(once.take("m", self.event(), self.dir))
        self.assertFalse(once.take("m", self.event(), self.dir))

    def test_событие_после_вызова_не_гасится_меткой_события_до_него(self):
        # Идентификатор вызова у обоих событий ОДИН, и ключ у них один. Без имени
        # события в метке модуль терял бы половину событий молча.
        self.assertTrue(once.take("m", self.event(event="pre-tool"), self.dir))
        self.assertTrue(once.take("m", self.event(event="post-tool"), self.dir))

    def test_соседний_модуль_не_занимает_чужое_событие(self):
        self.assertTrue(once.take("m", self.event(), self.dir))
        self.assertTrue(once.take("другой", self.event(), self.dir))

    def test_событие_без_идентификатора_протухает_за_секунды(self):
        event = self.event(call_id="", key="хеш")
        self.assertTrue(once.take("m", event, self.dir))
        self.assertFalse(once.take("m", event, self.dir))
        mark = [n for n in os.listdir(self.dir) if n.startswith(once.MARK_PREFIX) and not n.endswith("sweep")][0]
        old = time.time() - once.CONTENT_TTL_S - 1
        os.utime(os.path.join(self.dir, mark), (old, old))
        self.assertTrue(once.take("m", event, self.dir), "то же сообщение позже — уже другое событие")

    def test_без_ключа_работаем_а_не_уступаем(self):
        # Отличить второй вызов от следующего события нечем, и уступка молча
        # гасила бы работу.
        self.assertTrue(once.take("m", self.event(call_id="", key=""), self.dir))
        self.assertTrue(once.take("m", self.event(call_id="", key=""), self.dir))


class Trace(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.log = os.path.join(self.dir, "decisions.sid.jsonl")
        self.event = {
            "session_id": "sid", "call_id": "toolu_1", "event": "pre-tool",
            "key": "toolu_1", "decision_log": self.log,
        }

    def test_строка_повторяет_формат_журнала_решений(self):
        self.assertTrue(trace.write(self.event, "guard", deny("нельзя")))
        line = state.read_jsonl(self.log)[0]
        self.assertEqual(sorted(line), sorted(
            ["kind", "ts", "key", "sid", "call_id", "event", "hook", "outcome", "class"]))
        self.assertEqual(line["kind"], "decision")
        self.assertEqual(line["key"], "toolu_1")
        self.assertEqual(line["hook"], "guard")

    def test_текста_причины_в_журнале_нет(self):
        # Журнал переживает сессию, а причина отказа содержит куски работы Влада.
        trace.write(self.event, "guard", deny("секретная причина"))
        with open(self.log, "r", encoding="utf-8") as fh:
            self.assertNotIn("секретная причина", fh.read())

    def test_класс_только_у_исключающих_исходов(self):
        self.assertEqual(trace.line(self.event, "guard", deny("x"))["class"], "guard")
        self.assertEqual(trace.line(self.event, "guard", block("x"))["class"], "guard")
        self.assertEqual(trace.line(self.event, "probe", none("x"))["class"], "")

    def test_непокрытое_видно_классом_а_не_молчанием(self):
        # Исход `none` верен: модуль ничего не запретил. Но без имени в классе
        # `unsupported` в журнале неотличим от молчащего модуля.
        line = trace.line(self.event, "guard", unsupported("tokens"))
        self.assertEqual(line["outcome"], "none")
        self.assertEqual(line["class"], "unsupported:tokens")

    def test_недоставленное_решение_видно_классом(self):
        # Отказ, которому на этом событии нет формы, харнес не показал. Встань он
        # в счёт наравне с доехавшими — сводка считала бы отказ, которого никто
        # не видел, а это ровно та ложь, от которой заведён весь канал.
        line = trace.line(self.event, "guard", deny("нельзя"), delivered=False)
        self.assertEqual(line["outcome"], "deny")
        self.assertEqual(line["class"], "undelivered:deny")
        self.assertEqual(trace.line(self.event, "guard", deny("нельзя"))["class"], "guard")

    def test_без_пути_журнала_говорит_нет(self):
        self.assertFalse(trace.write({"key": "k"}, "guard", none("x")))


class ClaudeTable(unittest.TestCase):
    def test_имена_событий_переводятся_в_канонические(self):
        event = claude.to_event(b'{"hook_event_name":"PostToolUse","tool_use_id":"t1","tool_name":"Bash"}')
        self.assertEqual(event["event"], "post-tool")
        self.assertEqual(event["call_id"], "t1")
        self.assertEqual(event["key"], "t1")

    def test_незнакомое_имя_даёт_пустое_а_не_догадку(self):
        self.assertEqual(claude.to_event('{"hook_event_name":"ЧтоТоНовое"}'.encode())["event"], "")

    def test_неразборное_событие_не_роняет_обёртку(self):
        event = claude.to_event("не json".encode())
        self.assertEqual(event["event"], "")
        self.assertEqual(event["input"], {})

    def test_путь_журнала_решений_повторяет_формулу_слоя(self):
        os.environ["CRAFT_STATE_DIR"] = "/тмп"
        os.environ.pop("CRAFT_DECISION_LOG", None)
        try:
            event = claude.to_event(b'{"hook_event_name":"Stop","session_id":"s1"}')
            self.assertEqual(event["decision_log"], os.path.join("/тмп", "decisions.s1.jsonl"))
            os.environ["CRAFT_DECISION_LOG"] = "/тмп/своё.jsonl"
            self.assertEqual(claude.to_event(b'{"session_id":"s1"}')["decision_log"], "/тмп/своё.jsonl")
        finally:
            os.environ.pop("CRAFT_STATE_DIR", None)
            os.environ.pop("CRAFT_DECISION_LOG", None)

    def test_рендер_молчит_на_none_без_контекста(self):
        event = {"event": "post-tool"}
        self.assertEqual(claude.render(event, none("проба")), "")

    def test_рендер_отказа_несёт_форму_харнеса(self):
        out = json.loads(claude.render({"event": "pre-tool"}, deny("нельзя")))
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecisionReason"], "нельзя")

    def test_рендер_блокировки_конца_хода(self):
        out = json.loads(claude.render({"event": "stop"}, block("почему", "агенту")))
        self.assertEqual(out, {"decision": "block", "reason": "агенту"})

    def test_отказ_на_чужом_событии_формы_не_получает(self):
        # Харнес сверяет имя события в ответе с тем, на которое подписан модуль, и
        # чужую форму молча выбрасывает. Подставить сюда PreToolUse значило бы
        # потерять решение, оставив след, — сводка посчитала бы отказ, которого
        # никто не видел.
        self.assertEqual(claude.render({"event": "post-tool"}, deny("нельзя")), "")
        self.assertEqual(claude.render({"event": "prompt"}, block("почему")), "")

    def test_словарь_событий_отдаётся_целиком(self):
        self.assertIn("post-tool", claude.events())
        self.assertNotIn("post_tool", claude.events())

    def test_дописанный_контекст_печатается_с_именем_своего_события(self):
        out = json.loads(claude.render({"event": "prompt"}, none("", add_context="вот")))
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertEqual(out["hookSpecificOutput"]["additionalContext"], "вот")
