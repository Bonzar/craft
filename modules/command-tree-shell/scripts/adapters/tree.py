"""Дерево команды оболочки: чистая нормализация вывода `shfmt --to-json`.

normalize(разбор) -> {"statements": [...]}. Ни диска, ни подпроцесса: разбор
приносит adapter.py, здесь только приведение к форме, которую ждут потребители.
Форма и её обещания описаны в шапке SKILL.md пакета.

Инварианты, на которых стоят потребители:
  - утверждения идут ПЛОСКИМ списком слева направо, вложенность — числом `depth`
    (подоболочка, тело цикла, ветка условия, содержимое подстановки);
  - закавыченная строка остаётся ОДНИМ словом, тело `bash -c "…"` в том числе:
    спускаться ли внутрь — политика потребителя;
  - подстановка ПОМЕЧЕНА (`expanded`), а не выдумана: текста она не добавляет,
    зато её содержимое ложится утверждениями глубиной ниже;
  - `line_first` считается среди утверждений СВОЕЙ глубины: иначе подстановка в
    первом слове забирала бы признак у самого утверждения.
"""

# Коды операторов у shfmt (mvdan/sh): числами, потому что числами их и отдаёт
# разбор. Имена нужны только здесь — потребитель сверяет коды.
BINARY_OPS = {10: "&&", 11: "||", 12: "|", 13: "|&"}
HEREDOC_OPS = (61, 62, 63)

# Составные команды: их тело — утверждения ГЛУБИНОЙ НИЖЕ. У каждой перечислены
# поля, в которых это тело лежит; поле бывает списком утверждений, одним
# утверждением или узлом, у которого тело внутри (ветка `else`).
NESTED = {
    "Subshell": ("Stmts",),
    "Block": ("Stmts",),
    "IfClause": ("Cond", "Then", "Else"),
    "WhileClause": ("Cond", "Do"),
    "ForClause": ("Do",),
    "FuncDecl": ("Body",),
    "CaseClause": (),
}


def normalize(parsed):
    out, seen = [], set()
    _stmts((parsed or {}).get("Stmts") or [], 0, out, seen, "")
    return {"statements": out}


def _stmts(stmts, depth, out, seen, sep):
    """Утверждения одного уровня. `sep` — оператор перед ПЕРВЫМ из них."""
    previous = None
    for stmt in stmts:
        if not isinstance(stmt, dict):
            continue
        step = sep if previous is None else _between(previous, stmt)
        previous = _stmt(stmt, depth, out, seen, step)
        sep = ""
    return previous


def _between(previous, stmt):
    """Разделитель между соседями. Перевод строки и `;` — разные вещи для
    потребителя: строка самостоятельна, `;` — звено той же цепочки."""
    if previous.get("background"):
        return "&"
    return "\n" if _line(stmt) > previous.get("line", 0) else ";"


def _line(node):
    return ((node or {}).get("Pos") or {}).get("Line") or 0


def _stmt(stmt, depth, out, seen, sep):
    """Одно утверждение и всё, что оно раскрывает. Возвращает ПОСЛЕДНЮЮ строку
    списка: по ней сосед считает свой разделитель."""
    cmd = stmt.get("Cmd") or {}
    kind = cmd.get("Type") or ""
    background = stmt.get("Background") is True
    redirs = stmt.get("Redirs") or []

    if kind == "BinaryCmd":
        left = _stmt(cmd.get("X") or {}, depth, out, seen, sep)
        right = _stmt(cmd.get("Y") or {}, depth, out, seen, BINARY_OPS.get(cmd.get("Op"), ";"))
        last = right or left
        if last is not None:
            last["redirects"].extend(_redirects(redirs, depth, out, seen))
            last["background"] = last["background"] or background
        return last

    # Обёртка вокруг одного утверждения: глубину не растит — команда исполняется
    # тем же уровнем, и `time cat a` обязан читаться как `cat a`.
    if kind in ("TimeClause", "CoprocClause") and isinstance(cmd.get("Stmt"), dict):
        return _stmt(cmd["Stmt"], depth, out, seen, sep)

    if kind in NESTED:
        for word in _own_words(cmd):
            _word(word, depth, out, seen)   # `for f in $(ls)`, `case $x in`
        last = _stmts(_nested(cmd, kind), depth + 1, out, seen, "")
        if not redirs:
            # Тело пусто — возвращать НЕЧЕГО, но и терять место в цепочке нельзя:
            # сосед считает свой разделитель по предыдущей строке, и пустота
            # отдала бы ему разделитель первого утверждения.
            return last if last is not None else _record(
                kind, depth, _line(stmt), seen, sep, [])
        # Перенаправление стоит на составной команде целиком (`{ …; } > f`), а не
        # на её последнем звене: своя строка, иначе цель приписалась бы чужому.
        record = _record(kind, depth, _line(stmt), seen, sep, [])
        record["background"] = background
        record["redirects"] = _redirects(redirs, depth, out, seen)
        out.append(record)
        return record

    record = _record("call" if kind == "CallExpr" else "other",
                     depth, _line(stmt), seen, sep, _words(cmd, depth, out, seen))
    record["background"] = background
    record["redirects"] = _redirects(redirs, depth, out, seen)
    out.append(record)
    return record


def _nested(cmd, kind):
    """Утверждения тела составной команды, в порядке исполнения."""
    inner = []
    for field in NESTED[kind]:
        value = cmd.get(field)
        if isinstance(value, list):
            inner.extend(value)
        elif isinstance(value, dict):
            # Ветка `else` приходит узлом со своим телом, а `Body` функции —
            # одним утверждением: узел без `Cmd` раскрывается своими полями.
            inner.extend([value] if "Cmd" in value else _nested(value, kind))
    for item in cmd.get("Items") or []:
        inner.extend(item.get("Stmts") or [])
    return inner


def _own_words(cmd):
    """Слова самой составной команды: перечень цикла и предмет разбора случая."""
    words = list(((cmd.get("Loop") or {}).get("Items")) or [])
    if isinstance(cmd.get("Word"), dict):
        words.append(cmd["Word"])
    return words


def _record(kind, depth, line, seen, sep, words):
    """Строка утверждения. `line_first` ставится один раз на пару «глубина и
    строка»: по ней потребитель отличает начало строки от продолжения цепочки."""
    key = (depth, line)
    first = key not in seen
    seen.add(key)
    return {"kind": kind, "depth": depth, "line": line, "line_first": first,
            "sep": sep, "background": False, "words": words, "redirects": []}


def _words(cmd, depth, out, seen):
    """Слова команды: присваивания окружения впереди, затем аргументы.

    Присваивание идёт СЛОВОМ вида `X=1`: разбор держит его отдельным полем, а
    потребитель снимает такой префикс сам и ждёт его среди слов.

    У `[[ … ]]`, арифметики и `let` своих аргументов нет — их выражение здесь не
    разбирается вовсе, и `kind` говорит потребителю, что это не вызов команды."""
    words = []
    if cmd.get("Variant"):
        words.append(_flat(cmd["Variant"].get("Value") or ""))
    for assign in cmd.get("Assigns") or []:
        words.append(_assign(assign, depth, out, seen))
    for arg in cmd.get("Args") or []:
        words.append(_assign(arg, depth, out, seen) if "Name" in arg else _word(arg, depth, out, seen))
    return words


def _assign(assign, depth, out, seen):
    name = (assign.get("Name") or {}).get("Value") or ""
    value = _word(assign.get("Value") or {}, depth, out, seen)
    return {"text": "%s=%s" % (name, value["text"]), "quoted": value["quoted"],
            "expanded": value["expanded"], "process": value["process"]}


def _flat(text):
    return {"text": text, "quoted": False, "expanded": False, "process": False}


def _word(word, depth, out, seen):
    """Слово: собранный текст, закавычено ли оно, есть ли в нём подстановка и
    является ли эта подстановка ПРОЦЕССНОЙ (`<(…)`).

    Процессная отделена от остальных нарочно: она даёт слову путь к каналу, а не
    к файлу, и потребитель, называющий прочитанные файлы, обязан её отличить."""
    text, quoted, expanded, process = "", False, False, False
    for part in word.get("Parts") or []:
        piece, was_quoted, was_expanded, was_process = _part(part, depth, out, seen)
        text += piece
        quoted = quoted or was_quoted
        expanded = expanded or was_expanded
        process = process or was_process
    return {"text": text, "quoted": quoted, "expanded": expanded, "process": process}


def _part(part, depth, out, seen):
    """Кусок слова → (текст, закавычен, подстановка, процессная подстановка).

    Подстановка текста НЕ ДАЁТ: её значение известно только оболочке, и выдумать
    его значит назвать путь, которого нет. Содержимое `$(…)` и `<(…)` при этом не
    теряется — оно ложится в список утверждениями глубиной ниже."""
    kind = part.get("Type") or ""
    if kind == "Lit":
        return (part.get("Value") or "", False, False, False)
    if kind == "SglQuoted":
        return (part.get("Value") or "", True, False, False)
    if kind == "DblQuoted":
        inner = _word(part, depth, out, seen)
        return (inner["text"], True, inner["expanded"], inner["process"])
    if kind in ("CmdSubst", "ProcSubst"):
        _stmts(part.get("Stmts") or [], depth + 1, out, seen, "")
        return ("", False, True, kind == "ProcSubst")
    # ParamExp, ArithmExp, ExtGlob и всё незнакомое: подстановка без текста.
    return ("", False, True, False)


def _redirects(redirs, depth, out, seen):
    """Перенаправления утверждения. Тело heredoc едет ОТДЕЛЬНЫМ полем: командой
    оно не является, и судить его строками нельзя."""
    out_list = []
    for redir in redirs:
        op = redir.get("Op")
        body = None
        if op in HEREDOC_OPS and isinstance(redir.get("Hdoc"), dict):
            body = _word(redir["Hdoc"], depth, out, seen)["text"]
        out_list.append({"op": op,
                         "target": _word(redir.get("Word") or {}, depth, out, seen),
                         "heredoc": body})
    return out_list
