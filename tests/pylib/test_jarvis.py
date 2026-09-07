"""Кейсы установщика: разбор манифеста, находки `check`, сборка и регистрация.

Все — на ВРЕМЕННОМ дереве: настоящие ~/.claude, список корней и modules/ репы
кейсами не трогаются.
"""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import jarvis  # noqa: E402

ADDON = """---
name: %s
kind: hook
for: general
events: [{ event: post-tool }]
requires: [%s]
data: []
mode: on
---

Добавка.
"""

MANIFEST = """---
name: %s
kind: %s
for: %s
events: [{ event: post-tool }]
requires: []
data: []
mode: on
---

Тело для человека.
"""


DECIDE = '''from decision import none


def decide(event, data_files, state_dir):
    return none("проба")
'''


def make_module(root, name, kind="hook", for_value="general", body=None, code=False):
    """Пакет на диске. `code=True` кладёт и работающую логику: без неё обёртка
    падает на импорте, и кейс про молчание зеленел бы от поломки, а не от режима."""
    directory = os.path.join(root, "modules", name)
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write(body if body is not None else MANIFEST % (name, kind, for_value))
    if code:
        hooks = os.path.join(directory, "scripts", "hooks")
        os.makedirs(hooks, exist_ok=True)
        with open(os.path.join(hooks, "decide.py"), "w", encoding="utf-8") as fh:
            fh.write(DECIDE)
    return directory


class Frontmatter(unittest.TestCase):
    def test_скаляры_и_поточные_списки(self):
        manifest = jarvis.parse_frontmatter(MANIFEST % ("trace-probe", "hook", "general"))
        self.assertEqual(manifest["name"], "trace-probe")
        self.assertEqual(manifest["events"], [{"event": "post-tool"}])
        self.assertEqual(manifest["requires"], [])
        self.assertEqual(manifest["mode"], "on", "`on` — слово, а не булево")

    def test_голое_имя_события_читается_наравне_с_картой(self):
        text = "---\nname: x\nevents: [post-tool, stop]\n---\n"
        self.assertEqual(jarvis.event_names(jarvis.parse_frontmatter(text)), ["post-tool", "stop"])

    def test_комментарий_обрезается_а_поточный_список_нет(self):
        text = "---\nfor: tool:git  # адаптер\nevents: [{ event: post-tool }]\n---\n"
        manifest = jarvis.parse_frontmatter(text)
        self.assertEqual(manifest["for"], "tool:git")
        self.assertEqual(manifest["events"], [{"event": "post-tool"}])

    def test_нет_фронтматтера_ошибка_а_не_пустой_манифест(self):
        with self.assertRaises(ValueError):
            jarvis.parse_frontmatter("# просто заголовок\n")
        with self.assertRaises(ValueError):
            jarvis.parse_frontmatter("---\nname: x\n")

    def test_незакрытый_список_ошибка_а_не_строка(self):
        # Комментарий после поточного списка съедал закрывающую скобку, и значение
        # оставалось строкой; `event_names` перебирал её ПОСИМВОЛЬНО, а модуль
        # ставился и молчал.
        with self.assertRaises(ValueError):
            jarvis.parse_frontmatter("---\nevents: [{ event: post-tool }]  # хвост\n---\n")


class Check(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "modules"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def found(self):
        return jarvis.check(self.root, jarvis.read_modules(self.root))

    def test_согласованное_дерево_находок_не_даёт(self):
        make_module(self.root, "trace-probe")
        self.assertEqual(self.found(), [])

    def test_имя_не_соответствует_for(self):
        make_module(self.root, "changeset", for_value="tool:git")
        self.assertTrue(any("не соответствует for" in line for line in self.found()), self.found())

    def test_адаптер_с_правильным_хвостом_проходит(self):
        make_module(self.root, "changeset-git", kind="adapter", for_value="tool:git")
        self.assertEqual(self.found(), [])

    def test_general_с_хвостом_инструмента_допустим_пока_такого_for_нет(self):
        # `for: general` запрещает не дефис, а ХВОСТ инструмента из своего `for`:
        # у general инструмента нет, поэтому проверять нечего — эту дыру закрывает
        # правило про добавку без базы.
        make_module(self.root, "trace-probe")
        self.assertEqual(self.found(), [])

    def test_пакет_в_манифесте_источника_без_папки(self):
        # Манифест источника — картина ПРОШЛОЙ установки: по нему модули узнают,
        # что лежит в чужом корне. Снесённая руками папка делает его враньём.
        make_module(self.root, "исчезнет")
        jarvis.write_index(self.root, jarvis.read_modules(self.root))
        shutil.rmtree(os.path.join(self.root, "modules", "исчезнет"))
        found = jarvis.check(self.root, jarvis.read_modules(self.root))
        self.assertTrue(any("папки нет" in line for line in found), found)
        self.assertEqual(jarvis.check(self.root, jarvis.read_modules(self.root), index=False), [],
                         "установке это не находка: она манифест источника и переписывает")

    def test_два_пакета_с_одним_именем(self):
        make_module(self.root, "первый")
        make_module(self.root, "второй", body=MANIFEST % ("первый", "hook", "general"))
        self.assertTrue(any("два пакета с одним именем" in line for line in self.found()), self.found())

    def test_добавка_называет_свою_базу_и_проходит(self):
        make_module(self.root, "changeset")
        make_module(self.root, "changeset-review",
                    body=ADDON % ("changeset-review", "changeset"))
        self.assertEqual(self.found(), [], "база названа — чтение не догадка")

    def test_имя_начинается_с_имени_пакета_а_базы_не_названо(self):
        # Ровно коллизия решения 18: `changeset-review` читается добавкой к
        # `changeset`, но сам себя добавкой не объявлял. Либо назови базу, либо
        # переименуйся — молчать тут нельзя, иначе имя базы стало началом имени
        # другого пакета незаметно.
        make_module(self.root, "changeset")
        make_module(self.root, "changeset-review")
        found = self.found()
        self.assertTrue(any("базы в requires нет" in line for line in found), found)

    def test_самостоятельный_пакет_с_дефисом_находки_не_даёт(self):
        # Пока пакета `trace` нет, `trace-probe` — обычная база с дефисом в имени.
        make_module(self.root, "trace-probe")
        self.assertEqual(self.found(), [])

    def test_добавка_объявила_несколько_баз(self):
        make_module(self.root, "changeset")
        make_module(self.root, "changeset-review",
                    body=ADDON % ("changeset-review", "changeset"))
        make_module(self.root, "changeset-review-extra",
                    body=("---\nname: changeset-review-extra\nkind: hook\nfor: general\n"
                          "events: [{ event: post-tool }]\n"
                          "requires: [changeset, changeset-review]\ndata: []\nmode: on\n---\n"))
        found = self.found()
        self.assertTrue(any("сразу несколько баз" in line for line in found), found)

    def test_хвост_имени_харнеса_при_general_находка(self):
        make_module(self.root, "scope-claude")
        found = self.found()
        self.assertTrue(any("имя харнеса" in line for line in found), found)

    def test_добавка_без_базы_видна_незакрытой_зависимостью(self):
        base = "---\nname: %s\nkind: hook\nfor: general\nevents: [{ event: post-tool }]\n"
        make_module(self.root, "база")
        make_module(self.root, "база-тема",
                    body=base % "база-тема" + "requires: [база]\ndata: []\nmode: on\n---\n")
        self.assertEqual(self.found(), [], "база на месте — зависимость закрыта")
        shutil.rmtree(os.path.join(self.root, "modules", "база"))
        found = self.found()
        self.assertTrue(any("ничем не закрыта" in line for line in found), found)

    def test_факт_события_зависимостью_к_пакету_не_считается(self):
        # Факт закрывает ОБЁРТКА, а не пакет: искать под него пакет — не то. Но и
        # молчать нельзя: ни одна таблица харнеса этих фактов пока не выдаёт, и
        # модуль встал бы, отвечая `unsupported` на каждом событии.
        base = "---\nname: x\nkind: hook\nfor: general\nevents: [{ event: stop }]\n"
        make_module(self.root, "x", body=base + "requires: [tokens]\ndata: []\nmode: on\n---\n")
        found = self.found()
        self.assertTrue(any("не выдаёт ни одна таблица харнеса" in line for line in found), found)
        self.assertFalse(any("ничем не закрыта" in line for line in found),
                         "факт не ищется среди пакетов")

    def test_неразобранный_манифест_называет_пакет_а_не_роняет_обход(self):
        make_module(self.root, "хороший")
        directory = os.path.join(self.root, "modules", "плохой")
        os.makedirs(directory)
        with open(os.path.join(directory, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nсломано\n---\n")
        found = self.found()
        self.assertTrue(any(line.startswith("плохой: манифест не разобран") for line in found), found)
        self.assertFalse(any(line.startswith("хороший") for line in found),
                         "соседний пакет от этого не страдает")

    def test_вид_не_из_списка(self):
        make_module(self.root, "x", kind="что-то")
        self.assertTrue(any("не из списка" in line for line in self.found()), self.found())


class Install(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.home = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "modules"))
        for name in ("runtime", "tools"):
            shutil.copytree(os.path.join(ROOT, name), os.path.join(self.root, name),
                            ignore=shutil.ignore_patterns("__pycache__"))
        make_module(self.root, "проба")
        self.saved = {k: os.environ.get(k) for k in
                      ("HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "JARVIS_MODULES_OFF")}
        os.environ["HOME"] = self.home
        os.environ["XDG_DATA_HOME"] = os.path.join(self.home, "share")
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, "config")
        os.environ.pop("JARVIS_MODULES_OFF", None)

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def install(self):
        return jarvis.main(["--root", self.root, "install"])

    def settings(self):
        with open(os.path.join(self.home, ".claude", "settings.json"), "r", encoding="utf-8") as fh:
            return json.load(fh)

    def commands(self, settings, event):
        return [entry.get("command", "")
                for group in settings.get("hooks", {}).get(event, [])
                for entry in group.get("hooks", [])]

    def test_ставит_и_не_двоит(self):
        self.assertEqual(self.install(), 0)
        hook = os.path.join(self.root, "modules", "проба", "dist", "claude", "hook.py")
        self.assertTrue(os.path.isfile(hook))
        line = "python3 " + shlex.quote(hook)
        self.assertEqual(self.commands(self.settings(), "PostToolUse"), [line])
        self.install()
        self.assertEqual(self.commands(self.settings(), "PostToolUse"), [line])

    def test_путь_с_пробелом_экранируется_и_снимается(self):
        # Харнес исполняет строку регистрации оболочкой: неэкранированный путь с
        # пробелом дал бы неработающую строку молча, а сверка по подстроке не
        # узнала бы её обратно и оставила бы мусор в настройках.
        spaced = os.path.join(self.root, "с пробелом")
        os.makedirs(os.path.join(spaced, "modules"))
        shutil.copytree(os.path.join(self.root, "modules", "проба"),
                        os.path.join(spaced, "modules", "проба"))
        shutil.copytree(os.path.join(self.root, "runtime"), os.path.join(spaced, "runtime"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.assertEqual(jarvis.main(["--root", spaced, "install"]), 0)
        hook = os.path.join(spaced, "modules", "проба", "dist", "claude", "hook.py")
        self.assertEqual(self.commands(self.settings(), "PostToolUse"),
                         ["python3 " + shlex.quote(hook)])
        shutil.rmtree(os.path.join(spaced, "modules", "проба"))
        jarvis.main(["--root", spaced, "install"])
        self.assertEqual(self.commands(self.settings(), "PostToolUse"), [],
                         "своя строка узнана обратно и снята")

    def test_off_не_трогает_одноимённый_ключ_чужой_секции(self):
        path = jarvis.user_modes_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('[modules]\n"проба" = "on"\n\n[scripts]\n"проба" = "мой-путь"\n')
        jarvis.main(["--root", self.root, "off", "проба"])
        with open(path, "r", encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn('[scripts]\n"проба" = "мой-путь"', body, "чужая секция цела")
        self.assertEqual(jarvis.mode_of(self.root, "проба")[0], "off")

    def test_заголовок_секции_с_комментарием_не_двоится(self):
        # По точному совпадению строки заголовок не находился, и в файл ложился
        # ВТОРОЙ `[modules]` — после чего tomllib его не разбирает вовсе, и оба
        # источника режима замолкают.
        path = jarvis.user_modes_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('[modules]  # мои модули\n')
        jarvis.main(["--root", self.root, "off", "проба"])
        with open(path, "r", encoding="utf-8") as fh:
            body = fh.read()
        self.assertEqual(body.count("[modules]"), 1, body)
        self.assertEqual(jarvis.mode_of(self.root, "проба")[0], "off")

    def test_чужая_запись_не_страдает(self):
        os.makedirs(os.path.join(self.home, ".claude"))
        with open(os.path.join(self.home, ".claude", "settings.json"), "w", encoding="utf-8") as fh:
            json.dump({"hooks": {"PostToolUse": [{"hooks": [
                {"type": "command", "command": "/opt/чужой.sh"}]}]}}, fh)
        self.install()
        self.assertIn("/opt/чужой.sh", self.commands(self.settings(), "PostToolUse"))

    def test_снятый_из_дерева_модуль_снимается_и_из_регистраций(self):
        self.install()
        shutil.rmtree(os.path.join(self.root, "modules", "проба"))
        self.install()
        self.assertEqual(self.commands(self.settings(), "PostToolUse"), [])

    def test_убранное_из_манифеста_событие_снимается_из_регистраций(self):
        # Сверять с объединением по ВСЕМ событиям нельзя: команда, оставшаяся
        # нужной на одном событии, переживала бы чистку на каждом, и модуль
        # запускался бы на снятом событии до конца жизни настроек.
        two = ("---\nname: проба\nkind: hook\nfor: general\n"
               "events: [{ event: post-tool }, { event: pre-tool }]\n"
               "requires: []\ndata: []\nmode: on\n---\n")
        make_module(self.root, "проба", body=two)
        self.install()
        self.assertEqual(len(self.commands(self.settings(), "PreToolUse")), 1)
        self.assertEqual(len(self.commands(self.settings(), "PostToolUse")), 1)
        make_module(self.root, "проба")  # снова одно событие: post-tool
        self.install()
        self.assertEqual(self.commands(self.settings(), "PreToolUse"), [],
                         "снятое из манифеста событие осталось зарегистрированным")
        self.assertEqual(len(self.commands(self.settings(), "PostToolUse")), 1,
                         "оставшееся событие не пострадало")

    def test_чужой_hooks_не_объект_отказ_словами(self):
        settings = os.path.join(self.home, ".claude", "settings.json")
        os.makedirs(os.path.dirname(settings), exist_ok=True)
        with open(settings, "w", encoding="utf-8") as fh:
            fh.write('{"hooks": ["не объект"]}')
        with self.assertRaises(ValueError):
            jarvis.register(self.root, jarvis.read_modules(self.root), "claude")
        with open(settings, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), '{"hooks": ["не объект"]}', "файл не тронут")

    def test_список_корней_без_хвостового_перевода_не_склеивается(self):
        path = jarvis.sources_list()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("/чужой/корень")  # руками, без перевода строки
        jarvis.add_source(self.root)
        self.assertEqual(jarvis.read_sources(), ["/чужой/корень", self.root])

    def test_корень_дописывается_в_список_один_раз(self):
        self.install()
        self.install()
        with open(jarvis.sources_list(), "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read().split(), [self.root])

    def test_манифест_источника_описывает_дерево(self):
        self.install()
        with open(jarvis.index_path(self.root), "r", encoding="utf-8") as fh:
            index = json.load(fh)
        self.assertEqual(index["root"], self.root)
        self.assertEqual([m["name"] for m in index["modules"]], ["проба"])
        self.assertNotIn("dir", index["modules"][0], "состояния и служебного в манифесте источника нет")

    def test_дерево_с_находками_не_ставится(self):
        make_module(self.root, "changeset", for_value="tool:git")
        self.assertEqual(self.install(), 1)

    def test_off_не_склеивает_чужой_конфиг(self):
        path = jarvis.user_modes_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('[other]\nkey = "value"\n\n[modules]\n')
        jarvis.main(["--root", self.root, "off", "проба"])
        with open(path, "r", encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("\n\n[modules]", body, "пустая строка между секциями цела")
        self.assertIn('key = "value"', body, "чужая секция цела")
        self.assertEqual(jarvis.mode_of(self.root, "проба")[0], "off")

    def test_mode_старшинство_переменная_потом_конфиг(self):
        self.install()
        self.assertEqual(jarvis.mode_of(self.root, "проба")[0], "on")
        jarvis.main(["--root", self.root, "off", "проба"])
        mode, source = jarvis.mode_of(self.root, "проба")
        self.assertEqual(mode, "off")
        self.assertEqual(source, jarvis.user_modes_file())
        os.environ["JARVIS_MODULES_OFF"] = "какой-то-другой"
        self.assertEqual(jarvis.mode_of(self.root, "проба"), ("on", "JARVIS_MODULES_OFF"),
                         "переменная старше конфига и отвечает за все модули сразу")
        os.environ.pop("JARVIS_MODULES_OFF")
        jarvis.main(["--root", self.root, "on", "проба"])
        self.assertEqual(jarvis.mode_of(self.root, "проба")[0], "on")

class EventNames(unittest.TestCase):
    """Имя события в манифесте сверяется со словарём харнеса. Без этой находки
    модуль ставится, обёртка собирается, регистрация не пишется — и он молчит
    навсегда."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "modules"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def found(self):
        return jarvis.check(self.root, jarvis.read_modules(self.root))

    def test_каноническое_имя_проходит(self):
        make_module(self.root, "x")
        self.assertEqual(self.found(), [])

    def test_подчёркивание_вместо_дефиса_находка(self):
        body = MANIFEST % ("x", "hook", "general")
        make_module(self.root, "x", body=body.replace("post-tool", "post_tool"))
        found = self.found()
        self.assertTrue(any("харнесу неизвестно" in line for line in found), found)

    def test_выдуманное_событие_находка(self):
        body = MANIFEST % ("x", "hook", "general")
        make_module(self.root, "x", body=body.replace("post-tool", "когда-нибудь-потом"))
        self.assertTrue(any("харнесу неизвестно" in line for line in self.found()))


class Wrapper(unittest.TestCase):
    """В обёртку из `requires` едут только ФАКТЫ события: имя возможности задать
    событию нельзя, и модуль отвечал бы `unsupported` на каждом событии."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "modules"))
        for name in ("runtime",):
            shutil.copytree(os.path.join(ROOT, name), os.path.join(self.root, name),
                            ignore=shutil.ignore_patterns("__pycache__"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_возможность_из_requires_в_обёртку_не_едет(self):
        body = ("---\nname: база-тема\nkind: hook\nfor: general\n"
                "events: [{ event: post-tool }]\nrequires: [база, tokens]\ndata: []\nmode: on\n---\n")
        make_module(self.root, "база-тема", body=body)
        hook = jarvis.build(self.root, jarvis.read_modules(self.root)[0], "claude")
        with open(hook, "r", encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("FACTS = ['tokens']", text)
        self.assertNotIn("база", text.split("DATA_FILES")[0].split("FACTS =")[1])


class CodexFindings(unittest.TestCase):
    """Три находки ревью Codex: зависимость из чужого корня, режим из манифеста,
    неразборные настройки харнеса."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.other = tempfile.mkdtemp()
        self.home = tempfile.mkdtemp()
        for base in (self.root, self.other):
            os.makedirs(os.path.join(base, "modules"))
        for name in ("runtime",):
            shutil.copytree(os.path.join(ROOT, name), os.path.join(self.root, name),
                            ignore=shutil.ignore_patterns("__pycache__"))
        self.saved = {k: os.environ.get(k) for k in
                      ("HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "JARVIS_MODULES_OFF")}
        os.environ["HOME"] = self.home
        os.environ["XDG_DATA_HOME"] = os.path.join(self.home, "share")
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, "config")
        os.environ.pop("JARVIS_MODULES_OFF", None)

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for base in (self.root, self.other, self.home):
            shutil.rmtree(base, ignore_errors=True)

    def test_зависимость_закрывает_пакет_чужого_корня(self):
        # Командный пресет и дерево коллеги — такие же источники (решение 12).
        # Смотри только своё дерево — межкорневая зависимость читалась бы
        # незакрытой, и установка отказывала бы там, где всё на месте.
        make_module(self.other, "база")
        jarvis.write_index(self.other, jarvis.read_modules(self.other))
        jarvis.add_source(self.other)
        body = ("---\nname: база-тема\nkind: hook\nfor: general\n"
                "events: [{ event: post-tool }]\nrequires: [база]\ndata: []\nmode: on\n---\n")
        make_module(self.root, "база-тема", body=body)
        modules = jarvis.read_modules(self.root)
        self.assertTrue(any("ничем не закрыта" in line for line in jarvis.check(self.root, modules)),
                        "без чужих корней зависимость не закрыта")
        self.assertEqual(jarvis.check(self.root, modules, known=jarvis.known_names(self.root)), [],
                         "с известными корнями — закрыта")

    def test_режим_из_манифеста_гасит_пакет(self):
        body = ("---\nname: спящий\nkind: hook\nfor: general\n"
                "events: [{ event: post-tool }]\nrequires: []\ndata: []\nmode: off\n---\n")
        make_module(self.root, "спящий", body=body, code=True)
        self.assertEqual(jarvis.main(["--root", self.root, "install"]), 0)
        self.assertEqual(jarvis.mode_of(self.root, "спящий", "off"), ("off", "манифест"))
        hook = os.path.join(self.root, "modules", "спящий", "dist", "claude", "hook.py")
        with open(hook, "r", encoding="utf-8") as fh:
            self.assertIn("MODE = 'off'", fh.read())
        event = ('{"hook_event_name":"PostToolUse","session_id":"s","tool_use_id":"t1",'
                 '"tool_name":"Bash","tool_input":{},"tool_response":{}}')
        state = tempfile.mkdtemp()
        done = subprocess.run([sys.executable, hook], input=event.encode(), capture_output=True,
                              env=dict(os.environ, CRAFT_STATE_DIR=state))
        self.assertEqual(done.returncode, 0, done.stderr.decode())
        self.assertEqual(done.stderr, b"", "молчит и в служебном потоке: это режим, а не поломка")
        self.assertEqual(done.stdout, b"", "выключенный манифестом пакет молчит")
        self.assertFalse(os.path.exists(os.path.join(state, "decisions.s.jsonl")),
                         "и следа не оставляет")
        shutil.rmtree(state, ignore_errors=True)

    def test_неразборные_настройки_не_затираются(self):
        make_module(self.root, "проба")
        settings = os.path.join(self.home, ".claude", "settings.json")
        os.makedirs(os.path.dirname(settings))
        with open(settings, "w", encoding="utf-8") as fh:
            fh.write('{"hooks": {"PreToolUse": [')  # оборванный файл
        with self.assertRaises(ValueError):
            jarvis.register(self.root, jarvis.read_modules(self.root), "claude")
        with open(settings, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), '{"hooks": {"PreToolUse": [', "файл не тронут")
