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
import shlex
import shutil
import sys
import tempfile
import time

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
        line = _strip_comment(line)
        if not line:
            continue
        if ":" not in line:
            raise ValueError("строка без ключа: %s" % line)
        key, value = line.split(":", 1)
        out[key.strip()] = _scalar_or_list(key.strip(), value.strip())
    return out


def _strip_comment(line):
    """Хвостовой комментарий — по ГЛУБИНЕ скобок, а не по их наличию в строке:
    `events: [{ event: post-tool }]  # проба` иначе оставался бы с хвостом, список
    читался бы незакрытым, и ошибка называла бы не ту причину."""
    depth = 0
    for i, ch in enumerate(line):
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "#" and depth <= 0:
            return line[:i].strip()
    return line.strip()


def _scalar_or_list(key, value):
    if value.startswith("[") and not value.endswith("]"):
        raise ValueError("список не закрыт у ключа %s: %s" % (key, value))
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
    расхождение с полем `name` — находка `check`, а не повод его молча починить.

    Неразобранный манифест не роняет обход трейсбеком: он возвращается ПУСТЫМ
    манифестом с причиной, и `check` называет пакет по имени папки — ради этого
    он и заведён."""
    base = os.path.join(root, "modules")
    out = []
    for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        path = os.path.join(base, name, "SKILL.md")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                manifest = parse_frontmatter(fh.read())
        except (OSError, ValueError) as bad:
            manifest = {"broken": str(bad)}
        manifest["dir"] = name
        out.append(manifest)
    return out


# --- проверка ------------------------------------------------------------------

def _for_mismatch(name, for_value):
    """Расхождение имени с `for` (решение 18), словами; пусто — сошлось.

    Правило механическое: `for: tool:git` и `for: harness:codex` требуют хвоста
    `-git` / `-codex`. У `for: general` проверяется ОДНО — что хвост не есть имя
    ХАРНЕСА: их список известен (`HARNESSES`), и `scope-claude` с `for: general`
    читался бы адаптером, которым не является. Имена инструментов не известны
    никому, поэтому хвост `-git` у `for: general` тут не ловится."""
    if for_value.startswith("tool:") or for_value.startswith("harness:"):
        want = for_value.split(":", 1)[1]
        if not name.endswith("-" + want):
            return "имя не соответствует for: %s (ждали хвост «-%s»)" % (for_value, want)
        return ""
    tail = name.rsplit("-", 1)[-1] if "-" in name else ""
    if tail in HARNESSES:
        return "хвост «-%s» — имя харнеса, а for: %s" % (tail, for_value or "не задан")
    return ""


def check(root, modules, known=None, index=True):
    """Находки установщика. Пустой список — дерево согласовано.

    Две находки решения 18 держатся на ОДНОМ правиле: имя, начинающееся с имени
    другого пакета, читается ДОБАВКОЙ, и добавка обязана назвать свою базу в
    `requires`. Отсюда обе:

    «имя базы — начало имени другой базы» — находка, когда пакет начинается с
    имени существующего пакета, а базы в `requires` не назвал: значит он себя
    добавкой не считает, и два имени столкнулись. Либо назови базу, либо
    переименуйся.

    «добавка без базы» — находка о НЕЗАКРЫТОЙ ЖЁСТКОЙ ЗАВИСИМОСТИ: база названа,
    а её нет ни в этом дереве, ни в известных корнях (решение 6: установщик
    отказывается ставить модуль с незакрытой жёсткой зависимостью).

    Объявление и снимает неоднозначность, которой иначе не снять: по одному имени
    `changeset-review` — это и добавка к `changeset`, и самостоятельная база с
    дефисом, и различить их может только сам пакет."""
    found = []
    names = [m.get("name", "") for m in modules]
    everywhere = set(names) | set(known or [])
    # Словарь событий и фактов — ОБЪЕДИНЕНИЕ всех харнесов: модуль под codex не
    # обязан укладываться в словарь claude, а вторая таблица должна добавляться
    # строкой в HARNESSES, а не правкой этой проверки.
    supported, facts = set(), set()
    for one in HARNESSES:
        table = harness_table(one)
        supported |= set(table.events())
        facts |= set(table.FACTS)
    # Пакет, стоящий в манифесте ИСТОЧНИКА, но исчезнувший из дерева: манифест
    # пишет установка, а папку сносят руками, и между двумя установками модуль
    # значится поставленным, не существуя. УСТАНОВКЕ это не находка (index=False):
    # она манифест источника переписывает, то есть ровно это и чинит.
    if index:
        for gone in sorted(set(indexed_names(root)) - set(names)):
            found.append("%s: пакет в манифесте источника, а папки нет" % gone)
    for manifest in modules:
        if manifest.get("broken"):
            found.append("%s: манифест не разобран: %s"
                         % (manifest.get("dir", ""), manifest["broken"]))
            continue
        found.extend(_check_name(manifest, names, everywhere))
        found.extend(_check_surface(manifest, everywhere, supported, facts))
        found.extend(_check_code(root, manifest))
    return sorted(set(found))


def _check_name(manifest, names, everywhere):
    """Находки об ИМЕНИ: состав манифеста, имя против папки, вид, `for`, коллизии
    имён (решение 18)."""
    name = manifest.get("name", "")
    directory = manifest.get("dir", "")
    found = []
    for field in MANIFEST_FIELDS:
        if field not in manifest:
            found.append("%s: в манифесте нет поля %s" % (directory, field))
    if name != directory:
        found.append("%s: имя в манифесте (%s) не совпадает с именем папки" % (directory, name))
    if manifest.get("kind") not in KINDS:
        found.append("%s: вид «%s» не из списка %s" % (name, manifest.get("kind"), ", ".join(KINDS)))
    mismatch = _for_mismatch(name, manifest.get("for", ""))
    if mismatch:
        found.append("%s: %s" % (name, mismatch))
    if names.count(name) > 1:
        found.append("%s: два пакета с одним именем" % name)
    # Имя, начинающееся с имени другого пакета, читается ДОБАВКОЙ. Чтобы чтение
    # было не догадкой, добавка называет свою базу в `requires`: не назвала — имя
    # базы оказалось началом имени пакета, который добавкой себя не объявлял.
    bases = sorted(other for other in everywhere
                   if other != name and name.startswith(other + "-"))
    declared = [b for b in bases if b in (manifest.get("requires") or [])]
    if len(declared) > 1:
        found.append("%s: добавка объявила сразу несколько баз: %s" % (name, ", ".join(declared)))
    elif bases and not declared:
        found.append("%s: имя начинается с имени пакета %s, а базы в requires нет:"
                     " добавке базу надо назвать, самостоятельному пакету — переименоваться"
                     % (name, ", ".join(bases)))
    return found


def _check_surface(manifest, everywhere, supported, facts):
    """Находки о ПОВЕРХНОСТИ: события, на которые модуль подписан, и жёсткие
    зависимости, которыми он закрыт."""
    name = manifest.get("name", "")
    found = []
    for event in event_names(manifest):
        if event not in supported:
            found.append("%s: событие «%s» харнесу неизвестно; канонические имена: %s"
                         % (name, event, ", ".join(sorted(supported))))
    for need in manifest.get("requires") or []:
        if need in FACTS:
            # Факт события закрывает ОБЁРТКА, а не пакет. Не выдаёт его ни одна
            # таблица — модуль встанет и будет отвечать `unsupported` на каждом
            # событии; такое лучше не ставить вовсе (решение 6).
            if need not in facts:
                found.append("%s: факт «%s» не выдаёт ни одна таблица харнеса" % (name, need))
        elif need not in everywhere:
            found.append("%s: жёсткая зависимость «%s» ничем не закрыта" % (name, need))
    return found


def _check_code(root, manifest):
    """Находка о КОДЕ: у всего, что объявило СОБЫТИЯ, должна быть чистая функция
    решения. Спрашивается по событиям, а не по виду: регистрацию и обёртку
    получает каждый, кто объявил события, каким бы `kind` он себя ни назвал, — и
    без `decide.py` падает на импорте на каждом из них."""
    if not event_names(manifest):
        return []
    decide = os.path.join(root, "modules", manifest.get("dir", ""), "scripts", "hooks", "decide.py")
    if os.path.isfile(decide):
        return []
    return ["%s: вид hook, а scripts/hooks/decide.py нет" % manifest.get("name", "")]


# --- сборка --------------------------------------------------------------------

def build(root, manifest, harness):
    """Собрать `dist/<харнес>/`: обёртка по шаблону, таблица харнеса и копия
    pylib рядом с ней. Копия, а не ссылка: пакет должен уезжать целиком."""
    package = os.path.join(root, "modules", manifest["dir"])
    dist = os.path.join(package, "dist", harness)
    os.makedirs(dist, exist_ok=True)
    # Шаблон, таблицы и pylib берутся ИЗ САМОГО УСТАНОВЩИКА (ROOT), а не из
    # ставимого дерева: в дереве пакетов их нет и быть не должно — оно несёт
    # модули, а рантайм приезжает с тем, кто ставит.
    shutil.copyfile(os.path.join(ROOT, "runtime", "harness", "%s.py" % harness),
                    os.path.join(dist, "harness.py"))
    pylib = os.path.join(dist, "pylib")
    shutil.rmtree(pylib, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "runtime", "pylib"), pylib,
                    ignore=shutil.ignore_patterns("__pycache__"))
    with open(os.path.join(ROOT, "runtime", "wrapper.py.tmpl"), "r", encoding="utf-8") as fh:
        template = fh.read()
    body = (template
            .replace("{{MODULE}}", repr(manifest["name"]))
            .replace("{{HARNESS}}", harness)
            .replace("{{EVENTS}}", repr(event_names(manifest)))
            # В обёртку едут только ФАКТЫ события. Имена возможностей из того же
            # списка закрывает установка (находка «жёсткая зависимость ничем не
            # закрыта»), а событию их задать нельзя: такого поля в нём нет.
            .replace("{{FACTS}}", repr([r for r in (manifest.get("requires") or []) if r in FACTS]))
            .replace("{{DATA}}", repr(list(manifest.get("data") or [])))
            # Режим из манифеста — ПОСЛЕДНИЙ источник `mode`. Без него пакет,
            # объявленный выключенным, работал бы на каждом событии.
            .replace("{{MODE}}", repr(str(manifest.get("mode") or "on"))))
    hook = os.path.join(dist, "hook.py")
    with open(hook, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(hook, 0o755)
    return hook


# --- источник ------------------------------------------------------------------

def index_path(root):
    return os.path.join(root, "modules.index.json")


def indexed_names(root):
    """Имена пакетов из манифеста источника — картина ПРОШЛОЙ установки."""
    try:
        with open(index_path(root), "r", encoding="utf-8") as fh:
            index = json.load(fh)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as bad:
        # Пустой список тут значил бы «в корне нет пакетов», и добавка, чью базу
        # этот корень закрывал, встала бы с неверной причиной.
        sys.stderr.write("манифест источника %s не читается: %s\n" % (index_path(root), bad))
        return []
    return [m.get("name", "") for m in (index.get("modules") or []) if isinstance(m, dict)]


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
    # Перевод строки ВПЕРЕДИ, если файла не кончается им: список, поправленный
    # руками без хвостового перевода, склеил бы два корня в одну строку, и оба
    # перестали бы читаться.
    lead = ""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            body = fh.read()
        if body and not body.endswith("\n"):
            lead = "\n"
    except OSError:
        pass
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(lead + root + "\n")
    return True


def known_names(root):
    """Имена пакетов ДРУГИХ известных корней — по манифесту источника каждого.

    Зависимость закрывает пакет из любого корня, а не только из своего: командный
    пресет и дерево коллеги — такие же источники (решение 12). Смотри только своё
    дерево — межкорневая зависимость читалась бы незакрытой, и установка отказывала
    бы ровно там, где всё на месте."""
    out = []
    for other in read_sources():
        if os.path.abspath(other) == os.path.abspath(root):
            continue
        out.extend(indexed_names(other))
    return out


def read_sources():
    try:
        with open(sources_list(), "r", encoding="utf-8") as fh:
            return [line.strip() for line in fh if line.strip()]
    except OSError:
        return []


# --- регистрация в харнесе -----------------------------------------------------

# Путь к рантайму правится ОДИН РАЗ: harness_table зовётся в цикле по харнесам и
# из двух мест, и вставка на каждый вызов растила бы sys.path дублями.
for _runtime in ("pylib", "harness"):
    _path = os.path.join(ROOT, "runtime", _runtime)
    if _path not in sys.path:
        sys.path.insert(0, _path)


import state  # noqa: E402  (после правки sys.path выше)


def harness_table(harness):
    """Таблица харнеса: имена его событий, путь его настроек, форма записи в них.
    Единственное место, откуда установщик знает про харнес."""
    return __import__(harness)


def register(root, modules, harness):
    """Строка на модуль и событие в настройках харнеса. Чужие записи не
    трогаются; свои устаревшие — снимаются, иначе снятый из манифеста модуль
    продолжал бы запускаться."""
    table = harness_table(harness)
    path = table.settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            settings = json.load(fh)
    except FileNotFoundError:
        # Файла ЕЩЁ НЕТ — законная пустота: первая установка его и заводит.
        settings = {}
    except OSError as bad:
        # А вот существующий, но нечитаемый (права, чужой владелец, сбой чтения)
        # пустым считать нельзя: мы его перезапишем, и чужие регистрации с
        # разрешениями исчезнут — причём копию положить тоже не выйдет.
        raise ValueError("%s не читается (%s); установка остановлена, файл не тронут" % (path, bad))
    except ValueError as bad:
        # А вот НЕРАЗБОРНЫЙ файл пустым считать нельзя: мы его перезапишем, и с
        # ним исчезнут чужие регистрации и разрешения. Установка встаёт.
        raise ValueError("%s не разбирается (%s); установка остановлена, файл не тронут" % (path, bad))
    if not isinstance(settings, dict):
        raise ValueError("%s не объект; установка остановлена, файл не тронут" % path)
    if "hooks" in settings and not isinstance(settings["hooks"], dict):
        raise ValueError("%s: поле hooks не объект; установка остановлена, файл не тронут" % path)

    wanted = {}
    for manifest in modules:
        # Путь ЭКРАНИРУЕТСЯ: харнес исполняет строку регистрации оболочкой, и
        # чекаут по пути с пробелом дал бы неработающую строку — молча.
        hook = os.path.join(root, "modules", manifest["dir"], "dist", harness, "hook.py")
        command = "python3 %s" % shlex.quote(hook)
        for name in event_names(manifest):
            event = table.harness_event(name)
            # Имя, которого этот харнес не знает, до сюда не доходит: его ловит
            # `check`, и установка на находках не идёт.
            if event:
                wanted.setdefault(event, set()).add(command)

    mine = os.path.join(root, "modules")
    hooks = settings.setdefault("hooks", {})
    for event in list(hooks) + list(wanted):
        groups = hooks.get(event) or []
        keep = []
        for group in groups:
            if not isinstance(group, dict):
                raise ValueError("%s: группа хуков на событии %s не объект;"
                                 " установка остановлена, файл не тронут" % (path, event))
            entries = [
                entry for entry in (group.get("hooks") or [])
                # Сверка ПО ЭТОМУ событию, а не по объединению всех: команда,
                # оставшаяся нужной на другом событии, иначе переживала бы чистку
                # везде — и модуль, убравший событие из манифеста, продолжал бы
                # запускаться на нём до конца жизни настроек.
                if not _is_ours(entry.get("command", ""), mine, harness)
                or entry.get("command", "") in wanted.get(event, set())
            ]
            if entries:
                keep.append(dict(group, hooks=entries))
        present = {entry.get("command", "") for group in keep for entry in group.get("hooks") or []}
        missing = sorted(wanted.get(event, set()) - present)
        if missing:
            keep.append({"hooks": [table.registration(cmd) for cmd in missing]})
        if keep:
            hooks[event] = keep
        else:
            hooks.pop(event, None)

    body = json.dumps(settings, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return _write_if_changed(path, body, backup=True)


def _is_ours(command, modules_dir, harness):
    """Наша ли это строка регистрации. Разбирается КОМАНДА, а не подстрока: путь
    экранирован, и чужая строка, где наш каталог упомянут аргументом, нашей не
    является."""
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if len(parts) != 2 or os.path.basename(parts[0]) != "python3":
        return False
    return parts[1].startswith(modules_dir + os.sep) and parts[1].endswith(
        os.sep.join(("dist", harness, "hook.py")))


def _write_if_changed(path, body, backup=False):
    """Записать, если содержимое изменилось. Возвращает, была ли запись.

    Запись АТОМАРНАЯ: во временный файл рядом и переименованием поверх. В
    настройках харнеса лежат чужие регистрации и разрешения, и сорванная запись
    оставила бы вместо них пустой файл. `backup` кладёт рядом копию — ровно как
    install.sh делает перед своей правкой того же файла."""
    before = None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            before = fh.read()
    except FileNotFoundError:
        pass
    except OSError as bad:
        # Существующий файл, который не читается, не перезаписывается: под ним
        # чужое, и копию положить тоже нечем.
        raise ValueError("%s не читается (%s); файл не тронут" % (path, bad))
    if before == body:
        return False
    if backup and before is not None:
        stamp = time.strftime("%Y%m%d%H%M%S")
        try:
            with open("%s.bak.%s" % (path, stamp), "w", encoding="utf-8") as fh:
                fh.write(before)
        except OSError as bad:
            raise ValueError("копия %s не легла (%s); файл не тронут" % (path, bad))
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.replace(tmp, path)
    return True


# --- mode ----------------------------------------------------------------------

def user_modes_file():
    """Личный конфиг режимов. Формула общая с обёрткой (pylib/state)."""
    return state.modes_file()


def mode_of(root, name, manifest_mode="on"):
    """Режим модуля и откуда он взят. Спрашивается у ТОГО ЖЕ кода, который читает
    обёртка на каждом событии: `status`, отвечающий по своей копии старшинства,
    рано или поздно начал бы врать."""
    return state.mode(name, root, manifest_mode)


def set_mode(name, value):
    """Правка личного конфига: старший из двух файловых источников.

    Правится ТОЛЬКО секция `[modules]`. Файл чужой: в нём бывают другие секции, и
    одноимённый ключ в любой из них — не про нас. Пустые строки не выбрасываются:
    у чужого файла они разделяют блоки."""
    path = user_modes_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    except OSError:
        lines = []

    # Ключ В КАВЫЧКАХ: голым TOML берёт только ASCII, а имена пакетов бывают
    # какими угодно — незакавыченное имя молча не прочиталось бы, и `off` не
    # сработал бы вовсе.
    entry = '"%s" = "%s"' % (name, value)
    start = _section_start(lines, "modules")
    if start is None:
        if lines and lines[-1].strip() != "":
            lines.append("")
        lines.append("[modules]")
        lines.append(entry)
    else:
        end = _section_end(lines, start)
        for i in range(start + 1, end):
            if _key_of(lines[i]) == name:
                lines[i] = entry
                break
        else:
            lines.insert(end, entry)

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines).rstrip("\n") + "\n")


def _heading(line):
    """Имя секции TOML в строке, иначе пустота. Хвостовой комментарий и пробелы
    не в счёт: по точному совпадению строки заголовок `[modules] # моё` не нашёлся
    бы, и в файл лёг бы ВТОРОЙ `[modules]` — после чего он перестаёт разбираться."""
    text = line.split("#", 1)[0].strip()
    return text[1:-1].strip() if text.startswith("[") and text.endswith("]") else ""


def _section_start(lines, name):
    for i, line in enumerate(lines):
        if _heading(line) == name:
            return i
    return None


def _section_end(lines, start):
    """Первая строка ПОСЛЕ секции: следующий заголовок либо конец файла."""
    for i in range(start + 1, len(lines)):
        if _heading(lines[i]):
            return i
    return len(lines)


def _key_of(line):
    """Имя ключа в строке `ключ = значение`, с кавычками или без."""
    text = line.split("#", 1)[0].strip()
    if "=" not in text:
        return ""
    return _strip_quotes(text.split("=", 1)[0].strip())


# --- команды -------------------------------------------------------------------

def cmd_install(args):
    modules = read_modules(args.root)
    found = check(args.root, modules, known=known_names(args.root), index=False)
    if found:
        for line in found:
            sys.stderr.write("check: %s\n" % line)
        # Ставить дерево с находками нельзя: незакрытая жёсткая зависимость или
        # два пакета с одним именем — это не предупреждение, а неработающая
        # установка.
        return 1
    write_index(args.root, modules)
    added = add_source(args.root)
    changed = False
    for harness in HARNESSES:
        for manifest in modules:
            build(args.root, manifest, harness)
        changed = register(args.root, modules, harness) or changed
    print("modules: %d" % len(modules))
    print("sources: %s" % ("корень дописан" if added else "корень уже в списке"))
    print("settings: %s" % ("регистрации обновлены" if changed else "no changes needed"))
    return 0


def cmd_status(args):
    for manifest in read_modules(args.root):
        mode, source = mode_of(args.root, manifest.get("name", ""), manifest.get("mode"))
        print("%-24s %-4s %s" % (manifest.get("name", ""), mode, source))
    return 0


def cmd_check(args):
    found = check(args.root, read_modules(args.root), known=known_names(args.root))
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
    try:
        return args.run(args)
    except ValueError as bad:
        # Свой отказ печатается СЛОВАМИ: тщательно составленное «файл не тронут»
        # трейсбеком читается как поломка установщика, а не как его решение.
        sys.stderr.write("%s\n" % bad)
        return 1


if __name__ == "__main__":
    sys.exit(main())
