"""Кейсы pylib: общей части, копия которой едет в каждый пакет при сборке.

Гоняются отдельным шагом CI (`discover -s tests/pylib`): кейсы МОДУЛЕЙ лежат в
самих модулях и находятся своим обходом, а этот код не принадлежит ни одному
модулю и без своего шага остался бы непокрытым вовсе.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "runtime", "pylib"))
sys.path.insert(0, os.path.join(ROOT, "runtime", "harness"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import claude  # noqa: E402
import jarvis  # noqa: E402
import once  # noqa: E402
import state  # noqa: E402
import trace  # noqa: E402
import decision  # noqa: E402
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

    def test_правку_входа_принимает_только_событие_до_вызова(self):
        # Спрашивается у таблицы, а не ищется маркер в её выводе: причина решения
        # со словом `updatedInput` внутри погасила бы предупреждение о потере.
        self.assertTrue(claude.accepts_input("pre-tool"))
        self.assertFalse(claude.accepts_input("post-tool"))
        self.assertFalse(claude.accepts_input("stop"))

    def test_словарь_событий_отдаётся_целиком(self):
        self.assertIn("post-tool", claude.events())
        self.assertNotIn("post_tool", claude.events())

    def test_контекст_на_событии_без_его_формы_не_печатается(self):
        # Печатать контекст туда, где харнес его не читает, значит терять его
        # молча; обёртка про такую потерю говорит вслух.
        self.assertEqual(claude.render({"event": "pre-tool"}, none("", add_context="вот")), "")
        self.assertFalse(claude.accepts_context("pre-tool"))
        self.assertTrue(claude.accepts_context("post-tool"))

    def test_дописанный_контекст_печатается_с_именем_своего_события(self):
        out = json.loads(claude.render({"event": "prompt"}, none("", add_context="вот")))
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertEqual(out["hookSpecificOutput"]["additionalContext"], "вот")


class ModeParity(unittest.TestCase):
    """`mode` читают ДВОЕ: обёртка на каждом событии и `jarvis status`. Копии
    сегодня сходятся, и разъедутся молча — `status` начнёт утверждать «on» про
    модуль, который обёртка гасит. Держим их одной матрицей, как парность ключа.

    Одним файлом их не сделать: потолок pylib — пять файлов, и все пять названы
    карточкой поимённо."""

    MATRIX = [
        # (переменная, личный конфиг, personal источника, mode манифеста, ждём)
        (None, None, None, "on", "on"),
        (None, None, None, "off", "off"),
        ("проба", None, None, "on", "off"),
        ("другой", None, None, "off", "off"),
        ("другой", "off", None, "on", "off"),
        ("all", None, None, "on", "off"),
        (None, "off", None, "on", "off"),
        (None, "on", None, "off", "on"),
        (None, None, "off", "on", "off"),
        (None, "on", "off", "on", "on"),
    ]

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.home = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "modules"))
        self.saved = {k: os.environ.get(k) for k in
                      ("HOME", "XDG_CONFIG_HOME", "JARVIS_MODULES_OFF")}
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, "config")

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def _toml(self, path, value):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('[modules]\n"проба" = "%s"\n' % value)

    def test_обёртка_и_status_отвечают_одинаково(self):
        for env, user, personal, manifest_mode, want in self.MATRIX:
            with self.subTest(env=env, user=user, personal=personal, manifest=manifest_mode):
                shutil.rmtree(os.path.join(self.home, "config"), ignore_errors=True)
                shutil.rmtree(os.path.join(self.root, "personal"), ignore_errors=True)
                if env is None:
                    os.environ.pop("JARVIS_MODULES_OFF", None)
                else:
                    os.environ["JARVIS_MODULES_OFF"] = env
                if user is not None:
                    self._toml(jarvis.user_modes_file(), user)
                if personal is not None:
                    self._toml(os.path.join(self.root, "personal", "modules.toml"), personal)

                status = jarvis.mode_of(self.root, "проба", manifest_mode)[0]
                self.assertEqual(status, want, "status разошёлся с матрицей")
                self.assertEqual(self._wrapper_says(manifest_mode), want,
                                 "обёртка разошлась со status")

    def test_без_tomllib_манифестный_режим_остаётся(self):
        # Файловые источники читать нечем (tomllib с python 3.11), и это
        # называется вслух. Но манифест при этом никуда не делся: выйди отсюда
        # «включён», и модуль, объявленный выключенным, работал бы на стоковой
        # macOS с её python 3.9.
        saved = sys.modules.get("tomllib", "нет")
        sys.modules["tomllib"] = None  # делает `import tomllib` ошибкой импорта
        err, sys.stderr = sys.stderr, io.StringIO()
        try:
            self.assertEqual(state.mode("проба", self.root, "off")[0], "off")
            self.assertEqual(state.mode("проба", self.root, "on")[0], "on")
            self.assertIn("python 3.11+", sys.stderr.getvalue())
        finally:
            sys.stderr = err
            if saved == "нет":
                del sys.modules["tomllib"]
            else:
                sys.modules["tomllib"] = saved

    def _wrapper_says(self, manifest_mode):
        """Что отвечает СГЕНЕРИРОВАННАЯ обёртка: гасит модуль или нет."""
        body = ("---\nname: проба\nkind: hook\nfor: general\n"
                "events: [{ event: post-tool }]\nrequires: []\ndata: []\nmode: %s\n---\n"
                % manifest_mode)
        directory = os.path.join(self.root, "modules", "проба")
        hooks = os.path.join(directory, "scripts", "hooks")
        os.makedirs(hooks, exist_ok=True)
        with open(os.path.join(directory, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write(body)
        with open(os.path.join(hooks, "decide.py"), "w", encoding="utf-8") as fh:
            fh.write("from decision import none\n\n\ndef decide(e, d, s):\n    return none('проба')\n")
        hook = jarvis.build(self.root, jarvis.read_modules(self.root)[0], "claude")
        state = tempfile.mkdtemp()
        event = ('{"hook_event_name":"PostToolUse","session_id":"s","tool_use_id":"t1",'
                 '"tool_name":"Bash","tool_input":{},"tool_response":{}}')
        done = subprocess.run([sys.executable, hook], input=event.encode(), capture_output=True,
                              env=dict(os.environ, CRAFT_STATE_DIR=state))
        self.assertEqual(done.returncode, 0, done.stderr.decode())
        silent = not os.path.exists(os.path.join(state, "decisions.s.jsonl"))
        shutil.rmtree(state, ignore_errors=True)
        return "off" if silent else "on"


class Capabilities(unittest.TestCase):
    """Возможности и их реализации: чем закрыта жёсткая зависимость (решения 6, 25).

    Все — на ВРЕМЕННОМ дереве корней: настоящий список источников не трогается.
    """

    ADAPTER = '''def call(event, args):
    return {"эхо": (args or {}).get("что") or (event or {}).get("что")}
'''
    BROKEN = '''def call(event, args):
    raise RuntimeError("адаптер сломался")
'''
    REFUSES = '''def call(event, args):
    return {"unsupported": "нечем разбирать"}
'''

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def package(self, name, kind="adapter", for_value="tool:проба", code=ADAPTER):
        directory = os.path.join(self.root, "modules", name)
        if code is not None:
            adapters = os.path.join(directory, "scripts", "adapters")
            os.makedirs(adapters, exist_ok=True)
            with open(os.path.join(adapters, "adapter.py"), "w", encoding="utf-8") as fh:
                fh.write(code)
        else:
            os.makedirs(directory, exist_ok=True)
        index = os.path.join(self.root, "modules.index.json")
        try:
            with open(index, "r", encoding="utf-8") as fh:
                have = json.load(fh)
        except OSError:
            have = {"root": self.root, "modules": []}
        have["modules"].append({"name": name, "kind": kind, "for": for_value})
        with open(index, "w", encoding="utf-8") as fh:
            json.dump(have, fh, ensure_ascii=False)

    def test_возможность_выводится_из_имени_и_for(self):
        # У адаптера хвост имени повторяет инструмент из `for` и снимается;
        # дефисы становятся подчёркиваниями, потому что имя едет в событие полем.
        self.assertEqual(decision.capability_of("command-tree-shell", "tool:shell"), "command_tree")
        self.assertEqual(decision.capability_of("scope-codex", "harness:codex"), "scope")
        # У самостоятельного пакета возможность — он сам.
        self.assertEqual(decision.capability_of("trace-probe", "general"), "trace_probe")
        # Хвост, не совпавший с инструментом, НЕ снимается: снимать по дефису
        # значило бы обкорнать имя, которое адаптером не является.
        self.assertEqual(decision.capability_of("changeset-review", "general"), "changeset_review")

    def test_реализация_находится_в_известном_корне(self):
        self.package("проба-проба")
        found = decision.implementations("проба", [self.root])
        self.assertEqual([m.get("name") for _, m in found], ["проба-проба"])
        self.assertEqual(decision.implementations("чего-нет", [self.root]), [])

    def test_адаптер_зовётся_и_отвечает(self):
        self.package("проба-проба")
        answer, gap = decision.call("проба", {"что": "из события"}, [self.root])
        self.assertEqual(gap, "")
        self.assertEqual(answer, {"эхо": "из события"})
        answer, gap = decision.call("проба", {}, [self.root], {"что": "из аргументов"})
        self.assertEqual(answer, {"эхо": "из аргументов"})

    def test_реализации_нет_вовсе_имя_называется(self):
        # Непокрытое называется ИМЕНЕМ ВОЗМОЖНОСТИ (решение 14), а не молчанием.
        answer, gap = decision.call("command_tree", {}, [self.root])
        self.assertIsNone(answer)
        self.assertEqual(gap, "command_tree")

    def test_отказ_адаптера_едет_с_текстом(self):
        self.package("проба-проба", code=self.REFUSES)
        answer, gap = decision.call("проба", {}, [self.root])
        self.assertIsNone(answer)
        self.assertEqual(gap, "нечем разбирать")

    def test_падение_адаптера_называется_словами(self):
        # Падение адаптера ответа модуля не меняет (решение 25), но и выдумывать
        # значение вместо него нельзя: причина едет наверх текстом.
        self.package("проба-проба", code=self.BROKEN)
        answer, gap = decision.call("проба", {}, [self.root])
        self.assertIsNone(answer)
        self.assertIn("адаптер сломался", gap)

    def test_возможность_закрытая_НЕ_адаптером_звать_нечего(self):
        # Пакет закрывает возможность собой, адаптера у него нет: звать некого, и
        # это НЕ `unsupported` — зависимость закрыта установкой.
        self.package("проба", kind="hook", for_value="general", code=None)
        answer, gap = decision.call("проба", {}, [self.root])
        self.assertIsNone(answer)
        self.assertEqual(gap, "")

    def test_адаптер_объявлен_а_кода_нет_называется(self):
        # Пакет объявил себя АДАПТЕРОМ, а кода у него нет: это сломанная
        # установка, а не «возможность закрыта самим пакетом». Промолчать значило
        # бы отдать модулю пустоту вместо значения — то есть пропуск без имени.
        self.package("проба-проба", code=None)
        answer, gap = decision.call("проба", {}, [self.root])
        self.assertIsNone(answer)
        self.assertIn("проба-проба", gap)

    def test_свой_корень_первым_и_без_повторов(self):
        share = os.path.join(self.root, "share")
        os.makedirs(os.path.join(share, "jarvis"))
        with open(os.path.join(share, "jarvis", "sources.list"), "w", encoding="utf-8") as fh:
            fh.write("/чужой/корень\n%s\n" % self.root)
        было = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = share
        try:
            roots = state.source_roots(self.root)
        finally:
            os.environ.pop("XDG_DATA_HOME") if было is None else os.environ.update(XDG_DATA_HOME=было)
        self.assertEqual(roots, [self.root, "/чужой/корень"],
                         "свой корень первый, и списком он не удваивается")

    def test_без_списка_корней_свой_всё_равно_виден(self):
        # Прогон под чужим домом (кейсы, фикстура) списка не находит — а сосед,
        # лежащий рядом, обязан находиться, иначе зависимость читалась бы
        # незакрытой там, где всё на месте.
        было = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.root, "нет-такого")
        try:
            self.assertEqual(state.source_roots(self.root), [self.root])
        finally:
            os.environ.pop("XDG_DATA_HOME") if было is None else os.environ.update(XDG_DATA_HOME=было)


class SpareChannel(unittest.TestCase):
    """Запасной путь строки следа: журнал решений не пишется — строка идёт в
    журнал метрик, туда же, откуда её возьмёт свёртка."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def event(self, decisions):
        return {
            "session_id": "s", "call_id": "c", "event": "pre-tool",
            "decision_log": decisions,
            "metrics_log": os.path.join(self.dir, "metrics.s.jsonl"),
        }

    def test_журнал_решений_пишется_запасной_не_трогается(self):
        ok = trace.write(self.event(os.path.join(self.dir, "decisions.s.jsonl")), "проба", deny("причина"))
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "decisions.s.jsonl")))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "metrics.s.jsonl")),
                         "пока основной путь работает, запасной молчит")

    def test_неписучий_журнал_решений_НЕ_теряет_отказ(self):
        # Молчание на месте отказа читается как проход, и сводка недосчиталась бы
        # отказа пакета, тогда как отказ соседнего JS-хука уцелел бы: у него
        # запасной путь есть с самого начала.
        ok = trace.write(self.event("/proc/нет-такого-каталога/decisions.jsonl"), "проба", deny("причина"))
        self.assertTrue(ok)
        with open(os.path.join(self.dir, "metrics.s.jsonl"), "r", encoding="utf-8") as fh:
            record = json.loads(fh.read().strip())
        self.assertEqual(record["outcome"], "deny")
        self.assertEqual(record["hook"], "проба")

    def test_оба_журнала_недоступны_ответ_честный(self):
        event = self.event("/proc/нет-такого/decisions.jsonl")
        event["metrics_log"] = "/proc/нет-такого/metrics.jsonl"
        self.assertFalse(trace.write(event, "проба", deny("причина")))
