#!/usr/bin/env python3
"""jarvis — установщик пакетов слоя. Команды: install | off | on | status | check.

Пакет — папка с манифестом (`SKILL.md`), кодом и данными. Харнес регистрирует по
СТРОКЕ НА МОДУЛЬ И СОБЫТИЕ и запускает пакет напрямую: общего диспетчера у пакетов
нет, а совпавшие строки одного события харнес и так гоняет параллельно (решение 9).

Что делает `install` (идемпотентно, повторный запуск = no-op):
  1. читает манифесты пакетов СВОЕГО дерева;
  2. кладёт рядом манифест источника `modules.index.json` — по нему модуль на
     событии узнаёт, что в этом корне лежит;
  3. дописывает корень дерева в список корней `~/.local/share/jarvis/sources.list`;
  4. генерирует `dist/<харнес>/hook.py` на каждый модуль и харнес по шаблону
     `runtime/wrapper.py.tmpl`, копируя рядом таблицу харнеса и pylib;
  5. пишет строку регистрации на модуль и событие в `~/.claude/settings.json`.

ЦЕНТРАЛЬНОГО ИНДЕКСА ПАКЕТОВ он не пишет (решение 12): он был бы привилегией
одного дерева, не видел бы командного пресета и не знал бы дерева коллеги. Список
корней содержит только пути и про содержимое ничего не утверждает, поэтому
разъехаться не может; что лежит в корне, читается на событии.

Установщик на Python, потому что он один и должен пережить переход слоя с Node.
"""

import argparse
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Таблиц харнесов будет три (claude, codex, aisuite). Пока одна: генерировать
# обёртку для харнеса, таблицы которого нет, нечем.
HARNESSES = ("claude",)
MANIFEST_FIELDS = ("name", "kind", "for", "events", "requires", "data", "mode")
KINDS = ("hook", "adapter", "skill", "agent", "data")
# Факты канонического события (.claude/hooks/lib/event.js): их даёт обёртка, и
# пакетом они не закрываются. Всё прочее в `requires` — имя пакета.
FACTS = ("journal", "tokens")


# --- манифест ------------------------------------------------------------------

def parse_frontmatter(text):
    """Фронтматтер `SKILL.md` в словарь. Своим разбором, а не библиотекой:
    установщик обязан работать на голой стандартной библиотеке.

    Поддержано ровно то, чем манифест и является: `ключ: скаляр` и `ключ: [...]`
    в поточной форме, где элемент — скаляр или `{ключ: значение, ...}`. Всё
    остальное — ошибка с именем ключа, а не тихо пропущенная строка."""
    if not text.startswith("---"):
        raise ValueError("нет фронтматтера")
    end = text.find("\n---", 3)
    if end < 0:
        raise ValueError("фронтматтер не закрыт")
    out = {}
    for line in text[3:end].split("\n"):
        line = line.split("#", 1)[0].strip() if not _in_brackets(line) else line.strip()
        if not line:
            continue
        if ":" not in line:
            raise ValueError("строка без ключа: %s" % line)
        key, value = line.split(":", 1)
        out[key.strip()] = _scalar_or_list(value.strip())
    return out


def _in_brackets(line):
    """Комментарий обрезается только вне скобок: `for: tool:x  # ...` обрезать
    надо, а `[{ event: post-tool }]` трогать нельзя."""
    return "[" in line and "]" in line


def _scalar_or_list(value):
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_item(part) for part in _split_top(inner)]
    return _strip_quotes(value)


def _split_top(text):
    """Разбить по запятым верхнего уровня: вложенные `{...}` не режутся."""
    out, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(text[start:i])
            start = i + 1
    out.append(text[start:])
    return [part.strip() for part in out if part.strip()]


def _item(part):
    if part.startswith("{") and part.endswith("}"):
        out = {}
        for pair in _split_top(part[1:-1]):
            if ":" not in pair:
                raise ValueError("элемент без ключа: %s" % pair)
            key, value = pair.split(":", 1)
            out[key.strip()] = _strip_quotes(value.strip())
        return out
    return _strip_quotes(part)


def _strip_quotes(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def event_names(manifest):
    """Канонические имена событий модуля. Элемент бывает картой (`{event: x}`) и
    голым именем — обе формы значат одно."""
    out = []
    for item in manifest.get("events") or []:
        name = item.get("event") if isinstance(item, dict) else item
        if name:
            out.append(name)
    return out


def read_modules(root):
    """Манифесты пакетов дерева. Имя папки и есть имя модуля (решение 6), и
    расхождение с полем `name` — находка `check`, а не повод его молча починить."""
    base = os.path.join(root, "modules")
    out = []
    for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        path = os.path.join(base, name, "SKILL.md")
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as fh:
            manifest = parse_frontmatter(fh.read())
        manifest["dir"] = name
        out.append(manifest)
    return out


# --- проверка ------------------------------------------------------------------

def _tail_matches_for(name, for_value):
    """Имя обязано соответствовать `for` (решение 18). Правило механическое:
    `for: tool:git` и `for: harness:codex` требуют хвоста `-git` / `-codex`;
    `for: general` требует, чтобы такого хвоста НЕ было."""
    if for_value.startswith("tool:") or for_value.startswith("harness:"):
        return name.endswith("-" + for_value.split(":", 1)[1])
    return True


def check(root, modules, known=None):
    """Находки установщика. Пустой список — дерево согласовано.

    Две находки решения 18 читаются здесь ОПЕРАЦИОННО, и вот почему. По одному
    имени база и добавка неразличимы: `changeset-review` — это и «добавка review
    к базе changeset», и самостоятельная база с дефисом в имени. Поэтому:

    «имя базы — начало имени другой базы» проверяется как НЕОДНОЗНАЧНОЕ ЧТЕНИЕ:
    находка, когда именем пакета начинаются ДВА разных существующих имени
    (`changeset`, `changeset-review`, `changeset-review-extra`) — тогда правило
    чтения не может сказать, чья это добавка. Один кандидат читается однозначно и
    находкой не является.

    «добавка без базы» проверяется по НЕЗАКРЫТОЙ ЖЁСТКОЙ ЗАВИСИМОСТИ: добавка
    называет свою базу в `requires`, и пропавшая база видна там по имени. Имя,
    которого нет ни среди фактов события, ни среди пакетов известных корней, —
    находка (решение 6: установщик отказывается ставить модуль с незакрытой
    жёсткой зависимостью)."""
    found = []
    names = [m.get("name", "") for m in modules]
    everywhere = set(names) | set(known or [])
    for manifest in modules:
        name = manifest.get("name", "")
        directory = manifest.get("dir", "")
        for field in MANIFEST_FIELDS:
            if field not in manifest:
                found.append("%s: в манифесте нет поля %s" % (directory, field))
        if name != directory:
            found.append("%s: имя в манифесте (%s) не совпадает с именем папки" % (directory, name))
        if manifest.get("kind") not in KINDS:
            found.append("%s: вид «%s» не из списка %s" % (name, manifest.get("kind"), ", ".join(KINDS)))
        if not _tail_matches_for(name, manifest.get("for", "")):
            found.append("%s: имя не соответствует for: %s" % (name, manifest.get("for")))
        if not os.path.isdir(os.path.join(root, "modules", directory)):
            found.append("%s: пакет в манифесте, а папки нет" % name)
        if names.count(name) > 1:
            found.append("%s: два пакета с одним именем" % name)
        bases = sorted(other for other in everywhere
                       if other != name and name.startswith(other + "-"))
        if len(bases) > 1:
            found.append("%s: имя читается как добавка сразу к нескольким базам: %s"
                         % (name, ", ".join(bases)))
        for need in manifest.get("requires") or []:
            if need not in FACTS and need not in everywhere:
                found.append("%s: жёсткая зависимость «%s» ничем не закрыта" % (name, need))
    return sorted(set(found))


# --- сборка --------------------------------------------------------------------

def build(root, manifest, harness):
    """Собрать `dist/<харнес>/`: обёртка по шаблону, таблица харнеса и копия
    pylib рядом с ней. Копия, а не ссылка: пакет должен уезжать целиком."""
    package = os.path.join(root, "modules", manifest["dir"])
    dist = os.path.join(package, "dist", harness)
    os.makedirs(dist, exist_ok=True)
    shutil.copyfile(os.path.join(root, "runtime", "harness", "%s.py" % harness),
                    os.path.join(dist, "harness.py"))
    pylib = os.path.join(dist, "pylib")
    shutil.rmtree(pylib, ignore_errors=True)
    shutil.copytree(os.path.join(root, "runtime", "pylib"), pylib,
                    ignore=shutil.ignore_patterns("__pycache__"))
    with open(os.path.join(root, "runtime", "wrapper.py.tmpl"), "r", encoding="utf-8") as fh:
        template = fh.read()
    body = (template
            .replace("{{MODULE}}", manifest["name"])
            .replace("{{HARNESS}}", harness)
            .replace("{{EVENTS}}", repr(event_names(manifest)))
            .replace("{{REQUIRES}}", repr(list(manifest.get("requires") or [])))
            .replace("{{DATA}}", repr(list(manifest.get("data") or []))))
    hook = os.path.join(dist, "hook.py")
    with open(hook, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(hook, 0o755)
    return hook


# --- источник ------------------------------------------------------------------

def index_path(root):
    return os.path.join(root, "modules.index.json")


def write_index(root, modules):
    """Манифест источника: что в этом корне лежит. Состояния и маркеров в нём
    нет (решение 12) — только то, что объявили манифесты."""
    index = {
        "root": root,
        "modules": [
            {field: manifest.get(field) for field in MANIFEST_FIELDS}
            for manifest in modules
        ],
    }
    body = json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _write_if_changed(index_path(root), body)


def sources_list():
    share = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(share, "jarvis", "sources.list")


def add_source(root):
    """Дописать свой корень в список корней. Список содержит ТОЛЬКО пути и про
    содержимое ничего не утверждает, поэтому разъехаться не может."""
    path = sources_list()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    roots = read_sources()
    if root in roots:
        return False
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(root + "\n")
    return True


def read_sources():
    try:
        with open(sources_list(), "r", encoding="utf-8") as fh:
            return [line.strip() for line in fh if line.strip()]
    except OSError:
        return []


# --- регистрация в харнесе -----------------------------------------------------

def settings_path():
    return os.path.join(os.path.expanduser("~"), ".claude", "settings.json")


def _harness_event(harness, name):
    sys.path.insert(0, os.path.join(ROOT, "runtime", "harness"))
    sys.path.insert(0, os.path.join(ROOT, "runtime", "pylib"))
    table = __import__(harness)
    return table.harness_event(name)


def register(root, modules, harness="claude"):
    """Строка на модуль и событие в настройках харнеса. Чужие записи не
    трогаются; свои устаревшие — снимаются, иначе снятый из манифеста модуль
    продолжал бы запускаться."""
    path = settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            settings = json.load(fh)
    except (OSError, ValueError):
        settings = {}
    if not isinstance(settings, dict):
        settings = {}

    wanted = {}
    for manifest in modules:
        command = "python3 %s" % os.path.join(
            root, "modules", manifest["dir"], "dist", harness, "hook.py")
        for name in event_names(manifest):
            event = _harness_event(harness, name)
            if event:
                wanted.setdefault(event, set()).add(command)

    mine = os.path.join(root, "modules")
    hooks = settings.setdefault("hooks", {})
    for event in list(hooks) + list(wanted):
        groups = hooks.get(event) or []
        keep = []
        for group in groups:
            entries = [
                entry for entry in (group.get("hooks") or [])
                if not _is_ours(entry.get("command", ""), mine, harness)
                or entry.get("command", "") in wanted.get(event, set())
            ]
            if entries:
                keep.append(dict(group, hooks=entries))
        present = {entry.get("command", "") for group in keep for entry in group.get("hooks") or []}
        missing = sorted(wanted.get(event, set()) - present)
        if missing:
            keep.append({"hooks": [{"type": "command", "command": cmd} for cmd in missing]})
        if keep:
            hooks[event] = keep
        else:
            hooks.pop(event, None)

    body = json.dumps(settings, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return _write_if_changed(path, body)


def _is_ours(command, modules_dir, harness):
    return command.startswith("python3 ") and modules_dir in command and (
        os.sep.join(("dist", harness, "hook.py")) in command)


def _write_if_changed(path, body):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            if fh.read() == body:
                return False
    except OSError:
        pass
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return True


# --- mode ----------------------------------------------------------------------

def user_modes_file():
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(config, "jarvis", "modules.toml")


def mode_of(root, name):
    """Режим модуля и ОТКУДА он взят — те же три источника и то же старшинство,
    что читает обёртка на каждом событии."""
    listed = [part.strip() for part in os.environ.get("JARVIS_MODULES_OFF", "").split(",") if part.strip()]
    if listed:
        off = "all" in listed or "*" in listed or name in listed
        return ("off" if off else "on", "JARVIS_MODULES_OFF")
    import tomllib
    for path in (user_modes_file(), os.path.join(root, "personal", "modules.toml")):
        try:
            with open(path, "rb") as fh:
                table = tomllib.load(fh).get("modules") or {}
        except (OSError, ValueError):
            continue
        if name in table:
            value = str(table[name]).lower()
            return ("off" if value in ("off", "false", "0") else "on", path)
    return ("on", "манифест")


def set_mode(name, value):
    """Правка личного конфига: старший из двух файловых источников."""
    path = user_modes_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines, seen = [], False
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    except OSError:
        lines = ["[modules]"]
    if "[modules]" not in lines:
        lines.insert(0, "[modules]")
    # Ключ В КАВЫЧКАХ: голым TOML берёт только ASCII, а имена пакетов бывают
    # какими угодно — незакавыченное имя молча не прочиталось бы, и `off` не
    # сработал бы вовсе.
    entry = '"%s" = "%s"' % (name, value)
    for i, line in enumerate(lines):
        if line.strip().startswith('"%s"' % name) or line.strip().startswith(name + " "):
            lines[i] = entry
            seen = True
    if not seen:
        lines.insert(lines.index("[modules]") + 1, entry)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(line for line in lines if line.strip() != "") + "\n")


# --- команды -------------------------------------------------------------------

def cmd_install(args):
    modules = read_modules(args.root)
    found = check(args.root, modules)
    if found:
        for line in found:
            sys.stderr.write("check: %s\n" % line)
        # Ставить дерево с находками нельзя: незакрытая жёсткая зависимость или
        # два пакета с одним именем — это не предупреждение, а неработающая
        # установка.
        return 1
    write_index(args.root, modules)
    added = add_source(args.root)
    for manifest in modules:
        for harness in HARNESSES:
            build(args.root, manifest, harness)
    changed = register(args.root, modules)
    print("modules: %d" % len(modules))
    print("sources: %s" % ("корень дописан" if added else "корень уже в списке"))
    print("settings: %s" % ("регистрации обновлены" if changed else "no changes needed"))
    return 0


def cmd_status(args):
    for manifest in read_modules(args.root):
        mode, source = mode_of(args.root, manifest.get("name", ""))
        print("%-24s %-4s %s" % (manifest.get("name", ""), mode, source))
    return 0


def cmd_check(args):
    found = check(args.root, read_modules(args.root))
    for line in found:
        print(line)
    return 1 if found else 0


def cmd_off(args):
    set_mode(args.module, "off")
    return 0


def cmd_on(args):
    set_mode(args.module, "on")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="jarvis")
    parser.add_argument("--root", default=ROOT, help="корень дерева пакетов")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("install").set_defaults(run=cmd_install)
    sub.add_parser("status").set_defaults(run=cmd_status)
    sub.add_parser("check").set_defaults(run=cmd_check)
    for name, run in (("off", cmd_off), ("on", cmd_on)):
        one = sub.add_parser(name)
        one.add_argument("module")
        one.set_defaults(run=run)
    args = parser.parse_args(argv)
    args.root = os.path.abspath(args.root)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
