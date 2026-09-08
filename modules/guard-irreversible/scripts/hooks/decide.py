"""Гвард необратимого: чистая функция решения над деревом команды и строками данных.

decide(event, data_files, state_dir) -> решение. Дерево приходит ДАННЫМИ в
событии (возможность `command_tree`, объявлена в `requires`), правила — строками
из `data_files`. Своего разбора команды здесь нет, и имён инструментов тоже: и то
и другое живёт снаружи — разбор в адаптере, имена в данных (решения 16 и 25).

Первая сработавшая строка даёт отказ с её причиной. Ни одна не сработала —
`none`: молчание гварда и есть проход.

Строки читаются с диска на каждом событии: подпись `decide` тем и устроена, что
данные приходят ПУТЯМИ, — а держать их в памяти между вызовами нечем, процесс
живёт одно событие. Формат строки описан в шапке самих файлов данных.
"""

import re
import sys

from decision import deny, none

FIELDS = 6
YES = "да"


def decide(event, data_files, state_dir):
    tree = event.get("command_tree") or {}
    source = tree.get("source") or ""
    statements = tree.get("statements") or []
    for row in _rows(data_files):
        if _fires(row, statements, source):
            return deny(row["reason"])
    return none()


def _fires(row, statements, source):
    """Сработала ли строка. Отмена сильнее совпадения: она — объявленное автором
    команды исключение, и смотреть её надо до, а не после."""
    if row["cancel"] is not None and row["cancel"].search(source):
        return False
    if row["scope"] == "raw":
        return bool(row["pattern"].search(source))
    for statement in statements:
        if row["first"] and not _leads(statement):
            continue
        words = "\n".join(word.get("text") or "" for word in statement.get("words") or [])
        if words and row["pattern"].search(words):
            return True
    return False


def _leads(statement):
    """Первое ли это утверждение КОМАНДЫ: верхний уровень, первая строка, начало
    строки. Не «первое любой строки»: прежний гвард смотрел на начало вызова, и
    переезд обещан с теми же ожиданиями — расширять охват молча нельзя."""
    return (statement.get("depth") == 0 and statement.get("line") == 1
            and statement.get("line_first") is True)


def _rows(paths):
    out = []
    for path in paths or []:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError as bad:
            # Нечитаемые данные — это не «правил нет»: гвард без строк молчит на
            # всём, и молчать об этом было бы вторым отказом поверх первого.
            sys.stderr.write("[guard-irreversible] %s не читается: %s\n" % (path, bad))
            continue
        out.extend(_parse(text, path))
    return out


def _parse(text, path):
    out = []
    for number, line in enumerate(text.split("\n"), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != FIELDS:
            sys.stderr.write("[guard-irreversible] %s:%d: полей %d вместо %d\n"
                             % (path, number, len(parts), FIELDS))
            continue
        name, scope, first, pattern, cancel, reason = parts
        try:
            compiled = re.compile(pattern)
            stop = re.compile(cancel) if cancel else None
        except re.error as bad:
            sys.stderr.write("[guard-irreversible] %s:%d (%s): регулярка не разобрана: %s\n"
                             % (path, number, name, bad))
            continue
        out.append({"name": name, "scope": scope, "first": first.strip() == YES,
                    "pattern": compiled, "cancel": stop, "reason": reason})
    return out
