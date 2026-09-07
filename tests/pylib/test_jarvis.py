"""Кейсы установщика: разбор манифеста, находки `check`, сборка и регистрация.

Все — на ВРЕМЕННОМ дереве: настоящие ~/.claude, список корней и modules/ репы
кейсами не трогаются.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import jarvis  # noqa: E402

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


def make_module(root, name, kind="hook", for_value="general", body=None):
    directory = os.path.join(root, "modules", name)
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write(body if body is not None else MANIFEST % (name, kind, for_value))
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

    def test_пакет_в_манифесте_без_папки(self):
        make_module(self.root, "исчезнет")
        modules = jarvis.read_modules(self.root)
        shutil.rmtree(os.path.join(self.root, "modules", "исчезнет"))
        self.assertTrue(any("папки нет" in line for line in jarvis.check(self.root, modules)))

    def test_два_пакета_с_одним_именем(self):
        make_module(self.root, "первый")
        make_module(self.root, "второй", body=MANIFEST % ("первый", "hook", "general"))
        self.assertTrue(any("два пакета с одним именем" in line for line in self.found()), self.found())

    def test_добавка_к_одной_базе_читается_однозначно(self):
        make_module(self.root, "changeset")
        make_module(self.root, "changeset-review")
        self.assertEqual(self.found(), [], "один кандидат в базы — не находка")

    def test_добавка_сразу_к_нескольким_базам_находка(self):
        # `changeset-review-extra` начинается и с `changeset`, и с
        # `changeset-review`: правило чтения не может сказать, чья это добавка.
        make_module(self.root, "changeset")
        make_module(self.root, "changeset-review")
        make_module(self.root, "changeset-review-extra")
        found = self.found()
        self.assertTrue(any("сразу к нескольким базам" in line for line in found), found)

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
        base = "---\nname: x\nkind: hook\nfor: general\nevents: [{ event: stop }]\n"
        make_module(self.root, "x", body=base + "requires: [tokens]\ndata: []\nmode: on\n---\n")
        self.assertEqual(self.found(), [])

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
        self.assertEqual(self.commands(self.settings(), "PostToolUse"), ["python3 " + hook])
        self.install()
        self.assertEqual(self.commands(self.settings(), "PostToolUse"), ["python3 " + hook])

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
